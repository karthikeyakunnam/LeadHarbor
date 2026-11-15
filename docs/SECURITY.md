# Security design

LeadHarbor separates public intake, authenticated administration, untrusted websites/model output, and administrator-configured integrations.

| Boundary | Controls |
|---|---|
| Public form → database | Required/type/length validation, JSON-only body, no CORS, privacy acknowledgment, honeypot, body cap and request limits |
| Browser → admin | PBKDF2-SHA256 password hashes (600,000 rounds with random salt), HttpOnly SameSite=Strict cookies, eight-hour expiry, session revocation, same-origin mutation checks |
| n8n → ingestion | Separate bearer token with no read/admin privileges; stable idempotency key |
| Service → company website | Global-IP checks across every DNS answer, pinned sockets, TLS verification/SNI, redirect revalidation, downgrade rejection, restricted ports, bounded DNS waits/body/redirects/time |
| Website → AI | Script/style/navigation stripping and capped text; explicit untrusted-data instructions; model has no tools or direct send privileges |
| AI → CRM | Exact schema, strict score/type/enum/length checks, source-quote verification, unknown fallback, human review for low confidence |
| CRM → browser/export | Text escaping, no untrusted HTML injection, CSP, CSV formula protection |
| Service → provider/webhook | Only operator-configured URLs; no redirects/proxy inheritance; bounded responses; bearer secrets remain server-side |
| Worker → storage | Transactional job claims, lease-token checks, bounded attempts, durable event IDs |

Evidence verification proves that a quote occurs in supplied material; it does **not** independently verify a website's truth or guarantee the model's interpretation. Scoring remains advisory. A company website can contain hostile instructions; a prompt is not a perfect defense. Keep human review before outreach or commitments.

No API keys, customer records, session tokens, raw model output, or runtime database files belong in Git. `.gitignore`, `.dockerignore`, setup permissions, non-secret config responses, and log choices support that boundary. Operators remain responsible for filesystem access, host patching, TLS, network egress, provider contracts/retention, and secure backups.

The application has no Python package dependencies. Node dependencies are development-only and pinned by `package-lock.json`. CI does not run package lifecycle scripts. Container runtime code is copied explicitly, excluding tests and local runtime data.

## Reporting and operational limits

Use a private channel to your deployment owner for suspected vulnerabilities. Do not include secrets or real customer content in public bug reports. This version is a single-team deployment with a shared admin account; it does not implement SSO, user-level audit attribution, or per-organization data isolation.

The included Docker/Caddy configuration is a deployment starting point. Apply independent network policies and uptime monitoring, review vendor updates, and execute the acceptance checklist before accepting real data. Provider keys, public DNS, sender credentials and third-party CRM setup must be supplied by the operator.

References used during implementation:

- [Python ipaddress semantics](https://docs.python.org/3/library/ipaddress.html)
- [n8n SSRF protection and network controls](https://docs.n8n.io/hosting/securing/ssrf-protection/)
- [n8n error handling](https://docs.n8n.io/flow-logic/error-handling/)
