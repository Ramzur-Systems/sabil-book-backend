# Order lifecycle

Accepting an offer creates one Order in `awaiting_payment`. A payment
confirmation moves it to `funded` and records `funded_at`.

Allowed transitions:

```text
awaiting_payment -> funded -> delivered -> under_review
                              ^                |
                              |                +-> correction_requested --+
                              |                                           |
                              +-------------------------------------------+
under_review -> completed -> paid_out
under_review -> disputed -> completed -> paid_out
                        -> refunded
```

Business writes must use `sabil_book.offers.order_services` and
`sabil_book.payments.services`. Services lock the Order row and validate each
edge using `Order.transition_to`; invalid actions return HTTP 409. Direct ORM
status writes and bulk updates bypass the service contract and are reserved
for migrations and test/demo data.

## API

All routes require authentication. Only the customer and provider can list or
retrieve the order; unrelated users receive 404.

| Method/path | Actor | Payload / result |
| --- | --- | --- |
| `GET /api/orders/` | Participant | Their orders |
| `GET /api/orders/{id}/` | Participant | Status, dates, results, correction reason |
| `POST /api/orders/{id}/pay/` | Customer | 202: payment ID, status, amount, currency, provider |
| `POST /api/orders/{id}/attachments/` | Provider | Multipart `file`; stores private Attachment and SHA-256, queues antivirus scan |
| `POST /api/orders/{id}/accept/` | Customer | No payload; moves to `completed` |
| `POST /api/orders/{id}/request-correction/` | Customer | `{"reason": "..."}`; moves to `correction_requested` |
| `POST /api/orders/{id}/dispute/` | Customer | `{"reason": "..."}`; creates Dispute, moves to `disputed` |

Upload is permitted only from `funded` or `correction_requested`. Correction
reasons are limited to 2,000 characters; dispute reasons to 255. Files default
to a maximum of 25 MiB (`ORDER_RESULT_MAX_BYTES`). Each upload keeps prior
attachments; a clean antivirus scan publishes the result and starts a fresh
review period. See [private attachments](attachments.md) for download access,
S3 and ClamAV configuration.

## Timer

`ORDER_REVIEW_PERIOD_SECONDS` defaults to 259200 (72 hours). The deadline is
persisted at delivery. Celery Beat runs `process_order_reviews` every 60 seconds:
it moves `delivered` orders into `under_review` and completes expired reviews.
Customer actions can enter `under_review` immediately, without waiting for Beat.

Both review states are subject to the same persisted deadline. At or after the
deadline customer responses return 409, even if Beat has not yet run. Automatic
completion can lag by one polling interval plus worker queue latency. Correction
and dispute clear the deadline; redelivery sets a new deadline. Repeated ticks
are safe, and missed ticks recover after a worker restart. Run both Celery worker
and Beat. Tests exercise a two-second period using a controlled clock, without
sleeping or requiring Redis.

## Payments and disputes

### Simulated successful payment

Local settings enable `PAYMENT_SIMULATION_ENABLED` by default. A customer's
`POST /api/orders/{id}/pay/` immediately creates a `simulated` payment, confirms
it as `succeeded`, and moves the order to `funded` in the same transaction. The
response contains `status: succeeded` and `provider: simulated`. No Celery worker
or external payment provider is needed. Repeated payment requests return 409
without creating another payment. Access controls and state checks still apply.

Simulation uses a configured country currency when available; otherwise KZ uses
KZT, QA uses QAR, and other/empty countries use USD. It simulates payment
collection only; payout and refund confirmation retain their adapter contracts.

Set `PAYMENT_SIMULATION_ENABLED=False` to use external providers. Base and
production settings default to simulation disabled; other environments can
explicitly enable it with `PAYMENT_SIMULATION_ENABLED=True`.

### External providers

External payment providers are not implemented in this repository. With simulation
disabled, configure country routing and adapter classes using JSON variables:


```text
PAYMENT_PROVIDERS_BY_COUNTRY={"KZ":{"provider":"example","currency":"KZT"}}
PAYMENT_BACKENDS={"example":"your_package.payments.ExampleAdapter"}
```

Country routing uses the customer's uppercased country code. The adapter exposes
`initiate_payment(payment)`, `issue_payout(payout)`, and `refund_payment(payment)`.
Adapters must use persistent `Payment.pk` / `Payout.pk` as provider idempotency
keys: task retries, recovery and concurrent workers can repeat external calls.
Returning from an adapter never marks an operation financially successful.

Trusted, authenticated provider webhook handlers must verify the external
transaction ID, amount and currency, then call `confirm_payment(payment_id)`,
`confirm_payout(payout_id)` or `confirm_refund(payment_id)`. These internal services
are idempotent and validate the Order state. With simulation disabled, public
endpoints cannot confirm funds received, paid out or refunded. An unconfigured
`/pay/` returns 503 and creates no payment.

Entering `completed` queues `issue_order_payout` after the transaction commits.
The task creates a durable pending Payout from a successful Payment; KYC that is
not approved puts it on hold. Without an adapter it stays pending. Confirmation
alone moves the Order to `paid_out`. Periodic `recover_order_payments` requeues
pending payments, completed-order payouts and approved dispute refunds, including
operations whose initial broker message was lost.

A trusted dispute handler calls
`resolve_dispute(order_id, in_provider_favour=..., resolution=...)`. A provider
win completes the order and queues payout. A customer win records the resolution
and queues refund; the Order stays `disputed` until refund confirmation. The
Dispute's `outcome` prevents an unresolved dispute from authorizing a refund.

## Existing data

The migration maps `sent` to `delivered`, `revision_requested` to
`correction_requested`, `dispute`/`appeal`/`unresolved` to `disputed`, and `reverted`
to `refunded`. Legacy `confirmed` meant payout issued and maps to `paid_out`, so
migration cannot enqueue another payout for it. Legacy delivered orders receive
a fresh full review period at migration time; funded rows missing `funded_at`
receive the migration timestamp. Existing dispute records and attachments remain.
