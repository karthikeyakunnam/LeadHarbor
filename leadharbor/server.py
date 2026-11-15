"""Same-origin dashboard and API. Run behind the included Caddy proxy for HTTPS."""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .config import ROOT, Settings
from .db import Conflict, Store
from .security import ValidationError, normalize_lead, password_matches, session_token, session_valid
from .worker import Worker

log = logging.getLogger('leadharbor.http')
STATIC = ROOT / 'leadharbor/static'
MAX_BODY = 16384


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def make_handler(store, settings, worker):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LeadHarbor'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            # Request paths and submitted fields are deliberately excluded from logs.
            log.info('%s %s', self.command, getattr(self, '_status', '-'))

        def respond(self, status, payload, content_type='application/json; charset=utf-8', headers=None):
            self._status = status
            body = json.dumps(payload, allow_nan=False).encode() if content_type.startswith('application/json') else payload
            if isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            if self.headers.get_content_type() != 'application/json':
                raise APIError(415, 'Use application/json.')
            if self.headers.get('Transfer-Encoding'):
                raise APIError(400, 'Chunked requests are not supported.')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > MAX_BODY:
                    raise APIError(413, 'Request body exceeds the 16 KiB limit or is empty.')
                data = self.rfile.read(length)
                if len(data) != length:
                    raise APIError(400, 'Incomplete request body.')
                payload = json.loads(data, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                if not isinstance(payload, dict):
                    raise ValueError()
                return payload
            except (ValueError, UnicodeError, RecursionError):
                raise APIError(400, 'Body must be a valid JSON object.') from None

        def bearer(self, expected):
            auth = self.headers.get('Authorization', '')
            return bool(expected) and hmac.compare_digest(auth, 'Bearer ' + expected)

        def cookie(self):
            try:
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                return cookie['leadharbor_session'].value if 'leadharbor_session' in cookie else ''
            except Exception:
                return ''

        def authenticated(self):
            if self.bearer(settings.admin_token):
                return True
            token = self.cookie()
            if not session_valid(token, settings.session_secret):
                return False
            with store.connect() as db:
                return bool(db.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?',
                                      (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone())

        def authorize(self):
            if not self.authenticated():
                raise APIError(401, 'Please sign in.')
            if self.command != 'GET' and not self.bearer(settings.admin_token):
                if self.headers.get('Origin') != settings.public_url or self.headers.get('X-LeadHarbor') != '1':
                    raise APIError(403, 'Same-origin request verification failed.')

        def rate(self, namespace, maximum, seconds=60):
            if not store.rate_limit(namespace + ':' + self.client_address[0], maximum, seconds):
                raise APIError(429, 'Too many requests. Please wait and try again.')

        def dispatch(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if self.command == 'GET' and path == '/healthz':
                with store.connect() as db:
                    db.execute('SELECT 1').fetchone()
                alive = not settings.worker_enabled or (worker.thread and worker.thread.is_alive() and time.time() - worker.last_tick < 180)
                self.respond(200 if alive else 503, {'status': 'ok' if alive else 'worker_unhealthy'})
                return
            if self.command == 'GET' and path in ('/', '/contact', '/app.js', '/styles.css'):
                filename = {'/': 'index.html', '/contact': 'contact.html', '/app.js': 'app.js', '/styles.css': 'styles.css'}[path]
                mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8'}[filename.rsplit('.', 1)[1]]
                self.respond(200, (STATIC / filename).read_bytes(), mime)
                return
            if self.command == 'GET' and path == '/api/public-config':
                self.respond(200, {'name': 'LeadHarbor', 'demo': settings.demo, 'max_message_length': 6000,
                                   'privacy_contact': settings.rules.get('privacy_contact', 'the team using this form'),
                                   'retention_days': settings.retention_days})
                return
            if self.command == 'POST' and path == '/api/login':
                self.rate('login', 5, 900)
                if self.headers.get('Origin') != settings.public_url or self.headers.get('X-LeadHarbor') != '1':
                    raise APIError(403, 'Same-origin request verification failed.')
                data = self.body()
                password = data.get('password')
                if not isinstance(password, str) or len(password) > 512 or not password_matches(password, settings.admin_password_hash):
                    raise APIError(401, 'Incorrect password.')
                token = session_token(settings.session_secret)
                with store.transaction() as db:
                    db.execute('DELETE FROM sessions WHERE expires_at<?', (time.time(),))
                    db.execute('INSERT INTO sessions VALUES(?,?)', (hashlib.sha256(token.encode()).hexdigest(), time.time() + 28800))
                secure = '; Secure' if settings.secure_cookies else ''
                self.respond(200, {'ok': True}, headers={'Set-Cookie': f'leadharbor_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800{secure}'})
                return
            if self.command == 'POST' and path == '/api/automation-errors':
                if not self.bearer(settings.ingest_token):
                    raise APIError(401, 'A valid ingestion bearer token is required.')
                self.rate('automation-errors', 60, 60)
                store.automation_error(self.body())
                self.respond(200, {'ok': True})
                return
            if self.command == 'POST' and path in ('/api/submit', '/api/leads'):
                if path == '/api/leads' and not self.bearer(settings.ingest_token):
                    raise APIError(401, 'A valid ingestion bearer token is required.')
                if self.headers.get('Origin') and self.headers.get('Origin') != settings.public_url:
                    raise APIError(403, 'Cross-origin submission is not allowed.')
                self.rate('capture', 20, 600)
                if not store.rate_limit('capture_global', 1000, 86400):
                    raise APIError(429, 'Daily submission limit reached.')
                data = self.body()
                if data.get('company_fax'):
                    # Honeypot: explicitly reject, rather than claiming an unsaved lead was received.
                    raise APIError(422, 'Submission could not be accepted.')
                if data.get('consent') is not True:
                    raise APIError(422, 'Please acknowledge the privacy notice.')
                lead = normalize_lead(data)
                key = self.headers.get('Idempotency-Key', '')
                row, created = store.capture(lead, key, 'public_form' if path == '/api/submit' else 'n8n', data.get('consent') is True)
                # Do not return contact details or enrichment to a public caller replaying a key.
                self.respond(202 if created else 200, {'id': row['id'], 'received': True, 'duplicate': not created})
                return
            if not path.startswith('/api/'):
                raise APIError(404, 'Page not found.')
            self.authorize()
            if self.command == 'POST' and path == '/api/logout':
                token = self.cookie()
                with store.transaction() as db:
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
                secure = '; Secure' if settings.secure_cookies else ''
                self.respond(200, {'ok': True}, headers={'Set-Cookie': 'leadharbor_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0' + secure})
            elif self.command == 'GET' and path == '/api/config':
                self.respond(200, {'demo': settings.demo, 'provider_configured': bool(settings.llm_api_key),
                                   'model': settings.llm_model, 'webhook_configured': bool(settings.webhook_url),
                                   'retention_days': settings.retention_days, 'rules': settings.rules})
            elif self.command == 'GET' and path == '/api/metrics':
                self.respond(200, store.metrics())
            elif self.command == 'GET' and path == '/api/leads':
                try:
                    limit = min(100, max(1, int(query.get('limit', ['50'])[0])))
                    offset = max(0, int(query.get('offset', ['0'])[0]))
                except ValueError:
                    raise APIError(422, 'Invalid pagination.') from None
                self.respond(200, store.listing(query.get('search', [''])[0][:200], query.get('status', [''])[0], query.get('tier', [''])[0], limit, offset))
            elif self.command == 'GET' and path == '/api/events':
                self.respond(200, {'items': store.events(), 'automation_errors': store.automation_errors()})
            elif self.command == 'POST' and path.startswith('/api/events/') and path.endswith('/retry'):
                self.body()
                store.retry_event(path.split('/')[3])
                self.respond(200, {'ok': True})
            elif self.command == 'GET' and path == '/api/export':
                self.rate('export', 3, 60)
                stream = io.StringIO()
                fields = ['id', 'full_name', 'email', 'company_domain', 'status', 'tier', 'score', 'owner', 'stage', 'created_at', 'follow_up_at', 'message']
                writer = csv.DictWriter(stream, fields, extrasaction='ignore')
                writer.writeheader()
                # Bound export size. Pagination remains available for larger datasets.
                for offset in range(0, 10000, 100):
                    rows = store.listing(limit=100, offset=offset)['items']
                    if not rows:
                        break
                    for row in rows:
                        for key in fields:
                            value = row.get(key)
                            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                                row[key] = "'" + value
                        writer.writerow(row)
                self.respond(200, stream.getvalue(), 'text/csv; charset=utf-8', {'Content-Disposition': 'attachment; filename="leadharbor-leads.csv"'})
            elif path.startswith('/api/leads/'):
                parts = path.strip('/').split('/')
                lead_id = parts[2]
                lead = store.get(lead_id)
                if not lead:
                    raise APIError(404, 'Lead not found.')
                if self.command == 'GET' and len(parts) == 3:
                    self.respond(200, {**lead, 'history': store.history(lead_id), 'ai_usage': store.lead_usage(lead_id)})
                elif self.command == 'PATCH' and len(parts) == 3:
                    self.respond(200, store.patch(lead_id, self.body()))
                elif self.command == 'DELETE' and len(parts) == 3:
                    store.delete(lead_id)
                    self.respond(200, {'ok': True})
                elif self.command == 'POST' and len(parts) == 4 and parts[3] == 'retry':
                    self.body()
                    store.retry(lead_id)
                    self.respond(200, {'ok': True})
                elif self.command == 'POST' and len(parts) == 4 and parts[3] == 'approve':
                    store.approve(lead_id, self.body().get('reply'))
                    self.respond(200, {'ok': True, 'message': 'Reply approved for manual sending. No email has been sent.'})
                else:
                    raise APIError(405, 'Unsupported action.')
            else:
                raise APIError(404, 'API endpoint not found.')

        def handle_request(self):
            try:
                self.dispatch()
            except APIError as exc:
                self.close_connection = True
                self.respond(exc.status, {'error': exc.message}, headers={'Retry-After': '60'} if exc.status == 429 else None)
            except Conflict as exc:
                self.close_connection = True
                self.respond(409, {'error': str(exc)})
            except ValidationError as exc:
                self.close_connection = True
                self.respond(422, {'error': str(exc)})
            except KeyError:
                self.respond(404, {'error': 'Record not found.'})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except sqlite3.OperationalError:
                log.exception('Database request failed')
                self.respond(503, {'error': 'Storage is temporarily unavailable. Retry with the same submission key.'})
            except Exception:
                log.exception('Request failed')
                self.respond(500, {'error': 'Unexpected server error.'})

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request
    return Handler


class BoundedHTTPServer(ThreadingHTTPServer):
    """Cap concurrent request threads, including slow clients."""
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def build_server(settings):
    store = Store(settings.db_path)
    with store.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL)')
    worker = Worker(store, settings)
    server = BoundedHTTPServer((settings.host, settings.port), make_handler(store, settings, worker))
    server.daemon_threads = True
    server.request_queue_size = 32
    return server, store, worker


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    settings = Settings.from_env()
    server, _, worker = build_server(settings)
    if settings.worker_enabled:
        worker.start()
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    log.info('LeadHarbor listening at %s', settings.public_url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        worker.close()


if __name__ == '__main__':
    main()
