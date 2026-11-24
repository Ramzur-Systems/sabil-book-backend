# Private result attachments

Results a provider delivers are the paid product of an order. They are stored in
a private SeaweedFS bucket, never served publicly, scanned before anyone can
see them, kept downloadable after the order completes, and deleted a retention
period later.

## Upload and scanning

`POST /api/orders/{id}/attachments/` accepts a provider's multipart `file`.
It returns the order with an Attachment in `av_scan_status: pending`; the order
stays `funded` or `correction_requested`. One pending upload per order is allowed.
The API never serializes a storage URL or storage key. Supported extensions are
PDF, UTF-8 TXT, PNG and JPEG; size defaults to 25 MiB. The extension and content
signature are checked before storage. SHA-256 records the uploaded bytes.

After commit, Celery `scan_attachment` streams the stored object to ClamAV via
INSTREAM and verifies SHA-256. UTF-8 text, image integrity and PDF structure
are also checked in the worker; encrypted PDFs are rejected. Only an explicit
`stream: OK` publishes the result, moves the order to `delivered`, notifies the
customer, and starts the full review period. Duplicate tasks do not publish twice.

- **Infected, damaged, changed hash or unexpected scanner reply:** the
  attachment becomes `infected` or `error`, the provider gets a critical email,
  and can upload a replacement. The stored object is deleted once that verdict
  commits; `storage_key` keeps the original key for auditing.
- **Scanner or storage outage:** the task retries three times, then leaves the
  attachment `pending`. Beat (`recover_attachment_scans`, every minute)
  re-queues pending scans idle for `ATTACHMENT_SCAN_RECOVERY_GRACE_SECONDS`
  (default 10 minutes), at most once per grace period per attachment, which
  also recovers lost broker messages. Only after
  `ATTACHMENT_SCAN_MAX_PENDING_SECONDS` (default 24 hours) does it fail closed
  as `error` and notify the provider.

## Download

`GET /api/attachments/{id}/download/` requires the order's customer. Anonymous
requests return 401; other users (including the provider) get 404. Until both
`av_scan_status=clean` and a post-delivery order status are present, it returns
403. Allowed order statuses: `delivered`, `under_review`, `correction_requested`,
`disputed`, `completed`, `paid_out`, `refunded`. Earlier clean result versions
remain downloadable during corrections. A successful response is:

```json
{"url": "https://files.example.com/sabil-book-results/results/1/....pdf?X-Amz-...", "expires_in": 300}
```

Responses use `Cache-Control: private, no-store`. URLs force a download named
`result.<ext>` and expire after `ATTACHMENT_DOWNLOAD_TTL_SECONDS` (default 300
seconds, range 1–3600). Treat them as bearer credentials: anyone holding a URL
can use it until expiry. Changing the host, filename or any signed parameter
invalidates the signature.

## Retention

Customers keep access after the order completes. `purge_expired_attachments`
(hourly) deletes the attachments (rows and stored objects) of orders that have
been `completed` or `paid_out` for `ATTACHMENT_RETENTION_DAYS` (default 30)
since `completed_at`, or `refunded` since `refunded_at`. Orders, payments,
payouts and disputes are never deleted. Open orders (including `disputed`) keep
their attachments however old. Orders migrated without these timestamps are
skipped.

`sweep_orphan_attachments` (every 6 hours) deletes stored objects that no
Attachment references and that are older than `ATTACHMENT_ORPHAN_GRACE_SECONDS`
(default 1 hour). This covers uploads whose request transaction rolled back
after the object was written, and purges whose object delete failed.

## Storage: SeaweedFS

Both Compose files run one SeaweedFS node (`compose/production/seaweedfs/`,
image `chrislusf/seaweedfs:4.53`, pinned). Its start script:

- Accepts S3 requests only from one identity, built from
  `ATTACHMENT_S3_ACCESS_KEY`/`ATTACHMENT_S3_SECRET_KEY`. That key may only
  read, write/delete and list `ATTACHMENT_S3_BUCKET`. It cannot create or
  delete buckets, touch other buckets, or set ACLs (so it cannot make objects
  public). There is no admin identity. Anonymous requests get 403.
- Creates the bucket on start (an existing bucket is fine) and reports healthy
  only afterwards; Django and Celery wait for that.
- Exposes only the S3 API (port 8333) to other containers. Master, volume and
  filer (whose HTTP API has no authentication) listen on loopback inside the
  container.
- Encrypts stored chunks (`-filer.encryptVolumeData`). The keys live in the
  filer metadata on the same volume, so this protects copies of volume files,
  not a copy of the whole data volume. Protect the volume and its backups.

The storage class sends no ACL header (objects are private by default) and
sets `Cache-Control: private, no-store` on each object.

Django and Celery reach SeaweedFS at `ATTACHMENT_S3_ENDPOINT_URL`
(`http://seaweedfs:8333`). Browsers cannot resolve that host, and SigV4 signs
the host, so download URLs are signed for `ATTACHMENT_S3_PUBLIC_ENDPOINT_URL`
instead. Uploads never use the public host.

### Local

`.envs/.local/.attachments` holds development-only values shared by Django,
Celery and SeaweedFS. The S3 API is published on `127.0.0.1:8333` only, and
signed URLs point to `http://localhost:8333`.

### Production

On the production server, from the repository root, generate
`.envs/.production/.attachments` (not committed, mode 600) with a random key:

