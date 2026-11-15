# Acceptance and verification

## Automated checks

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_workflows.py
npm ci --ignore-scripts
npm test
npm run check
```

The tests use temporary databases, synthetic inquiries, deterministic provider fixtures and a local test HTTP server. They never contact an AI provider or send external messages. DNS/IP checks use controlled records. Workflow exports are validated for connections, IDs, embedded credentials, Code-node syntax, stable submission keys and notification escaping.

## Deployment acceptance

- Start the app in a non-demo configuration, over the intended HTTPS origin.
- Open `/healthz`, confirm 200, and verify that the dashboard requires login.
- Log in; verify secure/HttpOnly/SameSite cookie behavior and logout revocation.
- Submit a synthetic inquiry. Verify a receipt corresponds to an actual stored pending record.
- Repeat the same key/content after a simulated uncertain response: confirm only one inquiry exists. Submit a separate inquiry from the same email: confirm two inquiries share one contact.
- Check invalid/private website rejection and a missing website fallback. Confirm a 403/challenge page is not treated as company evidence.
- Configure your real provider and run one authorized synthetic inquiry. Inspect evidence, unknown facts, model, confidence, token reporting and your actual provider invoice.
- Disconnect/misconfigure the provider. Confirm the inquiry survives and becomes retry/failed/configuration-blocked. Restore settings and retry.
- Stop the worker during processing, restart, and confirm the expired job is recovered without a stale worker overwrite.
- Assign an owner, change stage, save a follow-up, give score feedback, edit and approve a draft. Confirm no email was sent.
- Review mobile/desktop layouts, keyboard navigation, form validation, dialog focus and long text in a real browser.
- Import/publish the capture and error workflows on the exact n8n release in deployment. Configure credentials and validate a synthetic form submission. Manual-test errors and production Error Trigger behavior differ.
- If enabling notifications, send one synthetic event to your own test destination, test receiver authentication and failed-delivery retry, and confirm your receiver's deduplication behavior.
- Create a backup, restore it into an isolated workspace, and verify receipt, records and job recovery.
- Verify privacy copy, retention, off-host backup lifecycle, deletion procedures and access to external CRM/provider records.

## Verification record for this implementation

**54 backend/API/provider/backup/security tests and 8 dashboard DOM tests passed.** JavaScript syntax checks, deterministic workflow regeneration, documentation links, Compose configuration and exported-workflow/code checks also passed locally. The app's local HTTP health/static/API endpoints were exercised with synthetic demo data.

Real-browser visual verification could not run because the browser access host was unavailable. Docker could not build because its engine storage was full, and the installed CLI has no Compose plugin. The Compose configuration was validated successfully using an official standalone Compose binary without installing it system-wide. No existing containers/images/volumes were deleted or services stopped to resolve those host conditions. No live n8n import, public DNS/TLS deployment, paid AI request, CRM write, Slack post or email delivery was performed. These remain environment/account-dependent acceptance checks; do not treat the local test suite as evidence that those external systems were exercised.
