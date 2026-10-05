# 01. Requirements & Assumptions Document

**Project:** SALESTORM Flash-Sale Platform  
**Target Event:** Limited-Stock High-Scale Flash Sale  
**Core Scale Target:** 10,000 concurrent purchase requests for 100 available units of Product X  
**Author:** System Architecture & Engineering Consulting Unit  

---

## 1. Executive Summary & Business Context
SALESTORM is preparing for a high-demand flash sale campaign where customer demand exceeds available inventory by two orders of magnitude (10,000 concurrent shoppers competing for 100 units of physical stock). During such flash-sale spikes, traditional e-commerce architectures face critical failure modes:
1. **Inventory Overselling:** Race conditions at the database layer allowing more than 100 successful checkouts, resulting in canceled orders, customer dissatisfaction, and regulatory penalties.
2. **Database Contention Collapse:** Thousands of concurrent connection threads contending for a single database row, exhausting connection pools (HikariCP) and causing cascading gateway timeouts.
3. **Double Charging & Lost Transactions:** Unhandled payment retries and downstream service outages (e.g., Order Service downtime) leading to duplicate charges or paid orders vanishing.

This document establishes the formal functional boundaries, strict guarantees versus engineering targets, and operational constraints governing the SALESTORM platform.

---

## 2. The Required Business Pipeline

The platform must support the complete end-to-end customer journey:
$$\text{Customer} \longrightarrow \text{Product Discovery} \longrightarrow \text{Cart} \longrightarrow \text{Inventory Check} \longrightarrow \text{Inventory Reservation} \longrightarrow \text{Checkout} \longrightarrow \text{Payment} \longrightarrow \text{Order} \longrightarrow \text{Fulfilment} \longrightarrow \text{Shipment} \longrightarrow \text{Notification} \longrightarrow \text{Delivery Tracking}$$

---

## 3. Functional Requirements

### 3.1 Product Discovery & Inventory Display
- **FR-01:** Real-time catalog display showing flash sale product specifications, pricing, countdown timer, and inventory availability status.
- **FR-02:** When available units reach zero, the user interface and API must immediately transition to "Sold Out", rejecting further checkout attempts.

### 3.2 Inventory Reservation
- **FR-03:** Customers clicking "Buy Now" must initiate an atomic inventory reservation request.
- **FR-04:** Maximum reservation limit of **1 unit per authenticated customer** per flash sale event.
- **FR-05:** Upon successful reservation, a unique `reservation_id` is issued, and the unit is held for a **10-minute hold window** (600 seconds).
- **FR-06:** If payment is not completed within 600 seconds, the hold expires automatically, and the reserved unit returns to the available inventory pool.

### 3.3 Checkout & Payment Processing
- **FR-07:** The customer must submit payment authorization referencing a valid, unexpired `reservation_id`.
- **FR-08:** Payment processing must support idempotency using a client-supplied `X-Idempotency-Key` to prevent duplicate charges upon network retries or double clicks.
- **FR-09:** On payment success, the payment record transitions to `SUCCESS`, and the reservation transitions to `CONFIRMED`.
- **FR-10:** On payment failure (e.g., insufficient funds, card declined), the reservation is immediately released (`RELEASED`), and the item is returned to the available stock pool.

### 3.4 Order Settlement & Fulfillment
- **FR-11:** Confirmed payments must trigger guaranteed asynchronous order creation.
- **FR-12:** If the Order Service experiences a temporary outage, payment success events must be durably preserved and processed upon service recovery.
- **FR-13:** Asynchronous dispatch of order confirmation notifications (Email/SMS) and handoff to 3PL logistics for shipment tracking.

---

## 4. Non-Functional Requirements: Strict Guarantees vs. Engineering Targets

The architecture distinguishes between non-negotiable **Strict Guarantees** (which must never be violated under any failure condition) and **Engineering Targets** (which guide performance and capacity planning).

| Category | Requirement | Specification | Classification | Enforcement Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Consistency** | Zero Overselling | Total Confirmed Sales $\le 100$. Never allow $available\_quantity < 0$. | **Strict Guarantee** | Redis Lua Atomic Pre-allocation + MySQL InnoDB row CHECK constraints |
| **Integrity** | Payment Idempotency | Exactly-once payment capture. 0 duplicate financial charges. | **Strict Guarantee** | Distributed Redis lock + MySQL `idempotency_key` unique index |
| **Reliability** | Zero Data Loss | No paid orders lost during 30s downstream service outage. | **Strict Guarantee** | Transactional Outbox Pattern + Apache Kafka persistent log |
| **Lifecycle** | Automated Stock Reclaim | Expired or failed reservations released within $600\text{s} \pm 5\text{s}$. | **Strict Guarantee** | Redis TTL eviction + Background Scheduled MySQL Reaper Job |
| **Performance** | Reservation Latency | P95 $< 100\text{ ms}$, P99 $< 250\text{ ms}$ under 10k concurrent requests. | **Engineering Target** | In-memory atomic Lua scripts with fast-fail HTTP 409 rejections |
| **Throughput** | Burst Concurrency | Handle 10,000 concurrent checkout attempts at $t_0$. | **Engineering Target** | Asynchronous decoupling, reactive gateway, and horizontal pod autoscaling |
| **Throughput** | Read Scalability | Support 500,000 req/s catalog read queries. | **Engineering Target** | Edge CDN caching (Cloudflare) + Redis distributed cache-aside |
| **Availability** | Platform Uptime | $99.99\%$ availability for browsing, discovery, and cart. | **Engineering Target** | Multi-AZ deployment, load balancing, circuit breakers |
| **Disaster Recovery**| Recovery Objectives | RPO = 0 (zero transactional data loss), RTO $< 30\text{ s}$. | **Engineering Target** | Kafka multi-broker replication and MySQL semi-synchronous replication |

---

## 5. Assumptions & Constraints

1. **Flash Sale Inventory Allocation:** Exactly 100 units of Product X are pre-allocated for the flash sale event.
2. **Traffic Surge Profile:** 10,000 authenticated users click "Buy Now" within a 5-second window. Inbound duplicate request rate is estimated at $2\%$ due to rapid double clicks or mobile network retries.
3. **Payment Provider Behavior:** External payment gateway (Stripe/PayPal) exhibits a $95\%$ success rate, $5\%$ failure rate, and an average response latency of $800\text{ ms}$ with occasional transient timeouts.
4. **Hardware & Infrastructure Limits:** Relational database connection pool is limited to 50 active connections per instance (HikariCP). The database must be shielded from direct 10,000-thread concurrent row contention.
5. **Downstream Resiliency Test Case:** The system must gracefully tolerate an unexpected 30-second complete outage of the Order Service without dropping customer orders or creating data anomalies.