```sh
./compose/production/seaweedfs/create-env https://files.yourdomain.com
```

It never overwrites an existing file. The result looks like:

```dotenv
ATTACHMENT_S3_BUCKET=sabil-book-results
ATTACHMENT_S3_ENDPOINT_URL=http://seaweedfs:8333
ATTACHMENT_S3_PUBLIC_ENDPOINT_URL=https://files.yourdomain.com
ATTACHMENT_S3_REGION=us-east-1
ATTACHMENT_S3_ACCESS_KEY=sabil-book-<16 hex>
ATTACHMENT_S3_SECRET_KEY=<64 hex>
```

Django, Celery and SeaweedFS all read this file (and
`merge_production_dotenvs_in_dotenv.py` includes it in `.env`), so rotating the
key means editing it and restarting `seaweedfs`, `django`, `celeryworker` and
`celerybeat` together.

Traefik serves `files.example.com` (replace it with the real domain in
`compose/production/traefik/traefik.yml`, matching
`ATTACHMENT_S3_PUBLIC_ENDPOINT_URL`, and point DNS at the server) over
HTTPS and lets only `GET` and `HEAD` through; uploads stay on the internal
network. Traefik passes the `Host` header through unchanged, which signature
checks depend on. SeaweedFS publishes no ports of its own.

Other settings shared by Django and Celery:

```dotenv
ATTACHMENT_DOWNLOAD_TTL_SECONDS=300
ATTACHMENT_RETENTION_DAYS=30
ORDER_RESULT_MAX_BYTES=26214400
ORDER_RESULT_ALLOWED_EXTENSIONS=pdf,txt,png,jpg,jpeg
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
CLAMAV_TIMEOUT_SECONDS=30
```

### Backups

`backup_attachments` exports every stored object through the S3 API into a
dated `attachments_<timestamp>.tar.gz` (mode 600) and deletes archives older
than `ATTACHMENT_RETENTION_DAYS`, so purged results do not outlive the
retention period in backups. Objects are never modified after upload, so no
downtime is needed. A failed run leaves no partial archive behind.

In production the on-demand `attachments-backup` service (profile
`maintenance`, never started by `up`) writes into the
`production_attachment_backups` volume. Schedule it daily from the host's
crontab, for example:

```cron
30 3 * * * cd /path/to/sabil-book-backend && docker compose -f docker-compose.production.yml run --rm attachments-backup >> /var/log/sabil-book-attachments-backup.log 2>&1
```

Archives contain the results unencrypted. That volume lives on the same host,
so also copy the archives off the server (encrypted, kept no longer than the
retention period) to survive losing the host.

To restore, restore the database first, then:

```sh
docker compose -f docker-compose.production.yml run --rm attachments-backup \
  python manage.py restore_attachments /backups/attachments/attachments_<timestamp>.tar.gz
```

Only objects an Attachment row still references are restored (under their
original keys, with the same metadata); existing objects are skipped, so it is
safe to rerun. Archive entries outside `results/` abort the restore. Test a
restore before relying on it.

## ClamAV

Local and production Compose include ClamAV on the internal network only. Run
Django, celeryworker, celerybeat and clamav. FreshClam downloads signatures on
first start; allow startup time and provision memory for ClamAV. Set clamd's
`StreamMaxLength` and `MaxFileSize` at least to `ORDER_RESULT_MAX_BYTES`; enable
`AlertExceedsMax` and `AlertEncrypted` if your daemon configuration is customized.
Do not expose clamd TCP to the internet.

## Migrating existing attachments

Apply migrations before starting workers. Migration `offers.0012` marks
pre-existing attachments without a stored file as `clean` (noted in
`av_scan_error`) so they no longer block their orders. They are not
downloadable, because there is no object to serve. Attachments that do have a
stored file stay `pending` and are scanned normally. Objects from the old
public media storage are not moved automatically: to keep them, copy them into
this bucket with the same keys, verify their hashes, and remove the public
copies (and invalidate any CDN caches).

## Verification

`pytest sabil_book/offers/tests/test_attachments.py` checks access/state
matrices, queueing after commit, ClamAV protocol, hash corruption, retries and
recovery, rejection cleanup, retention, orphan sweeping, notifications and S3
signatures without external services.

An optional test runs against a real S3-compatible service. Against local
SeaweedFS (`docker compose -f docker-compose.local.yml up -d seaweedfs`):

```sh
ATTACHMENT_TEST_S3_ENDPOINT=http://127.0.0.1:8333 \
ATTACHMENT_TEST_S3_ACCESS_KEY=local-dev-attachments \
ATTACHMENT_TEST_S3_SECRET_KEY=local-dev-attachments-secret-change-me \
ATTACHMENT_TEST_S3_BUCKET=sabil-book-results \
uv run pytest sabil_book/offers/tests/test_attachments.py -k real_s3
```

It verifies the `Cache-Control` metadata, listing, that unsigned and tampered
URLs get 403, that a signed URL downloads with the right filename and then
expires, and deletion. Without `ATTACHMENT_TEST_S3_BUCKET` it creates and
removes a temporary bucket, which needs a key allowed to create buckets.

Protocol reference: [ClamAV INSTREAM](https://docs.clamav.net/manual/Usage/ClamdProtocol.html).
Storage options: [django-storages S3](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html).
SeaweedFS S3: [S3 configuration](https://github.com/seaweedfs/seaweedfs/wiki/S3-Configuration).
