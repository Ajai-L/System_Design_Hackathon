# 02. High-Level Architecture (HLD)

**Project:** SALESTORM Flash-Sale Platform  
**Phase:** Stage 2 — Architecture & High-Level Design  
**Author:** Principal System Architect  

---

## 1. System Context Diagram (C4 Level 1)

The System Context diagram describes SALESTORM's position within the enterprise ecosystem, its user personas, and its external third-party dependencies.

```mermaid
flowchart TD
    subgraph Users ["Actors"]
        Customer["Customer\n(Web Browser / Mobile App)"]
        OpsAdmin["Operations & Admin Team\n(Flash Sale Config & Monitoring)"]
    end

    subgraph SystemBoundary ["SALESTORM Platform Boundary"]
        SalePlatform["SALESTORM Flash-Sale Platform\n(Spring Boot Microservices, Redis Cluster, MySQL 8.0, Kafka)"]
    end

    subgraph ExternalSystems ["External Third-Party Systems"]
        IDP["Identity Provider\n(OAuth2 / OIDC Keycloak)"]
        PaymentGW["Payment Gateway Provider\n(Stripe / PayPal API)"]
        NotificationProvider["Notification Provider\n(SendGrid Email / Twilio SMS)"]
        LogisticsPartner["3PL Logistics & Fulfillment\n(FedEx / ShipStation API)"]
    end

    Customer -->|"Discovers products, initiates reservations, submits payments (HTTPS)"| SalePlatform
    OpsAdmin -->|"Seeds inventory, configures flash deals, monitors metrics"| SalePlatform
    SalePlatform -->|"Validates user tokens & permissions"| IDP
    SalePlatform -->|"Authorizes and captures charges (PCI-DSS tokenized)"| PaymentGW
    SalePlatform -->|"Dispatches order confirmations & shipping updates"| NotificationProvider
    SalePlatform -->|"Transmits validated order manifests for shipment"| LogisticsPartner
```

---

## 2. Container & Service Architecture Diagram (C4 Level 2)

The container diagram illustrates the microservice decomposition, data persistence boundaries, caching layers, and asynchronous messaging topologies.

```mermaid
flowchart TD
    Client["Client Browser / Mobile App"] -->|"HTTPS / TLS 1.3"| CDN["Cloudflare Edge CDN & WAF\n(DDoS Protection, Static Asset Caching)"]
    CDN -->|"Reverse Proxy"| LB["AWS Application Load Balancer / NGINX Ingress"]

    subgraph IngressLayer ["API Gateway & Security Layer"]
        LB --> Gateway["Spring Cloud API Gateway\n(Token Bucket Rate Limiter, JWT Auth Validation, Request Routing)"]
    end

    subgraph MicroservicesDomain ["Spring Boot Microservices Ecosystem"]
        Gateway -->|"REST /sync"| CatalogService["Product Catalog Service\n(Port 8081)"]
        Gateway -->|"REST /sync (Reservation)"| InventoryService["Flash-Sale & Inventory Service\n(Port 8082)"]
        Gateway -->|"REST /sync (Payment)"| PaymentService["Payment Processing Service\n(Port 8083)"]
        Gateway -->|"REST /sync (Order Status)"| OrderService["Order Fulfillment Service\n(Port 8084)"]

        ReconciliationWorker["Reconciliation & Expiry Reaper\n(Scheduled Spring Task)"]
    end

    subgraph CacheLayer ["In-Memory Cache & Admission Gatekeeper"]
        RedisCluster[("Redis 7.2 Cluster\n- Atomic Stock Counter (Lua Scripts)\n- Redlock Distributed Locks\n- Idempotency Cache (SETNX)\n- Catalog Cache-Aside")]
    end

    subgraph MessageBackbone ["Event-Driven Messaging Backbone"]
        Kafka[("Apache Kafka Message Broker\nTopics:\n- inventory.reserved\n- payment.succeeded\n- payment.failed\n- order.confirmed\n- dead-letter-queue")]
    end

    subgraph PersistenceLayer ["Persistence Layer (Database-per-Service, MySQL 8.0)"]
        MySQL_Inventory[("MySQL Inventory DB\n(InnoDB, CHECK constraints,\nInventory & Reservations)")]
        MySQL_Payment[("MySQL Payment DB\n(InnoDB, Payments &\nTransactional Outbox Table)")]
        MySQL_Order[("MySQL Order DB\n(InnoDB, Orders &\nOrder Line Items)")]
    end

    %% Interactions
    CatalogService <-->|"Read-Through / Cache-Aside"| RedisCluster
    InventoryService <-->|"Atomic Lua Check & Decrement"| RedisCluster
    InventoryService <-->|"ACID Reservation Insert"| MySQL_Inventory
    ReconciliationWorker <-->|"Poll Expired Holds"| MySQL_Inventory
    ReconciliationWorker -.->|"Reclaim Stock (+1)"| RedisCluster

    PaymentService <-->|"Idempotency Lock (SETNX)"| RedisCluster
    PaymentService <-->|"Write Payment & Outbox Event"| MySQL_Payment
    PaymentService -->|"Transactional Outbox / CDC"| Kafka

    Kafka -->|"Consumer Group: order-workers"| OrderService
    OrderService <-->|"Write Confirmed Orders"| MySQL_Order
    OrderService -.->|"Emit order.confirmed"| Kafka

    NotificationWorker["Async Notification Worker"]
    Kafka -->|"Consumer Group: notify-workers"| NotificationWorker
    NotificationWorker -->|"Dispatch Email/SMS"| ExternalNotify["External SendGrid / Twilio"]
```

