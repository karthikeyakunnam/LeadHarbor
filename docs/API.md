# API reference

All JSON bodies must be objects, use `Content-Type: application/json`, and fit within 16 KiB. Errors have the shape `{"error":"message"}`. Timestamps are UTC epoch seconds. The API does not enable CORS.

## Public form

`POST /api/submit` requires an `Idempotency-Key` header (8–128 ASCII letters, digits, dots, underscores, or hyphens).

```json
{
  "full_name": "Jane Doe",
  "email": "jane@example.com",
  "website_url": "https://example.com",
  "message": "We would like to discuss an automation project.",
  "consent": true,
  "company_fax": ""
}
```

Returns 202 for a newly committed submission or 200 for an exact replay:

```json
{"id":"submission-uuid","received":true,"duplicate":false}
```

A key reused with different normalized content returns 409. Reuse the same key and content after an uncertain network failure. Generate a new key for a separate inquiry. Repeated email addresses identify the same contact but do not merge inquiry messages. The browser form keeps retry content/key in tab session storage and clears it after receipt.

Validation errors return 422, unsupported media type 415, oversized bodies 413, and rate limits 429. Company URLs must be public HTTP(S) destinations; blank websites fall back to non-free-mail email domains. Invalid/unusable fallback domains are skipped.

`GET /api/public-config` exposes only display/privacy settings. `GET /healthz` is unauthenticated and returns no lead details.

## n8n ingestion

`POST /api/leads` accepts the same payload/key but requires `Authorization: Bearer <INGEST_TOKEN>`. It cannot use the admin token. It applies the same consent, validation, and submission limits.

`POST /api/automation-errors` also requires the ingestion token:

```json
{"workflow":"LeadHarbor capture","execution_id":"123","message":"Execution failed; inspect n8n."}
```

Workflow name/execution ID pairs are deduplicated. Keep raw form data and secrets out of error reports.

## Dashboard authentication

`POST /api/login` accepts `{"password":"..."}` with `Origin` equal to `PUBLIC_URL` and `X-LeadHarbor: 1`. It sets an HttpOnly, SameSite=Strict, eight-hour session cookie; HTTPS deployments also require Secure cookies. `POST /api/logout` revokes that session.

Admin endpoints accept this session or `Authorization: Bearer <ADMIN_TOKEN>`. Cookie-authenticated mutations require the same Origin and X-LeadHarbor checks. Keep admin/ingestion tokens in server-side credentials only.

| Method / path | Behavior |
|---|---|
| `GET /api/config` | Non-secret provider status, model, routing rules, retention |
| `GET /api/metrics` | Retained-record counts, stages, score feedback, usage/cost |
| `GET /api/leads` | `search`, `status`, `tier`, `limit` (1–100), `offset` |
| `GET /api/leads/{id}` | Inquiry, enrichment and latest 100 audit records |
| `PATCH /api/leads/{id}` | Owner, notes, stage, follow_up_at, score_accepted |
| `POST /api/leads/{id}/retry` | Queue eligible failed/review/configuration-blocked inquiry; body `{}` |
| `POST /api/leads/{id}/approve` | Save `{"reply":"reviewed text"}` for manual sending |
| `DELETE /api/leads/{id}` | Permanently remove this inquiry and dependent local data |
| `GET /api/events` | Latest 100 delivery events and n8n automation failures |
| `POST /api/events/{id}/retry` | Requeue a failed delivery; body `{}` |
| `GET /api/export` | CSV, maximum 10,000 most recent inquiries; larger exports use paginated API |

Stages: `new`, `contacted`, `qualified`, `won`, `lost`. `score_accepted` is true/false/null. `follow_up_at` is epoch seconds or null. Model scores are not directly editable; configure business rules and reprocess instead. Reply approval never sends an email or queues outreach.

## Outbound events

Set `EVENT_WEBHOOK_URL` and `EVENT_WEBHOOK_TOKEN` to opt in. The worker sends authenticated POST requests with a stable `Idempotency-Key` equal to `event_id`.

- `lead.qualified`: lead ID, tier, score, owner, name, email, summary and draft.
- `lead.failed`: lead ID, terminal status and safe error category.

The receiver must return 2xx; redirects are failures and credentials are not forwarded. Five attempts are made with backoff before manual recovery. Delivery is at least once, and receiver-side deduplication is necessary for exactly-once business effects. In demo mode all delivery is disabled.
