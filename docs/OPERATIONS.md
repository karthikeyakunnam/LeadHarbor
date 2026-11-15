# Deployment and operations

## Before the first real inquiry

1. Generate a non-demo `.env` using `scripts/setup.py`; keep its permissions at `0600`.
2. Configure your own business policy and privacy contact in `config/scoring.json`.
3. Configure the model endpoint/key. A request is made only when a lead is processed; creating configuration does not contact a provider.
4. Set current per-million token rates if you need cost estimates. The daily limit counts reserved calls, including timeouts with unknown billing, and resets at UTC midnight.
5. Deploy HTTPS, verify login and the form with a synthetic inquiry, then complete `docs/ACCEPTANCE.md`.

The service reads configuration once at startup. Restart after changing `.env` or scoring policy. Do not run demo mode on `0.0.0.0`; it intentionally refuses this configuration.

## HTTPS and access

Set `DOMAIN`, `PUBLIC_URL=https://your-domain`, `SECURE_COOKIES=true`, and non-demo credentials. Point DNS to your server and allow inbound 80/443 for Caddy. Run:

```sh
docker compose --profile https up --build -d
```

Caddy obtains certificates and proxies the app. Preserve its data volume for renewal state. If terminating TLS elsewhere, forward the same origin unchanged and preserve the secure-cookie setting. `PUBLIC_URL` must match the browser origin exactly; a mismatch intentionally blocks login/mutations.

The app serves an unauthenticated contact form and a password-protected admin API on the same origin. HTML/CSS/JS are public assets and contain no secrets or lead data. Sessions expire after eight hours and are revoked on logout. There is one shared team account; individual user accounts, SSO, role-based access, and multi-tenancy are outside this version.

The Docker n8n profile publishes its editor on loopback only. Access it locally or through an SSH tunnel. Configure the n8n owner account in its UI. Exposing n8n forms to the public requires your own HTTPS n8n proxy and appropriate editor access restrictions; the bundled Caddyfile publishes only LeadHarbor.

## Network protections

The website fetcher validates all DNS answers and pins the socket to an approved address. It revalidates redirect destinations, never downgrades HTTPS, and rejects private, loopback, link-local, reserved and multicast destinations. Ports are restricted to 80/443 and must match the scheme. Responses are capped at 256 KiB, up to three redirects, with a 25-second fetch deadline, 4-second DNS wait, and bounded socket waits.

Apply outbound firewall/network rules as a second layer. Block private networks and metadata services from the app's website-fetch path while permitting the specific database/automation/provider connections you operate. Do not expose Docker's socket or mount host secrets into the app.

Administrator-configured AI/webhook endpoints are a separate trust boundary. They can intentionally access local development models or the internal n8n service. Only trusted operators should edit this configuration. Neither endpoint follows redirects or uses ambient proxy settings.

Limits: 16 KiB request body, 6,000-character message, 20 capture attempts per client address per 10 minutes, 1,000 capture attempts per rolling 24-hour window, and five login attempts per address per 15 minutes. Rejected/duplicate attempts count. The service does not trust forwarded client-IP headers: behind a reverse proxy these per-address limits conservatively apply to the proxy as a whole. Adjust limits in `server.py` or enforce individual-client limits at your trusted edge before running a high-traffic form. Do not simply trust arbitrary `X-Forwarded-For` input.

## Monitoring

`GET /healthz` checks database access and, when enabled, worker liveness/heartbeat. It returns 503 if the worker is unavailable. Configure an independent uptime monitor against this endpoint; a database failure cannot reliably report itself into that same database.

The dashboard's Recovery view shows failed enrichment, missing provider configuration, failed event delivery, and reports from n8n. Server logs include job IDs and error categories; they intentionally omit form fields, authorization headers, model outputs, and request query strings. Provider error bodies are not copied into logs.

Use `docker compose logs --tail=100 app` and the n8n Executions view for diagnosis. Log rotation is configured for container logs. Keep host clock synchronization enabled because queue leases and follow-up dates use UTC epoch timestamps. The dashboard displays dates in the viewer's local timezone.

## Recovery

Lead states: `pending → processing → ready / needs_review`. Temporary failures become `retry`, then `failed` after the configured attempt budget. Missing/unauthorized model configuration becomes `needs_configuration`. A worker claims a lead for 180 seconds; expired claims are recovered, and stale workers cannot commit results over a replacement claim.

Fix the underlying issue, then use **Retry enrichment**. Manual retries reset the attempt budget and may incur a new model charge. Assignment, notes, explicit follow-up dates and sales stage are preserved. Model outputs and website cache are replaced only when a new enrichment succeeds. An approved reply remains the text a human approved; it is never sent or silently rewritten.

Notification outbox events have their own five-attempt delivery budget. Failed events can be retried in Recovery. Delivery is **at least once**; the receiver must deduplicate using `event_id`/`Idempotency-Key` if duplicate effects are unacceptable. A received HTTP 2xx means the receiver accepted the request, not necessarily that a downstream human read a message.

## Backup

Native Python deployment:

```sh
python3 scripts/backup.py backups/leadharbor-2026-11-15.sqlite3
```

Container deployment:

```sh
mkdir -p backups
docker compose exec app python scripts/backup.py /app/data/backup.sqlite3
docker compose cp app:/app/data/backup.sqlite3 backups/leadharbor-2026-11-15.sqlite3
```

The backup helper refuses to overwrite an existing filename. Choose a new in-container name for each backup. It uses SQLite's online backup API, includes committed WAL data, and verifies integrity. Keep backups encrypted off-host with restricted permissions; also back up `.env`, scoring policy, and n8n's volume/encryption key privately. Do not commit them.

## Restore

Stop the application before replacing its database. Verify the backup with `PRAGMA integrity_check`. For a native deployment, move the old database and matching `-wal`/`-shm` files into a private rollback directory, copy the backup into `DATABASE_PATH`, restore file ownership/permissions, and restart. For Docker, do the same inside the persistent volume while app containers are stopped, using a one-off trusted maintenance container. Never restore only the main file while another process is writing to it.

Rotate session/API secrets after a disaster recovery involving a potentially exposed backup. Existing signed sessions become invalid when `SESSION_SECRET` changes. Test receipt, enrichment, and a retry after restoration.

## Retention and deletion

Completed/failed/configuration-blocked records older than `RETENTION_DAYS` are pruned hourly. Unfinished queued/processing records are retained for recovery rather than silently erased. Deleting a lead removes its evidence, usage records, audit history and queued events; a contact is deleted only when its last inquiry is removed. Metrics cover retained records and change after deletion/pruning.

Public-page cache expires after 24 hours. n8n successful execution payloads are not saved by default; error execution data is pruned after seven days. Backups, model-provider retention, and any external CRM/notification copies have independent lifecycles; delete them through those systems as needed. LeadHarbor cannot retract an already delivered event or remote provider request.

## Capacity and upgrades

Run one app instance with a local persistent filesystem. SQLite WAL is appropriate for small-team workloads; do not share its database over NFS or across replicas. The embedded HTTP server caps concurrent request threads at 32, limits request size and applies application rate limits; put a maintained reverse proxy in front of it. For high traffic, migrate to a production application server and PostgreSQL-backed job queue, then load-test your target workload.

Before upgrading, create and test a backup. Verify the official n8n security/release notes and update the pinned image deliberately. Current schema version is 1; startup creates missing tables but does not claim to migrate arbitrary older schemas. Future schema changes require explicit migrations and rollback instructions. Regenerate exports with `python3 scripts/build_workflows.py`, run all checks, and test the imported workflows on the exact n8n release you will deploy.