---

## 3. Component Justifications & Bottleneck Analysis

| Component | Primary Architectural Purpose | Potential Bottleneck Under 10k Concurrency | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **Cloudflare CDN & Edge WAF** | Absorbs static catalog traffic, blocks malicious scrapers, terminates TLS at edge. | Edge cache misses during price updates. | High TTL for static catalog data with instant webhook-based purge on price/stock mutation. |
| **Spring Cloud API Gateway** | Centralized ingress routing, JWT token extraction, and token-bucket rate limiting. | Netty event loop thread saturation under request surges. | Horizontal Autoscaling (HPA), non-blocking reactive filters, short request timeout limits (2s). |
| **Redis 7.2 Cluster** | **Primary Traffic Gatekeeper**: Executes atomic Lua check-and-decrement in $< 0.3\text{ ms}$, filtering 99% of requests in memory. | Single Redis shard CPU saturation or network I/O limits. | Single-key Lua script benchmarked at $> 120,000\text{ ops/sec}$. Cluster sharding with read replicas for status reads. |
| **Flash-Sale Inventory Service** | Validates customer limits and persists reservation holds into MySQL. | HikariCP database connection pool exhaustion if 10k threads hit MySQL simultaneously. | **Two-Tier Architecture**: Only the 100 requests granted by Redis ever enter MySQL; remaining 9,900 are fast-rejected. |
| **Payment Service** | Idempotently coordinates with third-party payment gateways; uses Transactional Outbox. | External Stripe latency (800ms) causing thread blocking. | Non-blocking HTTP client (WebClient), Resilience4j circuit breaker, and async event dispatch. |
| **Apache Kafka Broker** | Buffers high-volume events, decouples Payment from Order Service, prevents data loss during outages. | Consumer lag during downstream crashes or partition hot-spotting. | Partitioning by `customer_id`, durable SSD log segments, consumer auto-recovery upon restart. |
| **MySQL 8.0 Primary-Replica** | Systems of record for financial auditability, ACID transactions, and hardware CHECK constraints. | Lock contention and disk I/O on single product row. | Row-level locking scoped strictly to 100 transactions; read replicas offload all reporting traffic. |

---

## 4. Communication Protocols: Synchronous vs. Asynchronous Matrix

| Service Boundary | Protocol | Pattern | Justification |
| :--- | :--- | :--- | :--- |
| **Client $\rightarrow$ API Gateway** | HTTPS / TLS 1.3 | Synchronous REST | Client requires immediate synchronous feedback (reservation token or out-of-stock notification). |
| **Gateway $\rightarrow$ Inventory Service** | HTTP/2 REST | Synchronous Request-Response | Strict hold guarantee: Client must immediately obtain `reservation_id` or 409 Conflict. |
| **Inventory $\rightarrow$ Redis** | RESP3 TCP | Synchronous Atomic Lua | Redis executes in $< 1\text{ ms}$; atomic serial execution guarantees zero overselling. |
| **Inventory $\rightarrow$ MySQL** | JDBC (HikariCP) | Synchronous ACID Transaction | Guarantees durable persistent reservation before client proceeds to payment. |
| **Payment $\rightarrow$ External Gateway** | HTTPS REST | Synchronous with Circuit Breaker | Financial authorization requires authoritative external confirmation. |
| **Payment $\rightarrow$ Order Service** | Apache Kafka | **Asynchronous Event-Driven Saga** | **Decouples services**: If Order Service is down for 30s, Payment completes without error; Order consumes event on recovery. |
| **Order $\rightarrow$ Notification** | Apache Kafka | Asynchronous Publish-Subscribe | Customer notification must not block the core checkout execution critical path. |

