# n8n integration

## Capture workflow

1. Start a real LeadHarbor instance and the optional n8n Compose profile, or use your existing n8n deployment.
2. Import `workflow.json`.
3. On **Save submission durably**, select an HTTP Header Auth credential with name `Authorization` and value `Bearer <INGEST_TOKEN>` from `.env`.
4. Use `http://app:8080/api/leads` inside Compose. For n8n on the host, use the accessible app address; for n8n Cloud, use your public HTTPS origin.
5. Enable the `crypto` builtin for the Code node. Compose already sets `NODE_FUNCTION_ALLOW_BUILTIN=crypto`; external task-runner deployments must allow it in the runner configuration as well.
6. Customize the form description with your business contact, real provider-processing notice and actual retention period. The primary built-in app form reads current retention and privacy contact from configuration.
7. Import `workflows/error-handler.json`, configure its ingestion Header Auth credential and URL, and select it as the capture workflow's **Error workflow**.
8. Test with a synthetic inquiry, then publish the capture workflow and open its production form URL.

No Data Table needs to be created. LeadHarbor stores contacts, inquiries, jobs, audit records, usage and delivery events transactionally.

The submission key is a SHA-256 hash of the original `submittedAt` and form payload. The same original event replay has the same key, including retries using saved execution data. A new form submission timestamp represents a separate inquiry, even if the email/message match. HTTP retries happen before receipt; failure never displays a false success page.

The built-in app form is the most fully styled and directly validated intake path. An invalid n8n form submission may stop at the API node and appear as an n8n execution error. Correct input validation on custom form providers before ingestion, and retain the original submission for correction/replay.

## SSRF settings

Use a supported, patched n8n version. Compose pins 2.47.7, enables `N8N_SSRF_PROTECTION_ENABLED=true`, and narrowly allows the internally controlled `app` hostname so its ingestion node can connect to the private service. Do not add wildcard public-host allowlists. This exception is only for the configured internal capture endpoint; company websites are fetched by LeadHarbor's separately restricted fetcher.

The original README referenced n8n 2.8.x. Built-in SSRF protection is documented from 2.12.0; consult the official documentation for your installed version:

- [n8n SSRF protection](https://docs.n8n.io/hosting/securing/ssrf-protection/)
- [Code-node module allowlists](https://docs.n8n.io/hosting/configuration/environment-variables/nodes/)
- [n8n error workflows](https://docs.n8n.io/flow-logic/error-handling/)
- [Official releases](https://github.com/n8n-io/n8n/releases)

## Optional notifications

1. Import `workflows/notifications.json`.
2. On its Webhook node, add a Header Auth credential: `Authorization` / `Bearer <EVENT_WEBHOOK_TOKEN>`.
3. Choose a notification branch, configure your Slack channel or team-email credentials, and enable it. Neither branch sends the prospect's first reply.
4. Publish the workflow, then set `EVENT_WEBHOOK_URL` to its production URL and restart the app. In Compose this can be `http://n8n:5678/webhook/leadharbor-events`.
5. Make the receiver's business effect idempotent by event ID when required. The supplied Slack/SMTP branches do **not** guarantee duplicate-free delivery.

The webhook waits until its workflow completes before returning a success status. Leave failures visible to the caller so the app's outbox can retry them. Do not configure notification errors to silently continue as successes. If enabling multiple destinations, isolate them into destination-specific workflows/queues: otherwise a retry after a partial failure can repeat the successful branch.

To send only hot-lead alerts, insert an IF node after **Format event** checking `type == lead.qualified` and `tier == hot`; keep `lead.failed` on a separate operational-alert path. Qualification tier and owner are already calculated in the app.

## External CRM integration

LeadHarbor is the built-in CRM and audit source. The same authenticated event webhook can drive HubSpot, Pipedrive, Sheets or Airtable. Fetch full inquiry details from `GET /api/leads/{lead_id}` using a separate admin Header Auth credential, then map only the fields your destination needs. For HubSpot, upsert contacts using email and store the inquiry ID in your own deal/activity identifier. For Sheets/Airtable, deduplicate on the inquiry/event ID before appending.

A contact and an inquiry are different entities: do not overwrite earlier messages just because a contact's email matches. Provider credentials, custom CRM property IDs, tenant-specific fields and destination URLs must come from your own accounts. They are deliberately absent from these exports.

## Error workflow limitations

Error Trigger is for failed production executions, not manual test runs. It records safe metadata into LeadHarbor's Recovery view. If the app is unavailable, that report will fail too; independent uptime monitoring and the n8n execution log remain necessary. Saving failed execution payloads may retain personal data, so limit access and keep execution pruning enabled.

These JSON exports and their JavaScript are checked automatically. A live import/execution on your installed n8n version is a separate acceptance step; no credentials, published workflows or external sends were created during implementation.
