"""SQLite is the durable queue and source of truth; every write uses a transaction."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid

from .security import ValidationError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS contacts(
 id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS leads(
 id TEXT PRIMARY KEY, contact_id TEXT NOT NULL REFERENCES contacts(id),
 idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
 full_name TEXT NOT NULL, email TEXT NOT NULL, company_domain TEXT NOT NULL,
 website_url TEXT NOT NULL, message TEXT NOT NULL, is_free_mail INTEGER NOT NULL,
 source TEXT NOT NULL, consent_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL, claim_token TEXT,
 last_error TEXT, enrichment TEXT, score INTEGER, tier TEXT NOT NULL DEFAULT 'review',
 owner TEXT NOT NULL DEFAULT 'Unassigned', follow_up_at REAL,
 stage TEXT NOT NULL DEFAULT 'new', reply_status TEXT NOT NULL DEFAULT 'draft',
 approved_reply TEXT, score_accepted INTEGER, notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS lead_queue ON leads(status, next_attempt, created_at);
CREATE INDEX IF NOT EXISTS lead_contact ON leads(contact_id);
CREATE TABLE IF NOT EXISTS audit(
 id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id TEXT REFERENCES leads(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache(url TEXT PRIMARY KEY, result TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_calls(
 id TEXT PRIMARY KEY, lead_id TEXT REFERENCES leads(id) ON DELETE CASCADE,
 model TEXT NOT NULL, created_at REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
 cost REAL, outcome TEXT NOT NULL DEFAULT 'started'
);
CREATE TABLE IF NOT EXISTS outbox(
 id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 lease_until REAL, claim_token TEXT, last_error TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_queue ON outbox(status, next_attempt);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS automation_errors(
 id INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT NOT NULL, execution_id TEXT NOT NULL,
 message TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(workflow,execution_id)
);
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at REAL NOT NULL);
'''


class Conflict(ValidationError):
    pass


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def audit(db, lead_id, kind, detail=''):
        db.execute('INSERT INTO audit(lead_id,kind,detail,created_at) VALUES(?,?,?,?)',
                   (lead_id, kind, detail, time.time()))

    @staticmethod
    def decode(row):
        if row is None:
            return None
        row = dict(row)
        row['enrichment'] = json.loads(row['enrichment']) if row.get('enrichment') else None
        for field in ('payload_hash', 'idempotency_key', 'claim_token', 'lease_until'):
            row.pop(field, None)
        return row

    def capture(self, lead, key, source='form', consent=False):
        if not isinstance(key, str) or not 8 <= len(key) <= 128 or not key.isascii() or not all(c.isalnum() or c in '-_.' for c in key):
            raise ValidationError('A valid Idempotency-Key of 8–128 characters is required.')
        digest = hashlib.sha256(json.dumps(lead, sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self.transaction() as db:
            old = db.execute('SELECT * FROM leads WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old['payload_hash'] != digest:
                    raise Conflict('This submission key was already used with different content.')
                return self.decode(old), False
            db.execute('INSERT INTO contacts VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET full_name=excluded.full_name',
                       (str(uuid.uuid4()), lead['email'], lead['full_name'], now))
            contact = db.execute('SELECT id FROM contacts WHERE email=?', (lead['email'],)).fetchone()['id']
            lead_id = str(uuid.uuid4())
            db.execute('''INSERT INTO leads(id,contact_id,idempotency_key,payload_hash,full_name,email,company_domain,
              website_url,message,is_free_mail,source,consent_at,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (lead_id, contact, key, digest, lead['full_name'], lead['email'], lead['company_domain'],
                        lead['website_url'], lead['message'], int(lead['is_free_mail']), source, now if consent else None, now, now))
            self.audit(db, lead_id, 'received', source)
            return self.decode(db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone()), True

    def claim(self, max_attempts, now=None):
        now = now or time.time()
        with self.transaction() as db:
            expired = db.execute("SELECT id,attempts FROM leads WHERE status='processing' AND lease_until<?", (now,)).fetchall()
            for row in expired:
                status = 'failed' if row['attempts'] >= max_attempts else 'retry'
                db.execute('UPDATE leads SET status=?,lease_until=NULL,claim_token=NULL,last_error=? WHERE id=?',
                           (status, 'Worker lease expired; interrupted job recovered.', row['id']))
                self.audit(db, row['id'], 'lease_recovered', status)
                if status == 'failed':
                    self.enqueue(db, row['id'], 'lead.failed', {'lead_id': row['id'], 'status': status, 'error': 'Worker lease expired after final attempt.'})
            row = db.execute("SELECT * FROM leads WHERE status IN ('pending','retry') AND next_attempt<=? AND attempts<? ORDER BY created_at LIMIT 1",
                             (now, max_attempts)).fetchone()
            if not row:
                return None
            token = secrets.token_hex(16)
            db.execute("UPDATE leads SET status='processing',attempts=attempts+1,lease_until=?,claim_token=?,updated_at=? WHERE id=?",
                       (now + 180, token, now, row['id']))
            self.audit(db, row['id'], 'processing', f"Attempt {row['attempts'] + 1}")
            result = dict(db.execute('SELECT * FROM leads WHERE id=?', (row['id'],)).fetchone())
            return result

    def finish(self, lead, enrichment, score, tier, owner, follow_up_hours):
        now = time.time()
        status = 'needs_review' if tier == 'review' else 'ready'
        with self.transaction() as db:
            changed = db.execute('''UPDATE leads SET status=?,enrichment=?,score=?,tier=?,owner=CASE WHEN owner='Unassigned' THEN ? ELSE owner END,follow_up_at=COALESCE(follow_up_at,?),
              updated_at=?,lease_until=NULL,claim_token=NULL,last_error=NULL
              WHERE id=? AND status='processing' AND claim_token=?''',
              (status, json.dumps(enrichment), score, tier, owner, now + follow_up_hours * 3600,
               now, lead['id'], lead['claim_token'])).rowcount
            if changed:
                self.audit(db, lead['id'], 'enriched', f'{tier}; score={score}')
                self.enqueue(db, lead['id'], 'lead.qualified', {'lead_id': lead['id'], 'tier': tier, 'score': score,
                             'owner': owner, 'full_name': lead['full_name'], 'email': lead['email'],
                             'summary': enrichment['company_summary'], 'suggested_reply': enrichment['suggested_reply']})
            return bool(changed)

    @staticmethod
    def enqueue(db, lead_id, kind, payload):
        event_id = str(uuid.uuid4())
        payload = {'event_id': event_id, 'type': kind, 'created_at': time.time(), **payload}
        db.execute('INSERT INTO outbox(id,lead_id,kind,payload,created_at) VALUES(?,?,?,?,?)',
                   (event_id, lead_id, kind, json.dumps(payload), time.time()))

    def fail(self, lead, message, max_attempts, permanent=False, configuration=False):
        status = 'needs_configuration' if configuration else ('failed' if permanent or lead['attempts'] >= max_attempts else 'retry')
        delay = min(3600, 30 * 2 ** (lead['attempts'] - 1))
        with self.transaction() as db:
            changed = db.execute('''UPDATE leads SET status=?,last_error=?,next_attempt=?,lease_until=NULL,
              claim_token=NULL,updated_at=? WHERE id=? AND claim_token=?''',
              (status, message[:300], time.time() + delay, time.time(), lead['id'], lead['claim_token'])).rowcount
            if changed:
                self.audit(db, lead['id'], status, message[:300])
                if status in ('failed', 'needs_configuration'):
                    self.enqueue(db, lead['id'], 'lead.failed', {'lead_id': lead['id'], 'status': status, 'error': message[:300]})

    def retry(self, lead_id):
        with self.transaction() as db:
            row = db.execute('SELECT status FROM leads WHERE id=?', (lead_id,)).fetchone()
            if not row:
                raise KeyError(lead_id)
            if row['status'] not in ('failed', 'needs_review', 'needs_configuration'):
                raise Conflict('Only failed, review, or configuration-blocked leads can be retried.')
            db.execute("UPDATE leads SET status='pending',attempts=0,next_attempt=0,last_error=NULL,updated_at=? WHERE id=?", (time.time(), lead_id))
            self.audit(db, lead_id, 'retry_requested')

    def get(self, lead_id):
        with self.connect() as db:
            return self.decode(db.execute('SELECT * FROM leads WHERE id=?', (lead_id,)).fetchone())

    def listing(self, search='', status='', tier='', limit=100, offset=0):
        where, args = ['1=1'], []
        if search:
            where.append('(full_name LIKE ? ESCAPE \'\\\' OR email LIKE ? ESCAPE \'\\\' OR company_domain LIKE ? ESCAPE \'\\\')')
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + escaped + '%'] * 3)
        for field, value in [('status', status), ('tier', tier)]:
            if value:
                where.append(field + '=?')
                args.append(value)
        query = ' AND '.join(where)
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM leads WHERE ' + query, args).fetchone()[0]
            rows = db.execute('SELECT * FROM leads WHERE ' + query + ' ORDER BY created_at DESC LIMIT ? OFFSET ?',
                              (*args, limit, offset)).fetchall()
        return {'items': [self.decode(r) for r in rows], 'total': total, 'limit': limit, 'offset': offset}

    def patch(self, lead_id, fields):
        allowed = {'owner', 'follow_up_at', 'stage', 'notes', 'score_accepted'}
        if not isinstance(fields, dict) or not fields or set(fields) - allowed:
            raise ValidationError('Unsupported or empty update.')
        for key, value in fields.items():
            if key in ('owner', 'notes') and (not isinstance(value, str) or len(value) > (2000 if key == 'notes' else 150)):
                raise ValidationError('Invalid owner or notes.')
            if key == 'stage' and value not in ('new', 'contacted', 'qualified', 'won', 'lost'):
                raise ValidationError('Invalid sales stage.')
            if key == 'score_accepted' and value is not None and type(value) is not bool:
                raise ValidationError('Score feedback must be true, false, or null.')
            if key == 'follow_up_at' and value is not None and (type(value) not in (int, float) or not 0 <= value <= 4102444800):
                raise ValidationError('Invalid follow-up date.')
        with self.transaction() as db:
            if not db.execute('SELECT id FROM leads WHERE id=?', (lead_id,)).fetchone():
                raise KeyError(lead_id)
            db.execute('UPDATE leads SET ' + ','.join(k + '=?' for k in fields) + ',updated_at=? WHERE id=?', (*fields.values(), time.time(), lead_id))
            self.audit(db, lead_id, 'updated', ', '.join(fields))
        return self.get(lead_id)

    def approve(self, lead_id, reply):
        if not isinstance(reply, str) or not 1 <= len(reply.strip()) <= 3000:
            raise ValidationError('Reply must contain 1–3000 characters.')
        with self.transaction() as db:
            row = db.execute('SELECT status,reply_status,approved_reply FROM leads WHERE id=?', (lead_id,)).fetchone()
            if not row:
                raise KeyError(lead_id)
            if row['status'] not in ('ready', 'needs_review'):
                raise Conflict('Enrichment must finish before approving a reply.')
            if row['reply_status'] == 'approved' and row['approved_reply'] == reply.strip():
                return
            db.execute("UPDATE leads SET reply_status='approved',approved_reply=?,updated_at=? WHERE id=?", (reply.strip(), time.time(), lead_id))
            self.audit(db, lead_id, 'reply_approved', 'Approved for manual sending; no email was sent.')

    def delete(self, lead_id):
        with self.transaction() as db:
            row = db.execute('SELECT contact_id FROM leads WHERE id=?', (lead_id,)).fetchone()
            if not row:
                raise KeyError(lead_id)
            db.execute('DELETE FROM leads WHERE id=?', (lead_id,))
            db.execute('DELETE FROM contacts WHERE id=? AND NOT EXISTS(SELECT 1 FROM leads WHERE contact_id=?)', (row['contact_id'], row['contact_id']))

    def metrics(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT status, COUNT(*) FROM leads GROUP BY status').fetchall())
            tiers = dict(db.execute('SELECT tier,COUNT(*) FROM leads GROUP BY tier').fetchall())
            stages = dict(db.execute('SELECT stage,COUNT(*) FROM leads GROUP BY stage').fetchall())
            calls = dict(db.execute('SELECT COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, SUM(cost) cost, SUM(cost IS NULL) unpriced_calls FROM ai_calls').fetchone())
            overdue = db.execute("SELECT COUNT(*) FROM leads WHERE follow_up_at<? AND stage NOT IN ('won','lost')", (time.time(),)).fetchone()[0]
            feedback = dict(db.execute('SELECT COUNT(score_accepted) reviewed,SUM(score_accepted) accepted FROM leads').fetchone())
            delivered = db.execute("SELECT COUNT(*) FROM outbox WHERE status='failed'").fetchone()[0]
            contacts = db.execute('SELECT COUNT(*) FROM contacts').fetchone()[0]
        return {'total': sum(counts.values()), 'statuses': counts, 'tiers': tiers, 'stages': stages,
                'contacts': contacts, 'overdue': overdue, 'ai': calls, 'feedback': feedback, 'failed_events': delivered}

    def history(self, lead_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT kind,detail,created_at FROM audit WHERE lead_id=? ORDER BY id DESC LIMIT 100', (lead_id,))]

    def rate_limit(self, key, maximum, seconds=60):
        now = time.time()
        # Persist only a one-way identifier, never the visitor's raw IP.
        key = hashlib.sha256(key.encode()).hexdigest()
        with self.transaction() as db:
            db.execute('DELETE FROM rate_limits WHERE expires_at<?', (now,))
            db.execute('INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET hits=hits+1', (key, now + seconds))
            hits = db.execute('SELECT hits FROM rate_limits WHERE key=?', (key,)).fetchone()[0]
            return hits <= maximum

    def cache_get(self, url):
        with self.connect() as db:
            row = db.execute('SELECT result FROM cache WHERE url=? AND expires_at>?', (url, time.time())).fetchone()
            return json.loads(row[0]) if row else None

    def cache_set(self, url, result):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (url, json.dumps(result), time.time() + 86400))

    def reserve_call(self, lead_id, model, maximum=500):
        call_id = str(uuid.uuid4())
        now = time.time()
        with self.transaction() as db:
            count = db.execute('SELECT COUNT(*) FROM ai_calls WHERE created_at>=?', (int(now // 86400) * 86400,)).fetchone()[0]
            if count >= maximum:
                raise Conflict('Daily AI call limit reached; retry after the UTC day resets.')
            db.execute('INSERT INTO ai_calls(id,lead_id,model,created_at) VALUES(?,?,?,?)', (call_id, lead_id, model, now))
        return call_id

    def record_call(self, call_id, usage, settings, outcome='completed'):
        inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if type(inp) is not int or type(out) is not int or inp < 0 or out < 0:
            inp, out = None, None
        priced = settings.input_price_per_million > 0 and settings.output_price_per_million > 0
        cost = (inp * settings.input_price_per_million + out * settings.output_price_per_million) / 1e6 if priced and inp is not None else None
        with self.transaction() as db:
            db.execute('UPDATE ai_calls SET input_tokens=?,output_tokens=?,cost=?,outcome=? WHERE id=?', (inp, out, cost, outcome, call_id))

    def claim_event(self, max_attempts=5):
        now = time.time()
        with self.transaction() as db:
            db.execute("UPDATE outbox SET status=CASE WHEN attempts>=? THEN 'failed' ELSE 'pending' END,claim_token=NULL WHERE status='sending' AND lease_until<?", (max_attempts, now))
            row = db.execute("SELECT * FROM outbox WHERE status='pending' AND next_attempt<=? AND attempts<? ORDER BY created_at LIMIT 1", (now, max_attempts)).fetchone()
            if not row:
                return None
            token = secrets.token_hex(16)
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,lease_until=?,claim_token=? WHERE id=?", (now + 60, token, row['id']))
            return dict(db.execute('SELECT * FROM outbox WHERE id=?', (row['id'],)).fetchone())

    def complete_event(self, event, error=None):
        with self.transaction() as db:
            status = 'delivered' if error is None else ('failed' if event['attempts'] >= 5 else 'pending')
            db.execute('UPDATE outbox SET status=?,last_error=?,next_attempt=?,claim_token=NULL WHERE id=? AND claim_token=?',
                       (status, error, time.time() + min(3600, 30 * 2 ** event['attempts']), event['id'], event['claim_token']))

    def events(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,lead_id,kind,status,attempts,last_error,created_at FROM outbox ORDER BY created_at DESC LIMIT 100')]

    def retry_event(self, event_id):
        with self.transaction() as db:
            changed = db.execute("UPDATE outbox SET status='pending',attempts=0,next_attempt=0,last_error=NULL WHERE id=? AND status='failed'", (event_id,)).rowcount
            if not changed:
                raise Conflict('Only failed events can be retried.')

    def prune(self, days):
        with self.transaction() as db:
            # Never remove unfinished work automatically.
            db.execute("DELETE FROM leads WHERE created_at<? AND status NOT IN ('pending','retry','processing')", (time.time() - days * 86400,))
            db.execute('DELETE FROM contacts WHERE NOT EXISTS(SELECT 1 FROM leads WHERE contact_id=contacts.id)')
            db.execute('DELETE FROM automation_errors WHERE created_at<?', (time.time() - days * 86400,))
            db.execute('DELETE FROM sessions WHERE expires_at<?', (time.time(),))
            db.execute('DELETE FROM cache WHERE expires_at<?', (time.time(),))
            db.execute('DELETE FROM rate_limits WHERE expires_at<?', (time.time(),))

    def automation_error(self, data):
        if not isinstance(data, dict):
            raise ValidationError('Invalid error report.')
        values = []
        for key, maximum in [('workflow', 150), ('execution_id', 100), ('message', 300)]:
            value = data.get(key)
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise ValidationError('Invalid automation error fields.')
            values.append(value)
        with self.transaction() as db:
            db.execute('INSERT OR IGNORE INTO automation_errors(workflow,execution_id,message,created_at) VALUES(?,?,?,?)', (*values, time.time()))

    def automation_errors(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM automation_errors ORDER BY id DESC LIMIT 100')]

    def lead_usage(self, lead_id):
        with self.connect() as db:
            return dict(db.execute('SELECT COUNT(*) calls,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,SUM(cost) cost,SUM(cost IS NULL) unpriced_calls FROM ai_calls WHERE lead_id=?', (lead_id,)).fetchone())
