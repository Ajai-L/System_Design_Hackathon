# 08. Scalability, Reliability & Critical Workflows

**Project:** SALESTORM Flash-Sale Platform  
**Phase:** Stage 4 & Stage 8 — Resilience, High Availability & Workflow Verification  
**Author:** Principal System Architect  

---

## 1. Complete Reservation & Order State Transition Diagram

```mermaid
stateDiagram-v2
    [*] --> AVAILABLE : Stock Seeded (100 Units)

    AVAILABLE --> RESERVED : Buy Request Succeeded (Redis Lua Decrement & DB Write)
    AVAILABLE --> SOLD_OUT : Available Units Reach 0 (Fast Rejection)

    state RESERVED {
        [*] --> HOLD_ACTIVE : 10-Minute Expiry Timer Started
        HOLD_ACTIVE --> PAYMENT_PENDING : User Initiates Checkout & Payment
    }

    PAYMENT_PENDING --> CONFIRMED : Payment Captured (200 OK from Gateway)
    PAYMENT_PENDING --> RELEASED : Payment Failed / Card Declined
    PAYMENT_PENDING --> RELEASED : Gateway Timeout / Network Drop
    HOLD_ACTIVE --> EXPIRED : 10-Minute Timer Elapses Without Payment

    RELEASED --> AVAILABLE : Compensation Triggered (Stock Restored to Pool)
    EXPIRED --> AVAILABLE : Reaper Worker Restores Stock (+1 to Redis & DB)

    CONFIRMED --> PROCESSING : Order Service Receives Payment Event
    PROCESSING --> SHIPPED : Warehouse Dispatches Tracking Manifest
    SHIPPED --> OUT_FOR_DELIVERY : 3PL Scans at Destination Hub
    OUT_FOR_DELIVERY --> DELIVERED : Item Handed to Customer
    DELIVERED --> [*]
```

---

## 2. Sequence Diagrams for Critical Purchase Workflows

### 2.1 Flow 1: Successful Purchase Flow (Request to Order Confirmation)

```mermaid
sequenceDiagram
    autonumber
    actor Customer
    participant GW as API Gateway
    participant InvService as Inventory Service
    participant Redis as Redis Cluster
    participant MySQL_Inv as MySQL Inventory DB
    participant PayService as Payment Service
    participant ExtPG as Stripe / Payment GW
    participant Kafka as Apache Kafka
    participant OrderService as Order Service
    participant MySQL_Ord as MySQL Order DB

    Customer->>GW: POST /api/v1/reservations (X-Idempotency-Key)
    GW->>InvService: Forward Reservation Request
    InvService->>Redis: Execute Lua Script (Stock Check & Decrement)
    Redis-->>InvService: Success (Stock: 99 remaining, Token Issued)
    InvService->>MySQL_Inv: INSERT INTO inventory_reservation (Status='RESERVED')
    MySQL_Inv-->>InvService: 1 Row Inserted
    InvService-->>Customer: 201 Created (reservation_id, expires_at: +10m)

    Customer->>GW: POST /api/v1/payments/process (reservation_id, card_token)
    GW->>PayService: Forward Payment Request
    PayService->>Redis: SET payment_lock:{res_id} NX EX 60
    PayService->>ExtPG: POST /v1/charges (Amount, Idempotency-Key)
    ExtPG-->>PayService: 200 OK (Charge Succeeded, tx_id="ch_99x")
    
    critical Save Payment & Outbox Event in Single ACID Transaction
        PayService->>PayService: Begin DB Transaction
        PayService->>PayService: INSERT INTO payment (Status='SUCCESS', tx_id)
        PayService->>PayService: INSERT INTO transactional_outbox (Event='PaymentSucceeded')
        PayService->>PayService: Commit Transaction
    end

    PayService-->>Customer: 200 OK (Payment Verified, Processing Order)
    
    PayService->>Kafka: Publish Event to topic 'payment.succeeded'
    Kafka->>OrderService: Consume 'PaymentSucceeded' Event
    OrderService->>MySQL_Ord: INSERT INTO orders & order_items
    OrderService->>MySQL_Inv: UPDATE inventory_reservation SET status='CONFIRMED'
    OrderService->>Kafka: Publish Event to topic 'order.confirmed'
```

---

### 2.2 Flow 2: Payment Failure Triggering Reservation Release

