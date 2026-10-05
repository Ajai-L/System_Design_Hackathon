# 05. RESTful API Specifications

**Project:** SALESTORM Flash-Sale Platform  
**Phase:** Stage 7 — API & Event Interface Design  
**Author:** Principal System Architect  
**Specification Standard:** REST / OpenAPI 3.0 / RFC 7807 (Problem Details)  

---

## 1. Global Ingress Headers & Standards

All write-capable flash-sale endpoints strictly require the following headers:
- `Authorization: Bearer <JWT>` — Cryptographically signed user authentication token.
- `Content-Type: application/json` — Standard JSON payload format.
- `X-Idempotency-Key: <UUIDv4>` — Required for state-mutating requests (`POST`, `PUT`). Ensures duplicate requests yield identical, cached responses without repeating transactions.
- `X-Trace-Id: <UUIDv4>` — Distributed tracing identifier propagated via W3C TraceContext.

---

## 2. API Endpoints

### 2.1 Endpoint 1: Create Flash-Sale Reservation
Acquires a temporary 10-minute hold on a flash-sale item.

- **HTTP Method:** `POST`
- **Path:** `/api/v1/flash-sale/reservations`
- **Rate Limit:** 5 requests/minute per authenticated user IP.

#### Request Headers
```http
POST /api/v1/flash-sale/reservations HTTP/1.1
Host: api.salestorm.io
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI...
Content-Type: application/json
X-Idempotency-Key: 7b92f7c0-8d4e-4b47-b892-d98c25785012
X-Trace-Id: c9a0e68d-82d4-4e2b-986d-6eb4519920b1
```

#### Request Payload
```json
{
  "product_id": 100,
  "quantity": 1
}
```

#### Response 201 Created (Reservation Granted)
```http
HTTP/1.1 201 Created
Content-Type: application/json
Location: /api/v1/flash-sale/reservations/res_8f7b2c14-5d9a-4c11-b921-3e28c7f99012
```
```json
{
  "status": "SUCCESS",
  "data": {
    "reservation_id": "res_8f7b2c14-5d9a-4c11-b921-3e28c7f99012",
    "product_id": 100,
    "quantity": 1,
    "status": "RESERVED",
    "expires_at": "2026-10-05T10:10:00.000Z",
    "hold_duration_seconds": 600
  }
}
```

#### Response 409 Conflict (Flash Sale Sold Out)
```http
HTTP/1.1 409 Conflict
Content-Type: application/problem+json
```
```json
{
  "type": "https://salestorm.io/errors/out-of-stock",
  "title": "Inventory Exhausted",
  "status": 409,
  "detail": "Product 100 has zero remaining units in this flash sale event.",
  "instance": "/api/v1/flash-sale/reservations",
  "trace_id": "c9a0e68d-82d4-4e2b-986d-6eb4519920b1"
}
```

#### Response 429 Too Many Requests (Rate Limit Exceeded)
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 30
Content-Type: application/problem+json
```
```json
{
  "type": "https://salestorm.io/errors/rate-limit-exceeded",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Too many reservation requests submitted. Please wait 30 seconds.",
  "instance": "/api/v1/flash-sale/reservations"
}
```

---

### 2.2 Endpoint 2: Process Payment
Authorizes and captures funds for an active reservation hold.

- **HTTP Method:** `POST`
- **Path:** `/api/v1/payments/process`

#### Request Headers
```http
POST /api/v1/payments/process HTTP/1.1
Host: api.salestorm.io
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI...
Content-Type: application/json
X-Idempotency-Key: 11a0c8b2-33d4-4b47-b892-d98c25785999
```

#### Request Payload
```json
{
  "reservation_id": "res_8f7b2c14-5d9a-4c11-b921-3e28c7f99012",
  "payment_provider": "STRIPE",
  "payment_token": "tok_visa_4242",
  "amount": 499.99,
  "currency": "USD"
}
```

#### Response 200 OK (Payment Verified)
```http
HTTP/1.1 200 OK
Content-Type: application/json
```
```json
{
  "status": "SUCCESS",
  "data": {
    "payment_id": "pay_99a8b7c6-1122-3344-5566-778899aabbcc",
    "reservation_id": "res_8f7b2c14-5d9a-4c11-b921-3e28c7f99012",
    "status": "SUCCESS",
    "provider_tx_id": "ch_3N8xYz2eZvKYlo2C1g7Q",
    "amount": 499.99,
    "currency": "USD",
    "processed_at": "2026-10-05T10:02:15.120Z"
  }
}
```

#### Response 402 Payment Required (Card Declined)
```http
HTTP/1.1 402 Payment Required
Content-Type: application/problem+json
```
```json
{
  "type": "https://salestorm.io/errors/payment-declined",
  "title": "Payment Authorization Failed",
  "status": 402,
  "detail": "Card declined: Insufficient funds. Your reservation has been released.",
  "instance": "/api/v1/payments/process",
  "reservation_status": "RELEASED"
}
```

#### Response 410 Gone (Reservation Expired)
```http
HTTP/1.1 410 Gone
Content-Type: application/problem+json
```
```json
{
  "type": "https://salestorm.io/errors/reservation-expired",
  "title": "Reservation Expired",
  "status": 410,
  "detail": "The 10-minute hold window for reservation res_8f7b2c14 elapsed. Stock was returned to the pool.",
  "instance": "/api/v1/payments/process"
}
```

---

### 2.3 Endpoint 3: Get Order Status
Retrieves current fulfillment state for a placed order.

- **HTTP Method:** `GET`
- **Path:** `/api/v1/orders/{orderId}`

#### Response 200 OK
```http
HTTP/1.1 200 OK
Content-Type: application/json
```
```json
{
  "status": "SUCCESS",
  "data": {
    "order_id": "ord_55112233-aabb-ccdd-eeff-001122334455",
    "customer_id": 40012,
    "status": "CONFIRMED",
    "items": [
      {
        "product_id": 100,
        "quantity": 1,
        "unit_price": 499.99
      }
    ],
    "total_amount": 499.99,
    "payment_id": "pay_99a8b7c6-1122-3344-5566-778899aabbcc",
    "tracking_number": null,
    "created_at": "2026-10-05T10:02:16.000Z",
    "updated_at": "2026-10-05T10:02:16.000Z"
  }
}
```

#### Response 404 Not Found
```http
HTTP/1.1 404 Not Found
Content-Type: application/problem+json
```
```json
{
  "type": "https://salestorm.io/errors/order-not-found",
  "title": "Order Not Found",
  "status": 404,
  "detail": "Order ID ord_99999999 does not exist.",
  "instance": "/api/v1/orders/ord_99999999"
}
```
