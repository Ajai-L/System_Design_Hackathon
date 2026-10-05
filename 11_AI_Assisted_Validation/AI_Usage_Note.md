# AI Usage Note & Prototype Validation

**Tool Used:** Gemini (Agentic AI)
**Purpose:** To generate a localized, asynchronous Python simulation script validating the critical concurrency and fault-tolerance assumptions of our High-Level Design.

**Validation Objective:**
We used AI-assisted coding to build a test harness (`test_flash_sale_simulation.py`) to prove that our Two-Tier Concurrency Architecture (Redis Lua + MySQL) meets the SALESTORM success criteria under extreme load. 

**Results Verified:**
* **Concurrency & Overselling:** Processed 10,200 requests. 10093 were fast-rejected in memory. Exactly 100 final orders were confirmed. Zero overselling occurred.
* **Payment Failures:** 5 payments failed, and the script successfully demonstrated the stock being reclaimed and re-issued to waiting customers.
* **Idempotency:** Successfully filtered duplicate inbound requests without double-charging.
* **Outage Resilience:** Buffered 100 orders in the outbox queue during a simulated 30-second Order Service outage, reconciling all of them upon recovery.

Every team member understands the generated simulation logic and how it reflects our architectural blueprints.