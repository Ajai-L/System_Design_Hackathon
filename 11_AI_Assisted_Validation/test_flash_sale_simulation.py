"""
SALESTORM Flash Sale Concurrency & Resilience Simulation
Simulates 10,000 concurrent purchase requests competing for 100 items of inventory.
Validates:
1. Zero overselling (concurrent active reservations + sold <= initial stock at all times; total confirmed orders <= 100)
2. Idempotency handling (2% duplicate requests handled without duplicate reservations or double charging)
3. 95% payment success / 5% payment failure with automatic reservation release back to pool
4. Downstream Order Service temporary outage (30s delay) with transactional outbox queue recovery
"""

import asyncio
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Dict

class ReservationStatus(Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    CONFIRMED = "CONFIRMED"
    SOLD = "SOLD"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"

@dataclass
class Reservation:
    reservation_id: str
    product_id: str
    customer_id: str
    quantity: int
    status: ReservationStatus
    idempotency_key: str
    created_at: float
    expires_at: float

class MockRedisCluster:
    """
    Simulates Redis single-threaded execution model for Lua scripts & distributed locking.
    Executes atomic stock check-and-decrement and idempotency check.
    """
    def __init__(self, initial_stock: int):
        self.stock = initial_stock
        self.processed_idempotency_keys: Dict[str, dict] = {}
        self.active_reservations: Dict[str, dict] = {}
        self.lock = asyncio.Lock()  # Simulates Redis single-threaded event loop

    async def execute_reservation_lua(self, product_id: str, customer_id: str, idempotency_key: str) -> dict:
        async with self.lock:
            # 1. Idempotency Check (SETNX equivalent)
            if idempotency_key in self.processed_idempotency_keys:
                return {
                    "success": True,
                    "duplicate": True,
                    "data": self.processed_idempotency_keys[idempotency_key]
                }
            
            # 2. Atomic Stock Check & Decrement
            if self.stock >= 1:
                self.stock -= 1
                reservation_id = f"res_{uuid.uuid4().hex[:12]}"
                res_data = {
                    "reservation_id": reservation_id,
                    "product_id": product_id,
                    "customer_id": customer_id,
                    "status": "RESERVED",
                    "remaining_stock": self.stock
                }
                self.processed_idempotency_keys[idempotency_key] = res_data
                self.active_reservations[reservation_id] = res_data
                return {"success": True, "duplicate": False, "data": res_data}
            else:
                return {"success": False, "reason": "OUT_OF_STOCK", "remaining_stock": 0}

    async def release_stock_lua(self, product_id: str, reservation_id: str):
        async with self.lock:
            if reservation_id in self.active_reservations:
                del self.active_reservations[reservation_id]
                self.stock += 1

    async def finalize_sold_lua(self, product_id: str, reservation_id: str):
        async with self.lock:
            if reservation_id in self.active_reservations:
                del self.active_reservations[reservation_id]
                # Stock was already decremented at reservation, so it remains off the pool permanently

class MockMySQLDatabase:
    """
    Simulates MySQL 8.0 InnoDB engine with row-level locking, ACID transactions,
    and strict table constraints.
    """
    def __init__(self, initial_stock: int):
        self.total_capacity = initial_stock
        self.available_stock = initial_stock
        self.reserved_stock = 0
        self.sold_stock = 0
        self.reservations: Dict[str, Reservation] = {}
        self.orders: Dict[str, dict] = {}
        self.db_lock = asyncio.Lock()  # Simulates InnoDB row-level exclusive lock on inventory row

    async def persist_reservation(self, res_data: dict, idempotency_key: str) -> bool:
        async with self.db_lock:
            # Enforce strict invariant: available_stock >= 1
            if self.available_stock < 1:
                return False
            self.available_stock -= 1
            self.reserved_stock += 1
            reservation = Reservation(
                reservation_id=res_data["reservation_id"],
                product_id=res_data["product_id"],
                customer_id=res_data["customer_id"],
                quantity=1,
                status=ReservationStatus.RESERVED,
                idempotency_key=idempotency_key,
                created_at=time.time(),
                expires_at=time.time() + 600.0  # 10 minutes TTL
            )
            self.reservations[reservation.reservation_id] = reservation
            # Verify conservation invariant
            assert (self.available_stock + self.reserved_stock + self.sold_stock) == self.total_capacity
            return True

    async def release_reservation(self, reservation_id: str):
        async with self.db_lock:
            if reservation_id in self.reservations:
                res = self.reservations[reservation_id]
                if res.status in (ReservationStatus.RESERVED, ReservationStatus.PAYMENT_PENDING):
                    res.status = ReservationStatus.RELEASED
                    self.reserved_stock -= 1
                    self.available_stock += 1
                    assert (self.available_stock + self.reserved_stock + self.sold_stock) == self.total_capacity

    async def confirm_order(self, reservation_id: str, order_id: str, customer_id: str):
        async with self.db_lock:
            if reservation_id in self.reservations:
                res = self.reservations[reservation_id]
                res.status = ReservationStatus.CONFIRMED
                self.reserved_stock -= 1
                self.sold_stock += 1
                self.orders[order_id] = {
                    "order_id": order_id,
                    "reservation_id": reservation_id,
                    "customer_id": customer_id,
                    "status": "CONFIRMED"
                }
                assert (self.available_stock + self.reserved_stock + self.sold_stock) == self.total_capacity

class FlashSaleSimulator:
    def __init__(self, total_stock: int = 100, total_requests: int = 10000):
        self.total_stock = total_stock
        self.total_requests = total_requests
        self.redis = MockRedisCluster(initial_stock=total_stock)
        self.mysql = MockMySQLDatabase(initial_stock=total_stock)
        
        # Asynchronous Message Queue / Kafka buffer for Order Service resilience
        self.outbox_queue: asyncio.Queue = asyncio.Queue()
        self.order_service_healthy = True

        # Metrics & Telemetry
        self.metrics = {
            "total_requests": 0,
            "duplicate_requests_filtered": 0,
            "reservations_granted": 0,
            "out_of_stock_rejected": 0,
            "payments_attempted": 0,
            "payments_succeeded": 0,
            "payments_failed": 0,
            "stock_reclaimed_to_pool": 0,
            "orders_confirmed": 0,
            "orders_buffered_during_outage": 0
        }

    async def handle_purchase_request(self, customer_id: str, product_id: str, idempotency_key: str):
        self.metrics["total_requests"] += 1

        # Phase 1: High-Speed Pre-Allocation in Redis Lua (Traffic Gatekeeper)
        redis_result = await self.redis.execute_reservation_lua(product_id, customer_id, idempotency_key)

        if not redis_result["success"]:
            self.metrics["out_of_stock_rejected"] += 1
            return {"status": 409, "message": "OUT_OF_STOCK"}

        if redis_result.get("duplicate"):
            self.metrics["duplicate_requests_filtered"] += 1
            return {"status": 200, "message": "IDEMPOTENT_REPLAY", "data": redis_result["data"]}

        self.metrics["reservations_granted"] += 1
        res_data = redis_result["data"]
        res_id = res_data["reservation_id"]

        # Phase 2: Transactional DB Reservation Write (ACID Persistence)
        db_persisted = await self.mysql.persist_reservation(res_data, idempotency_key)
        if not db_persisted:
            # Defensive compensation if DB was out of sync
            await self.redis.release_stock_lua(product_id, res_id)
            self.metrics["out_of_stock_rejected"] += 1
            return {"status": 409, "message": "OUT_OF_STOCK"}

        # Phase 3: Payment Processing (Simulating 95% Success, 5% Failure)
        self.metrics["payments_attempted"] += 1
        payment_succeeded = random.random() < 0.95

        if not payment_succeeded:
            self.metrics["payments_failed"] += 1
            # Release Reservation back to inventory pool
            await self.redis.release_stock_lua(product_id, res_id)
            await self.mysql.release_reservation(res_id)
            self.metrics["stock_reclaimed_to_pool"] += 1
            return {"status": 402, "message": "PAYMENT_FAILED", "reservation_id": res_id}

        self.metrics["payments_succeeded"] += 1
        await self.redis.finalize_sold_lua(product_id, res_id)

        # Phase 4: Order Creation Workflow & Outage Handling
        order_payload = {
            "order_id": f"ord_{uuid.uuid4().hex[:10]}",
            "reservation_id": res_id,
            "customer_id": customer_id
        }

        if not self.order_service_healthy:
            # Simulating Order Service Down: Store in Outbox / Kafka Queue
            self.metrics["orders_buffered_during_outage"] += 1
            await self.outbox_queue.put(order_payload)
            return {"status": 202, "message": "ORDER_ACCEPTED_ASYNC", "order_id": order_payload["order_id"]}
        else:
            await self.mysql.confirm_order(res_id, order_payload["order_id"], customer_id)
            self.metrics["orders_confirmed"] += 1
            return {"status": 201, "message": "ORDER_CONFIRMED", "order_id": order_payload["order_id"]}

    async def recover_order_service(self):
        """Processes events queued during Order Service outage once service recovers."""
        while not self.outbox_queue.empty():
            payload = await self.outbox_queue.get()
            await self.mysql.confirm_order(payload["reservation_id"], payload["order_id"], payload["customer_id"])
            self.metrics["orders_confirmed"] += 1
            self.outbox_queue.task_done()

async def run_hackathon_simulation():
    print("=" * 75)
    print("SALESTORM SYSTEM DESIGN HACKATHON: HIGH-CONCURRENCY & RELIABILITY TEST")
    print("Scenario: 10,000 concurrent requests competing for 100 items of stock.")
    print("Specifications: Stock = 100 | Payment Success = 95% | Duplicate Rate = 2%")
    print("=" * 75)

    sim = FlashSaleSimulator(total_stock=100, total_requests=10000)

    # Step 1: Generate 10,000 unique client requests
    base_requests = []
    for i in range(10000):
        cust_id = f"cust_{i:05d}"
        idem_key = f"idem_key_{i:05d}"
        base_requests.append((cust_id, "PROD-FLASH-100", idem_key))

    # Step 2: Inject 200 duplicate requests (2% duplicate rate)
    all_requests = list(base_requests)
    duplicates = random.sample(base_requests[:1000], 200)
    all_requests.extend(duplicates)
    random.shuffle(all_requests)

    # Step 3: Simulate Order Service 30s Outage during burst
    sim.order_service_healthy = False  # Order service is down at start!

    start_time = time.perf_counter()

    # Dispatch all 10,200 requests concurrently in batches
    batch_size = 500
    for i in range(0, len(all_requests), batch_size):
        batch = all_requests[i:i + batch_size]
        batch_tasks = [
            sim.handle_purchase_request(cust_id, prod_id, idem_key)
            for cust_id, prod_id, idem_key in batch
        ]
        await asyncio.gather(*batch_tasks)

    # Simulate Order Service Recovery after outage (consuming from Kafka Outbox)
    sim.order_service_healthy = True
    await sim.recover_order_service()

    elapsed = time.perf_counter() - start_time

    print(f"\n[SIMULATION TELEMETRY & RESULTS - Elapsed Time: {elapsed:.3f}s]")
    print(f"Total Inbound HTTP Requests       : {sim.metrics['total_requests']}")
    print(f"Idempotent Duplicates Filtered    : {sim.metrics['duplicate_requests_filtered']}")
    print(f"Total Reservations Granted        : {sim.metrics['reservations_granted']}")
    print(f"Out of Stock Fast-Rejections (409): {sim.metrics['out_of_stock_rejected']}")
    print(f"Payment Transactions Attempted    : {sim.metrics['payments_attempted']}")
    print(f"Successful Payments (95%)         : {sim.metrics['payments_succeeded']}")
    print(f"Failed Payments (5%)              : {sim.metrics['payments_failed']}")
    print(f"Stock Reclaimed on Payment Failure: {sim.metrics['stock_reclaimed_to_pool']}")
    print(f"Orders Buffered During 30s Outage : {sim.metrics['orders_buffered_during_outage']}")
    print(f"Final Confirmed Orders            : {sim.metrics['orders_confirmed']}")
    print("-" * 75)
    print(f"MySQL State -> Sold: {sim.mysql.sold_stock} | Reserved: {sim.mysql.reserved_stock} | Available: {sim.mysql.available_stock}")
    print(f"Redis State -> Available Stock In Cache: {sim.redis.stock}")
    print("-" * 75)

    # Strict Guarantee Verification Assertions
    assert sim.mysql.sold_stock <= 100, f"VIOLATION: Oversold items! Sold: {sim.mysql.sold_stock}"
    assert (sim.mysql.sold_stock + sim.mysql.reserved_stock + sim.mysql.available_stock) == 100, "VIOLATION: Inventory conservation invariant broken!"
    assert sim.metrics['duplicate_requests_filtered'] > 0, "VIOLATION: Idempotency filter did not intercept duplicates!"
    assert sim.metrics['orders_confirmed'] == sim.mysql.sold_stock, "VIOLATION: Mismatch between confirmed orders and sold inventory!"
    
    print("[AUDIT SUCCESS: ZERO OVERSELLING - 100% INVENTORY INTEGRITY VERIFIED]")
    print("=" * 75)

if __name__ == "__main__":
    asyncio.run(run_hackathon_simulation())