---

## 5. Docker-Based Deployment Architecture

The following deployment topology isolates internal networks, configures multi-replica services, and provides durable volume mounts.

```mermaid
flowchart TD
    subgraph DockerHost ["Docker Swarm / Kubernetes Production Node Cluster"]
        subgraph NetFrontend ["frontend-net (Bridge / Overlay Network)"]
            LB_Cont["salestorm-ingress\n(NGINX / Envoy Proxy)\nPorts: 80, 443"]
            GW_Cont1["gateway-service-instance-1\nPort: 8080"]
            GW_Cont2["gateway-service-instance-2\nPort: 8080"]
        end

        subgraph NetBackend ["backend-net (Internal Microservice Mesh)"]
            Inv_Cont1["inventory-service-1\nPort: 8082"]
            Inv_Cont2["inventory-service-2\nPort: 8082"]
            Inv_Cont3["inventory-service-3\nPort: 8082"]
            Pay_Cont1["payment-service-1\nPort: 8083"]
            Pay_Cont2["payment-service-2\nPort: 8083"]
            Ord_Cont1["order-service-1\nPort: 8084"]
            Ord_Cont2["order-service-2\nPort: 8084"]
            Recon_Cont["reconciliation-worker\nPort: 8085"]
        end

        subgraph NetData ["data-net (Isolated Persistent Storage Network)"]
            Redis_Master["redis-master\nPort: 6379\nMount: /data/redis-master"]
            Redis_Replica["redis-replica\nPort: 6380\nMount: /data/redis-replica"]
            Kafka_Broker1["kafka-broker-1\nPort: 9092\nMount: /var/lib/kafka/data"]
            Kafka_Broker2["kafka-broker-2\nPort: 9093\nMount: /var/lib/kafka/data"]
            Zookeeper["zookeeper-kraft\nPort: 2181"]
            MySQL_Master["mysql-primary (8.0)\nPort: 3306\nMount: /var/lib/mysql-master"]
            MySQL_Replica["mysql-replica (8.0)\nPort: 3307\nMount: /var/lib/mysql-replica"]
        end
    end

    LB_Cont --> GW_Cont1 & GW_Cont2
    GW_Cont1 & GW_Cont2 --> Inv_Cont1 & Inv_Cont2 & Inv_Cont3
    GW_Cont1 & GW_Cont2 --> Pay_Cont1 & Pay_Cont2
    GW_Cont1 & GW_Cont2 --> Ord_Cont1 & Ord_Cont2

    Inv_Cont1 & Inv_Cont2 & Inv_Cont3 --> Redis_Master & MySQL_Master
    Pay_Cont1 & Pay_Cont2 --> Redis_Master & MySQL_Master & Kafka_Broker1
    Ord_Cont1 & Ord_Cont2 --> Kafka_Broker1 & MySQL_Master
    Recon_Cont --> MySQL_Master & Redis_Master

    Redis_Master -.->|"Replication"| Redis_Replica
    MySQL_Master -.->|"Row-Based Binlog Replication"| MySQL_Replica
    Kafka_Broker1 <--> Zookeeper
    Kafka_Broker2 <--> Zookeeper
```

---

## 6. Scaling to $50\times$ Traffic Surge ($500,000\text{ req/s}$)

If inbound traffic surges by $50\times$ (from 10,000 to 500,000 requests per second):
1. **Edge Offloading:** Cloudflare CDN serves cached static catalog pages and countdown timers directly from edge Points of Presence (PoPs), absorbing $95\%$ of read traffic.
2. **Gateway Tier Auto-Scaling:** Spring Cloud Gateway horizontally scales from 2 to 20 instances via Kubernetes Horizontal Pod Autoscaler (HPA) targeting $70\%$ CPU utilization.
3. **Redis Cluster Partitioning:** Redis stock keys are pre-sharded across multiple hash slots. Single-threaded Lua script throughput scales linearly across cluster nodes.
4. **Fast-Drop Gate:** The moment Redis stock reaches 0, the API Gateway immediately enables an in-memory circuit breaker flag (`LOCAL_STOCK_DEPLETED = true`), returning HTTP 409 responses at the gateway layer without generating any downstream network hops.