```mermaid
sequenceDiagram
    autonumber
    actor Customer
    participant PayService as Payment Service
    participant ExtPG as Stripe / Payment GW
    participant Kafka as Apache Kafka
    participant InvService as Inventory Service
    participant Redis as Redis Cluster
    participant MySQL_Inv as MySQL Inventory DB

    Customer->>PayService: POST /api/v1/payments/process (reservation_id)
    PayService->>ExtPG: Charge Card
    ExtPG-->>PayService: 402 Card Declined / Insufficient Funds
    
    critical Record Failed Payment & Outbox Rollback
        PayService->>PayService: Begin DB Transaction
        PayService->>PayService: INSERT INTO payment (Status='FAILED')
        PayService->>PayService: INSERT INTO transactional_outbox (Event='PaymentFailed')
        PayService->>PayService: Commit DB Transaction
    end

    PayService-->>Customer: 402 Payment Required ("Card declined, reservation released")
    PayService->>Kafka: Publish Event 'payment.failed' (reservation_id, product_id)
    
    Kafka->>InvService: Consume 'payment.failed' Event
    InvService->>MySQL_Inv: UPDATE inventory_reservation SET status='RELEASED' WHERE id = ?
    InvService->>MySQL_Inv: UPDATE inventory SET available = available + 1, reserved = reserved - 1
    InvService->>Redis: redis.call('INCR', 'stock:PROD-100')
    Note over Redis,MySQL_Inv: Stock restored to pool. Waiting customer can now claim it!
```

---

### 2.3 Flow 3: Payment Success Followed by Temporary Order Service Failure (30s Downtime)

This workflow verifies system survival when the Order Service crashes for 30 seconds immediately after payment authorization.

```mermaid
sequenceDiagram
    autonumber
    actor Customer
    participant PayService as Payment Service
    participant MySQL_Pay as MySQL Payment DB
    participant OutboxPoller as Debezium CDC / Outbox Poller
    participant Kafka as Kafka Broker ('payment.succeeded')
    participant OrderService as Order Service (Down for 30s)
    participant MySQL_Ord as MySQL Order DB

    PayService->>MySQL_Pay: Transaction: [INSERT Payment(SUCCESS) + INSERT OutboxEvent]
    MySQL_Pay-->>PayService: Transaction Committed
    PayService-->>Customer: 200 OK ("Payment Successful! Order in generation")

    OutboxPoller->>MySQL_Pay: Tail Binlog / Poll unread events
    OutboxPoller->>Kafka: Push message [payment_id, reservation_id, customer_id]
    
    Note over OrderService: 💥 OUTAGE: Order Service is Down (0% Availability for 30s)
    
    Kafka--x OrderService: Message delivery fails / Consumer disconnected
    Note over Kafka: Kafka buffers message on disk (Retention: 7 days). No data is lost!

    Note over OrderService: ⏳ 30 SECONDS ELAPSE... Order Service restarts and passes healthcheck
    
    OrderService->>Kafka: Reconnect to Consumer Group 'order-service-group'
    Kafka->>OrderService: Replay pending message from uncommitted offset
    
    critical Idempotent Order Creation
        OrderService->>MySQL_Ord: Check if order exists for reservation_id
        OrderService->>MySQL_Ord: INSERT INTO orders (order_id, reservation_id, status='CONFIRMED')
        OrderService->>MySQL_Ord: Commit Transaction
    end

    OrderService->>Kafka: Commit Consumer Offset (Ack message)
    OrderService->>Kafka: Emit 'order.created' Event
    Note over Customer,OrderService: Order reconciled without manual human intervention.
```

---

## 3. Asynchronous Resilience & Reconciliation Architecture

### 3.1 Retry & Backoff Strategy
For non-fatal errors (transient network drops), consumers adhere to an exponential backoff formula:
$$t_{\text{wait}} = \min(t_{\text{max}}, t_{\text{base}} \times 2^{\text{retry\_count}}) \pm \text{jitter}$$
- Base delay: $500\text{ ms}$; Multiplier: $2.0$; Max retries: 3.
- If all 3 retries fail, messages are diverted to a dedicated Dead-Letter Queue (DLQ): `order-service.DLQ`.

### 3.2 Scheduled Reconciliation Worker (Fail-Safe Reaper)
A background Spring Scheduled task runs every 10 seconds to detect orphaned holds or desynchronized stock:
```java
@Component
public class ReservationReconciliationReaper {

    @Autowired private ReservationRepository reservationRepo;
    @Autowired private InventoryRepository inventoryRepo;
    @Autowired private RedisTemplate<String, String> redisTemplate;

    @Scheduled(fixedRate = 10000)
    @Transactional
    public void sweepExpiredReservations() {
        Instant now = Instant.now();
        List<InventoryReservation> expired = reservationRepo.findExpiredReservations(now);

        for (InventoryReservation res : expired) {
            res.transitionTo(ReservationStatus.EXPIRED);
            reservationRepo.save(res);

            // Reclaim stock in MySQL
            inventoryRepo.releaseReservedToAvailable(res.getProductId(), res.getQuantity());

            // Reclaim stock in Redis
            redisTemplate.opsForValue().increment("stock:" + res.getProductId(), res.getQuantity());
        }
    }
}
```
