# 06. SOLID Principles Mapping & Extensibility Analysis

**Project:** SALESTORM Flash-Sale Platform  
**Phase:** Stage 6 — Object-Oriented Principles & Clean Architecture  
**Author:** Principal System Architect  

---

## 1. SOLID Principles Mapping Table

The SALESTORM codebase enforces strict adherence to all five SOLID object-oriented design principles:

| Principle | Architectural Definition | Concrete Classes in SALESTORM | Anti-Pattern Prevented |
| :--- | :--- | :--- | :--- |
| **S** — Single Responsibility | A class should have one, and only one, reason to change. | `PaymentProcessingService` (orchestrates checkout), `OutboxEventPublisher` (publishes events), `ReconciliationScheduler` (cleans expired holds). | **"God Service" Anti-pattern:** Prevents mixing payment gateway communication, Kafka event serialization, and database sweeps in a single class. |
| **O** — Open / Closed | Software entities should be open for extension, but closed for modification. | `PaymentGatewayStrategy` interface with `StripePaymentStrategy`, `PayPalPaymentStrategy`, `AdyenPaymentStrategy`. | **Fragile Switch Statements:** Adding a new payment partner never requires editing or re-testing existing payment code. |
| **L** — Liskov Substitution | Subtypes must be substitutable for their base types without altering system correctness. | Any implementation of `PaymentGatewayStrategy` strictly obeys the contract of `charge(PaymentRequest)` and returns a standardized `ChargeResult`. | **Contract Incompatibility:** A mock test strategy behaves identically to production Stripe integration without raising unexpected runtime exceptions. |
| **I** — Interface Segregation | Clients should not be forced to depend upon interfaces that they do not use. | Segregated interfaces: `InventoryReader` (read-only for catalog), `InventoryWriter` (state mutation), `RefundablePaymentGateway` (refund operations). | **Bloated Interfaces:** Prevents read-only reporting services from exposing mutating methods like `reserveStock()` or `refund()`. |
| **D** — Dependency Inversion | High-level modules should not depend on low-level modules; both should depend on abstractions. | `PaymentProcessingService` depends on `PaymentGatewayStrategy` and `PaymentRepository` interfaces, injected via Spring IoC. | **Tight Coupling:** The core domain logic is decoupled from external payment provider SDKs, HTTP client libraries, or specific JDBC drivers. |

---

## 2. In-Depth Demonstration of SOLID in Action

### 2.1 Single Responsibility Principle (SRP)
```java
// RESPONSIBILITY: Pure orchestration of payment business rules
@Service
public class PaymentProcessingService {
    private final PaymentStrategyFactory strategyFactory;
    private final PaymentRepository paymentRepository;
    private final TransactionalOutboxRepository outboxRepository;

    public PaymentResult processPayment(PaymentCommand command) {
        PaymentGatewayStrategy strategy = strategyFactory.getStrategy(command.getProvider());
        ChargeResult chargeResult = strategy.charge(command.toRequest());
        
        // Mutates domain state only
        Payment payment = Payment.create(command, chargeResult);
        paymentRepository.save(payment);

        // Appends outbox event only
        outboxRepository.save(new TransactionalOutbox("Payment", payment.getId(), "PaymentSucceeded"));
        return PaymentResult.from(payment);
    }
}

// RESPONSIBILITY: Pure asynchronous event polling and dispatching to Kafka
@Component
public class OutboxPoller {
    private final TransactionalOutboxRepository outboxRepository;
    private final KafkaTemplate<String, String> kafkaTemplate;

    @Scheduled(fixedDelay = 500)
    public void pollAndPublish() {
        List<TransactionalOutbox> pending = outboxRepository.findTop100Pending();
        for (TransactionalOutbox event : pending) {
            kafkaTemplate.send(event.getTopic(), event.getPayload());
            event.markPublished();
        }
    }
}
```

---

### 2.2 Open/Closed Principle (OCP) & Extensibility Scenarios

#### Scenario A: Adding a New Payment Provider (e.g., Apple Pay)
To support Apple Pay, developers create a single new class implementing `PaymentGatewayStrategy`. **Zero lines of code in `PaymentProcessingService` are altered**:

```java
@Component("APPLE_PAY")
public class ApplePayPaymentStrategy implements PaymentGatewayStrategy {
    private final ApplePayClient applePayClient;

    @Override
    public ChargeResult charge(PaymentRequest request) {
        ApplePayResponse response = applePayClient.authorizeToken(request.getToken(), request.getAmount());
        return response.isAuthorized() 
            ? ChargeResult.success(response.getTransactionId()) 
            : ChargeResult.failure(response.getDeclineReason());
    }

    @Override
    public RefundResult refund(RefundRequest request) {
        return applePayClient.executeRefund(request.getTransactionId());
    }
}
```

#### Scenario B: Adding a Dynamic Pricing Strategy
The `PricingStrategy` interface allows swapping between `FixedFlashPriceStrategy`, `TieredEarlyBirdStrategy`, or `VIPDiscountStrategy` dynamically at runtime without modifying checkout or inventory modules:

```java
public interface PricingStrategy {
    BigDecimal calculatePrice(Product product, Customer customer);
}

@Component("TIERED_EARLY_BIRD")
public class TieredEarlyBirdPricingStrategy implements PricingStrategy {
    @Override
    public BigDecimal calculatePrice(Product product, Customer customer) {
        // First 50 buyers receive 20% discount; next 50 receive 10%
        return product.getBasePrice().multiply(BigDecimal.valueOf(0.80));
    }
}
```

---

### 2.3 Interface Segregation Principle (ISP)

Instead of a monolithic repository interface, repository abstractions are split into purpose-built contracts:

```java
// Used by high-traffic catalog browsing
public interface InventoryReader {
    int getAvailableStock(Long productId);
    boolean isFlashSaleActive(Long productId);
}

// Used strictly by reservation and settlement services
public interface InventoryWriter {
    int decrementAvailableAndIncrementReserved(Long productId, int quantity);
    int releaseReservedToAvailable(Long productId, int quantity);
}

// Segregated payment capability interfaces
public interface AuthorizablePayment {
    ChargeResult charge(PaymentRequest request);
}

public interface RefundablePayment {
    RefundResult refund(RefundRequest request);
}
```

---

### 2.4 Dependency Inversion Principle (DIP)

High-level domain workflows depend exclusively on abstract interfaces. Infrastructure dependencies are injected by the Spring container:

```java
@Configuration
public class PaymentModuleConfiguration {

    @Bean
    public PaymentProcessingService paymentProcessingService(
            PaymentStrategyFactory strategyFactory,
            PaymentRepository paymentRepository,
            TransactionalOutboxRepository outboxRepository) {
        // High-level service depends on abstraction contracts, not SQL or Stripe SDK details
        return new PaymentProcessingService(strategyFactory, paymentRepository, outboxRepository);
    }
}
```
