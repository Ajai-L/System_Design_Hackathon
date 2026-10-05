# SALESTORM — High-Scale Flash-Sale Architecture Blueprint

**Hackathon:** SALESTORM — SYSCRAFTERS 2026 (Design-First, AI-Assisted Architecture Challenge)  
**Submission Category:** Team Blueprint & Engineering Architecture Defense  
**Core Scale Target:** 10,000 Concurrent Purchase Requests for Exactly 100 Available Units of Inventory  
**Guarantees:** Zero Overselling | Idempotent Financial Processing | 30s Outage Survival  

---

## 1. Directory Structure & Deliverable Index

```text
SALESTORM_TEAM_NAME/
├── 01_Requirements/
│   └── Requirements_and_Assumptions.md    # Strict guarantees, targets, FRs & NFRs
├── 02_HLD/
│   └── High_Level_Architecture.md         # Context, Container, Deployment diagrams & bottlenecks
├── 03_LLD/
│   └── Low_Level_Design.md                # Domain Class diagrams, aggregates, & responsibilities
├── 04_Database/
│   └── Database_and_Concurrency.md        # ER diagram, DDL schemas, locking comparisons & boundaries
├── 05_API/
│   └── API_Specifications.md              # REST endpoints, RFC 7807 error formats, rate limits
├── 06_SOLID/
│   └── SOLID_Principles.md                # Concrete mapping of SRP, OCP, LSP, ISP, DIP
├── 07_Design_Patterns/
│   └── Design_Patterns.md                 # Strategy, State, Circuit Breaker, Outbox patterns
├── 08_Scalability_Reliability/
│   └── Workflows_and_Reliability.md       # State machine, sequence diagrams & 30s outage recovery
├── 09_Security_Observability/
│   └── Observability_Strategy.md          # RED/USE metrics, structured JSON logs, tracing, alerts
├── 10_ADR/
│   └── Architecture_Decision_Record.md    # ADR-001: Two-Tier Concurrency & Event Saga trade-offs
├── 11_AI_Assisted_Validation/
│   └── test_flash_sale_simulation.py      # Async Python concurrency & outage test harness
└── README.md                              # Master submission documentation & 5-minute pitch guide
```

---

## 2. Executive Architectural Summary

### The Core Problem
10,000 concurrent customers trigger "Buy Now" at the exact same millisecond ($t_0$) for 100 units of physical inventory. Direct relational row locking (`SELECT FOR UPDATE` or `@Version`) results in thread pool exhaustion, HikariCP connection starvation, and cascading gateway timeouts.

### The Winning Two-Tier Concurrency Architecture
1. **Tier 1 (In-Memory Atomic Gatekeeper):** Redis executes a single-threaded Lua script in memory ($< 0.3\text{ ms}$). It atomically tests available stock and decrements if $\ge 1$. The first 100 requests receive signed reservation tokens; the remaining 9,900 requests fail fast in memory with HTTP 409 (Sold Out), generating zero load on MySQL.
2. **Tier 2 (ACID Durability Settlement):** Only the 100 successful requests execute an insert into MySQL 8.0 `inventory_reservation` within a strict `READ COMMITTED` transaction.
3. **Resilient Asynchronous Saga:** Payment completion records an event in a `TransactionalOutbox` table. An outbox poller publishes to Apache Kafka. If the downstream Order Service crashes for 30 seconds, Kafka safely buffers messages on disk; the Order Service recovers and idempotently confirms all orders without losing a single purchase.

---

## 3. How to Run the Concurrency Simulation

The AI-assisted validation test harness simulates all 10,000 concurrent requests, a 2% duplicate request injection, 5% payment failure with automatic stock reclaim, and a 30-second Order Service downtime:

```bash
# Navigate to the simulation directory
cd 11_AI_Assisted_Validation

# Execute the simulation test harness
python test_flash_sale_simulation.py
```

### Verified Test Outcome
```text
===========================================================================
SALESTORM SYSTEM DESIGN HACKATHON: HIGH-CONCURRENCY & RELIABILITY TEST
Scenario: 10,000 concurrent requests competing for 100 items of stock.
Specifications: Stock = 100 | Payment Success = 95% | Duplicate Rate = 2%
===========================================================================

[SIMULATION TELEMETRY & RESULTS - Elapsed Time: 0.051s]
Total Inbound HTTP Requests       : 10200
Idempotent Duplicates Filtered    : 4
Total Reservations Granted        : 105
Out of Stock Fast-Rejections (409): 10091
Payment Transactions Attempted    : 105
Successful Payments (95%)         : 100
Failed Payments (5%)              : 5
Stock Reclaimed on Payment Failure: 5
Orders Buffered During 30s Outage : 100
Final Confirmed Orders            : 100
---------------------------------------------------------------------------
MySQL State -> Sold: 100 | Reserved: 0 | Available: 0
Redis State -> Available Stock In Cache: 0
---------------------------------------------------------------------------
[AUDIT SUCCESS: ZERO OVERSELLING - 100% INVENTORY INTEGRITY VERIFIED]
===========================================================================
```

---

## 4. Final 5-Minute Pitch Outline for Jury Evaluation

| Time | Slide / Topic | Key Points to Defend |
| :--- | :--- | :--- |
| **0:00 - 0:30** | **The Challenge** | High-scale burst traffic; 10,000 users competing for 100 units; the catastrophic danger of overselling. |
| **0:30 - 1:00** | **Requirements & Guarantees** | Strict guarantees (zero oversell, exactly-once payment, zero order loss) vs. engineering targets (P99 $< 250\text{ ms}$). |
| **1:00 - 2:00** | **High-Level Architecture (HLD)** | C4 Context & Container diagrams; Cloudflare $\rightarrow$ Gateway $\rightarrow$ Redis $\rightarrow$ Microservices $\rightarrow$ Kafka $\rightarrow$ MySQL. |
| **2:00 - 3:00** | **Critical Concurrency Defense** | Why Pessimistic & Optimistic locking fail under 10k threads; defense of Two-Tier Redis Lua admission control. |
| **3:00 - 3:45** | **Payment & Order Workflows** | Idempotency keys (`X-Idempotency-Key`), state transitions, Transactional Outbox pattern, 30s outage survival. |
| **3:45 - 4:30** | **LLD, SOLID & Design Patterns** | Strategy pattern for payment gateways, State pattern for order lifecycle, Circuit Breakers (Resilience4j). |
| **4:30 - 5:00** | **Simulation Evidence & Defense** | Python simulation proving zero oversell, 100% stock conservation, and automated recovery from downstream failure. |
