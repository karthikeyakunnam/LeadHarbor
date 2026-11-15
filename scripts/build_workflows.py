#!/usr/bin/env python3
"""Generate portable n8n workflow exports without credentials or private data."""
from pathlib import Path
import json
import uuid
ROOT = Path(__file__).resolve().parent.parent


def node(name, kind, parameters, x, y, version=1, **extra):
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'leadharbor:' + name)), 'name': name,
            'type': kind, 'typeVersion': version, 'position': [x, y], 'parameters': parameters, **extra}


def connect(*names):
    return {a: {'main': [[{'node': b, 'type': 'main', 'index': 0}]]} for a, b in zip(names, names[1:])}


def save(path, name, nodes, connections):
    value = {'name': name, 'nodes': nodes, 'connections': connections,
             'settings': {'executionOrder': 'v1', 'executionTimeout': 60,
                          'saveDataSuccessExecution': 'none', 'saveDataErrorExecution': 'all',
                          'saveManualExecutions': False}, 'pinData': {}, 'meta': {'templateCredsSetupCompleted': False}}
    (ROOT / path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def main():
    form = node('Lead form', 'n8n-nodes-base.formTrigger', {
        'formTitle': 'Let’s talk · LeadHarbor',
        'formDescription': 'Tell us about your project. Your message is saved before enrichment starts. We process your message and public company website with our configured AI provider to prepare a reply. Records are retained for 90 days by default; contact our team for access or deletion. Replace this notice with your business contact and configured retention before publishing.',
        'formFields': {'values': [
            {'fieldLabel': 'Full name', 'requiredField': True, 'placeholder': 'Jane Doe'},
            {'fieldLabel': 'Work email', 'fieldType': 'email', 'requiredField': True},
            {'fieldLabel': 'Company website', 'placeholder': 'company.com (optional)'},
            {'fieldLabel': 'Message', 'fieldType': 'textarea', 'requiredField': True},
            {'fieldLabel': 'Privacy acknowledgment', 'fieldType': 'dropdown', 'requiredField': True,
             'fieldOptions': {'values': [{'option': 'I acknowledge the privacy notice and agree to processing for this inquiry.'}]}}
        ]}, 'options': {'path': 'leadharbor', 'buttonLabel': 'Send your message'}
    }, 0, 240, 2.5, webhookId='leadharbor-form')
    prepare = node('Prepare durable submission', 'n8n-nodes-base.code', {'jsCode': """// Enable only the crypto builtin (included in compose.yml). No external modules required.
const { createHash } = require('crypto');
const form = $input.first().json;
if (!form.submittedAt) throw new Error('Missing original submission timestamp; refuse an unstable retry key.');
const payload = {
  full_name: form['Full name'], email: form['Work email'],
  website_url: form['Company website'] || '', message: form['Message'],
  consent: form['Privacy acknowledgment'] === 'I acknowledge the privacy notice and agree to processing for this inquiry.',
};
if (!payload.consent) throw new Error('Privacy acknowledgment is required.');
const key = createHash('sha256').update(JSON.stringify([form.submittedAt, payload])).digest('hex');
return [{json: {payload, submission_key: 'n8n-' + key}}];"""}, 260, 240, 2)
    capture = node('Save submission durably', 'n8n-nodes-base.httpRequest', {
        'method': 'POST', 'url': 'http://app:8080/api/leads',
        'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth',
        'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'Idempotency-Key', 'value': '={{ $json.submission_key }}'}]},
        'sendBody': True, 'specifyBody': 'json', 'jsonBody': '={{ JSON.stringify($json.payload) }}',
        'options': {'timeout': 10000, 'redirect': {'redirect': {'followRedirects': False}},
                    'response': {'response': {'responseFormat': 'json'}}}
    }, 520, 240, 4.4, retryOnFail=True, maxTries=3, waitBetweenTries=1500)
    thank = node('Confirm receipt', 'n8n-nodes-base.form', {'operation': 'completion', 'respondWith': 'text',
        'completionTitle': 'Thanks for saying hello!', 'completionMessage': 'Your message has been saved. Our team will review it and get back to you.',
        'options': {'formTitle': 'Message received · LeadHarbor'}}, 800, 240, 2.5, webhookId='leadharbor-thanks')
    note = node('Setup & architecture', 'n8n-nodes-base.stickyNote', {'content': '## LeadHarbor · Save first, enrich in the background\n1. Start the app service.\n2. Set the HTTP Header Auth credential on Save submission durably: Authorization = Bearer <INGEST_TOKEN>.\n3. Docker uses http://app:8080. For n8n Cloud, use your HTTPS app origin.\n4. Customize the privacy notice and publish the form.\n5. Set this workflow’s Error workflow to the imported LeadHarbor error handler.\n\nThe durable service handles validation, idempotency, safe website fetching, strict AI output, retries, CRM, and the dashboard. Receipt is confirmed only after the database commits.\n\nNo credentials are embedded. See docs/N8N.md. For Docker, the internal app hostname is narrowly allowlisted for n8n SSRF protection.', 'width': 1100, 'height': 270}, 0, -100)
    save('workflow.json', 'LeadHarbor · Durable lead capture', [form, prepare, capture, thank, note],
         connect('Lead form', 'Prepare durable submission', 'Save submission durably', 'Confirm receipt'))
    webhook = node('LeadHarbor events', 'n8n-nodes-base.webhook', {'httpMethod': 'POST', 'path': 'leadharbor-events', 'authentication': 'headerAuth',
                   'responseMode': 'lastNode', 'options': {}}, 0, 200, 2.1, webhookId='leadharbor-events')
    format_node = node('Format event', 'n8n-nodes-base.code', {'jsCode': """const event = $input.first().json.body;
if (!event || typeof event.event_id !== 'string' || !['lead.qualified', 'lead.failed'].includes(event.type)) throw new Error('Invalid LeadHarbor event');
// Do not interpolate lead text into HTML or Slack mentions. Plain text only.
const safe = value => String(value || '').replace(/[<>&]/g, ' ').slice(0, 2000);
const text = event.type === 'lead.failed'
 ? `Lead processing needs attention. Lead: ${safe(event.lead_id)}. Issue: ${safe(event.error)}`
 : `New ${safe(event.tier)} lead: ${safe(event.full_name)} (${safe(event.email)})\\nFit: ${event.score}/10. Owner: ${safe(event.owner)}\\n${safe(event.summary)}\\nEvent ID: ${safe(event.event_id)}`;
return [{json:{...event, notification_text:text}}];"""}, 260, 200, 2)
    slack = node('Slack alert (enable after setup)', 'n8n-nodes-base.slack', {'resource': 'message', 'operation': 'post', 'select': 'channel',
                 'channelId': {'__rl': True, 'mode': 'name', 'value': '#leads'}, 'text': '={{ $json.notification_text }}',
                 'otherOptions': {'mrkdwn': False}}, 560, 150, 2.4, disabled=True)
    email = node('Team email (enable after setup)', 'n8n-nodes-base.emailSend', {'fromEmail': 'leads@yourcompany.com', 'toEmail': 'sales@yourcompany.com',
                 'subject': '=LeadHarbor: {{ $json.type }}', 'emailFormat': 'text', 'text': '={{ $json.notification_text }}', 'options': {}}, 560, 350, 2.1, disabled=True)
    notes = node('Notification setup', 'n8n-nodes-base.stickyNote', {'content': '## Optional team notifications\nSet the Webhook Header Auth credential: Authorization = Bearer <EVENT_WEBHOOK_TOKEN>.\nPublish before setting EVENT_WEBHOOK_URL in the app.\nChoose ONE notification branch or wire independent subworkflows. Nodes are disabled until you add credentials. Delivery errors must fail this workflow so the outbox retries.\n\nDelivery is at least once. event_id / Idempotency-Key is stable across retries. This starter does NOT promise exactly-once Slack/email delivery; add receiver-side deduplication for your destination. A crash after a send can cause a duplicate alert.\n\nNever send the suggested first reply automatically. Approval and sending are separate human actions.', 'width': 900, 'height': 270}, 0, -150)
    save('workflows/notifications.json', 'LeadHarbor · Optional team notifications', [webhook, format_node, slack, email, notes],
         {**connect('LeadHarbor events','Format event'), 'Format event': {'main': [[{'node':slack['name'],'type':'main','index':0},{'node':email['name'],'type':'main','index':0}]]}})
    trigger = node('Workflow failure', 'n8n-nodes-base.errorTrigger', {}, 0, 200)
    report = node('Record automation failure', 'n8n-nodes-base.httpRequest', {'method': 'POST', 'url': 'http://app:8080/api/automation-errors',
                  'authentication': 'genericCredentialType', 'genericAuthType': 'httpHeaderAuth', 'sendBody': True, 'specifyBody': 'json',
                  'jsonBody': '={{ JSON.stringify({workflow: $json.workflow?.name || "Unknown", execution_id: String($json.execution?.id || "trigger"), message: "n8n execution failed; inspect its execution log."}) }}',
                  'options': {'timeout': 10000}}, 280, 200, 4.4, retryOnFail=True, maxTries=3, waitBetweenTries=1500)
    note = node('Error handler setup', 'n8n-nodes-base.stickyNote', {'content': '## Persist automation failures\nAdd the same ingestion Header Auth credential to the HTTP node. Select this workflow as the Error workflow for your capture workflow.\nThe app exposes these events in Recovery. No raw lead data or model output is sent in the error report.\nIf the app itself is unavailable, use independent uptime monitoring and inspect n8n Executions. Error Trigger does not run for manual test executions.', 'width': 700, 'height': 230}, 0, -100)
    save('workflows/error-handler.json', 'LeadHarbor · Automation error handler', [trigger, report, note], connect('Workflow failure','Record automation failure'))


if __name__ == '__main__':
    (ROOT / 'workflows').mkdir(exist_ok=True)
    main()
