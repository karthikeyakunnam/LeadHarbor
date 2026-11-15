import json
import logging
import threading
import time

from .db import Conflict
from .enrichment import FetchError, ProviderError, PROMPT_VERSION, ai_payload, fetch_website, parse_ai, post_json, qualify
from .security import ValidationError

log = logging.getLogger('leadharbor.worker')


class Worker:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.stop = threading.Event()
        self.thread = None
        self.last_tick = time.time()

    def process_one(self):
        lead = self.store.claim(self.settings.max_attempts)
        if not lead:
            return False
        call_id = None
        try:
            if self.settings.demo:
                website = {'website_fetched': False, 'fetch_status': 'demo_no_network', 'source_url': '', 'text': ''}
                ai = {'company_summary': 'Unknown', 'industry': 'Unknown', 'company_size_guess': 'unknown',
                      'fit_score_1_10': 7, 'why': 'Synthetic demo qualification. Configure an AI provider for real analysis.',
                      'suggested_reply': f"Hi {lead['full_name'].split()[0]}, thanks for reaching out. Could we arrange a short call to discuss your project?",
                      'confidence': 'medium', 'evidence': [{'source': 'message', 'quote': lead['message'][:150]}] if len(lead['message']) >= 8 else []}
                ai = parse_ai(ai, lead, website)
            else:
                if not self.settings.llm_api_key:
                    raise ProviderError('LLM_API_KEY is missing. Configure a provider and retry this lead.', configuration=True)
                website = self.store.cache_get(lead['website_url']) if lead['website_url'] else None
                if website:
                    website['cached'] = True
                else:
                    try:
                        website = fetch_website(lead['website_url'])
                        if website.get('website_fetched'):
                            self.store.cache_set(lead['website_url'], website)
                    except FetchError as exc:
                        website = {'website_fetched': False, 'fetch_status': exc.code, 'text': '', 'source_url': lead['website_url']}
                call_id = self.store.reserve_call(lead['id'], self.settings.llm_model,
                                                int(self.settings.rules.get('max_daily_ai_calls', 500)))
                try:
                    response = post_json(self.settings.llm_base_url + '/chat/completions', ai_payload(lead, website, self.settings), self.settings.llm_api_key)
                    if not isinstance(response, dict):
                        raise ProviderError('provider_invalid_response')
                    usage = response.get('usage') or {}
                    self.store.record_call(call_id, usage if isinstance(usage, dict) else {}, self.settings)
                except ProviderError:
                    self.store.record_call(call_id, {}, self.settings, 'failed_or_unknown')
                    raise
                try:
                    content = response['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError):
                    raise ValidationError('AI response is missing message content.') from None
                ai = parse_ai(content, lead, website)
            score, tier, owner, hours, adjustments = qualify(ai, lead, self.settings.rules)
            enrichment = {**ai, 'website': website, 'model': 'synthetic-demo' if self.settings.demo else self.settings.llm_model,
                          'prompt_version': PROMPT_VERSION, 'enriched_at': time.time(), 'adjustments': adjustments,
                          'ai_score': ai['fit_score_1_10'], 'demo': self.settings.demo}
            self.store.finish(lead, enrichment, score, tier, owner, hours)
            log.info('Enriched lead %s (%s)', lead['id'], tier)
        except ProviderError as exc:
            self.store.fail(lead, str(exc), self.settings.max_attempts, permanent=exc.permanent, configuration=exc.configuration)
        except Conflict as exc:
            self.store.fail(lead, str(exc), self.settings.max_attempts, configuration=True)
        except ValidationError as exc:
            # Malformed generations are retried within the same bounded job budget.
            self.store.fail(lead, str(exc), self.settings.max_attempts)
        except Exception:
            log.exception('Unexpected enrichment failure for lead %s', lead['id'])
            self.store.fail(lead, 'Unexpected processing failure; see server logs.', self.settings.max_attempts)
        return True

    def deliver_one(self):
        if not self.settings.webhook_url or self.settings.demo:
            return False
        event = self.store.claim_event()
        if not event:
            return False
        try:
            post_json(self.settings.webhook_url, json.loads(event['payload']), self.settings.webhook_token,
                      timeout=15, extra_headers={'Idempotency-Key': event['id']}, expect_json=False)
            self.store.complete_event(event)
        except ProviderError as exc:
            self.store.complete_event(event, str(exc))
        return True

    def run(self):
        last_prune = 0
        while not self.stop.is_set():
            self.last_tick = time.time()
            try:
                if self.last_tick - last_prune > 3600:
                    self.store.prune(self.settings.retention_days)
                    last_prune = self.last_tick
                worked = self.process_one()
                sent = self.deliver_one()
                if not worked and not sent:
                    self.stop.wait(1)
            except Exception:
                log.exception('Worker iteration failed; durable jobs will be reclaimed.')
                self.stop.wait(2)

    def start(self):
        self.thread = threading.Thread(target=self.run, name='leadharbor-worker', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=60)
