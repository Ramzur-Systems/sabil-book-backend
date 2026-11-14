# Private result attachments

`POST /api/orders/{id}/attachments/` accepts a provider's multipart `file`.
It returns the order with an Attachment in `av_scan_status: pending`; the order
stays `funded` or `correction_requested`. One pending upload per order is allowed.
The API never serializes a storage URL or storage key. Supported extensions are
PDF, UTF-8 TXT, PNG and JPEG; size defaults to 25 MiB. The extension and content
signature are checked before storage. SHA-256 records the uploaded bytes.

After commit, Celery `scan_attachment` streams the stored object to ClamAV via
INSTREAM and verifies SHA-256. UTF-8 text, image integrity and PDF structure
are also checked in the worker; encrypted PDFs are rejected. Only an explicit `stream: OK` publishes the result,
moves the order to `delivered`, notifies the customer, and starts the full review
period. Duplicate tasks do not publish twice. Storage/network failures retry
three times; failure, an unexpected scanner response, a changed hash, or infection
keeps the attachment inaccessible and sends the provider a critical email
notification. The provider can then upload a replacement. Rejected files remain
private for investigation. Beat requeues pending scans every minute, including
uploads whose initial broker message was lost.

`GET /api/attachments/{id}/download/` requires the order's customer. Anonymous
requests return 401; other users (including the provider) get 404. Until both
`av_scan_status=clean` and a post-delivery order status are present, it returns
403. Allowed order statuses: `delivered`, `under_review`, `correction_requested`,
`disputed`, `completed`, `paid_out`, `refunded`. Earlier clean result versions
remain downloadable during corrections. A successful response is:

```json
{"url": "https://s3.example/bucket/key?X-Amz-...", "expires_in": 300}
```

Responses use `Cache-Control: private, no-store`. URLs force attachment downloads
and expire after `ATTACHMENT_DOWNLOAD_TTL_SECONDS` (default 300 seconds, range 1–3600). Treat
them as bearer credentials: anyone holding a URL can use it until expiry.

## Configuration

The `attachments` storage alias is independent of public media, including the
production Google Cloud storage. Both Django and Celery need the same settings:

```dotenv
ATTACHMENT_S3_BUCKET=sabil-book-results
ATTACHMENT_S3_ENDPOINT_URL=https://s3.example.com
ATTACHMENT_S3_REGION=us-east-1
ATTACHMENT_S3_ACCESS_KEY=replace-me
ATTACHMENT_S3_SECRET_KEY=replace-me
ATTACHMENT_DOWNLOAD_TTL_SECONDS=300
ORDER_RESULT_MAX_BYTES=26214400
ORDER_RESULT_ALLOWED_EXTENSIONS=pdf,txt,png,jpg,jpeg
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
CLAMAV_TIMEOUT_SECONDS=30
```

For AWS, omit endpoint URL and credentials to use the SDK credential chain (e.g.
an IAM role). Use a dedicated private bucket. The CloudFormation template
`compose/production/s3/attachments-bucket.yml` blocks all public access, uses
private object ACLs and encryption, and requires HTTPS. Give the application role
only `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on this bucket's objects.
For MinIO, create a dedicated bucket with anonymous access set to `none`
(`mc anonymous set none ALIAS/BUCKET`); do not add public bucket policies.
Object ACL `private` alone cannot override an existing public bucket policy.
The configured endpoint must be reachable by both workers and download clients.

Local and production Compose include ClamAV on the internal network only. Run
Django, celeryworker, celerybeat and clamav. FreshClam downloads signatures on
first start; allow startup time and provision memory for ClamAV. Set clamd's
`StreamMaxLength` and `MaxFileSize` at least to `ORDER_RESULT_MAX_BYTES`; enable
`AlertExceedsMax` and `AlertEncrypted` if your daemon configuration is customized.
Do not expose clamd TCP to the internet.

Apply migrations before starting workers. Existing attachments become `pending`,
never implicitly clean. Their old objects are not moved by a schema migration:
before enabling access, move them from the old media storage into this private
bucket with the same keys, verify their hashes, and remove public copies (and
invalidate any CDN caches). Entries with only metadata or missing objects need
replacement uploads. Review processing waits while no attachment has passed scanning. Legacy already-delivered orders
must keep their existing lifecycle timestamps when scanned.

## Verification

`pytest sabil_book/offers/tests/test_attachments.py` checks access/state matrices,
queueing after commit, ClamAV protocol, hash corruption, retry exhaustion,
recovery, notifications and S3 signatures without external services.
An optional real S3-compatible test uses a temporary bucket and verifies that
unsigned GET fails, a signed GET succeeds, and the URL expires:

```sh
ATTACHMENT_TEST_S3_ENDPOINT=http://127.0.0.1:9000 \
ATTACHMENT_TEST_S3_ACCESS_KEY=test-access \
ATTACHMENT_TEST_S3_SECRET_KEY=test-secret \
uv run pytest sabil_book/offers/tests/test_attachments.py -k real_s3
```

Protocol reference: [ClamAV INSTREAM](https://docs.clamav.net/manual/Usage/ClamdProtocol.html).
Storage options: [django-storages S3](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html).
