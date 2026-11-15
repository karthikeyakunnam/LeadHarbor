import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from leadharbor.config import ROOT
from leadharbor.db import Store
from leadharbor.enrichment import ProviderError, post_json
from leadharbor.security import normalize_lead
from test_core import AI, LEAD, WEB, settings
from leadharbor.worker import Worker
from unittest.mock import patch


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests=[]
        requests=self.requests
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append({'path':self.path,'headers':dict(self.headers),'body':body})
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/destination');self.end_headers();return
                if self.path=='/rate-limit':
                    self.send_response(429);self.end_headers();return
                if self.path=='/unauthorized':
                    self.send_response(401);self.end_headers();return
                if self.path=='/oversized':payload=b'x'*70000
                elif self.path=='/webhook':payload=b'accepted'
                else:payload=json.dumps({'choices':[{'message':{'content':json.dumps(AI)}}], 'usage':{'prompt_tokens':321,'completion_tokens':75}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01),daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()

    def test_provider_redirection_never_forwards_token(self):
        with self.assertRaises(ProviderError):post_json(self.url+'/redirect',{},'test-only-secret')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.requests[0]['headers']['Authorization'],'Bearer test-only-secret')

    def test_transient_and_configuration_errors(self):
        with self.assertRaises(ProviderError) as rate:post_json(self.url+'/rate-limit',{})
        self.assertFalse(rate.exception.permanent)
        with self.assertRaises(ProviderError) as auth:post_json(self.url+'/unauthorized',{})
        self.assertTrue(auth.exception.configuration)

    def test_size_and_plain_text_webhook(self):
        with self.assertRaises(ProviderError):post_json(self.url+'/oversized',{})
        self.assertEqual(post_json(self.url+'/webhook',{},expect_json=False),{})

    def test_full_worker_roundtrip_against_local_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'integration.sqlite3')
            row,_=store.capture(normalize_lead(LEAD),'provider-integration-001')
            config=settings(store.path,llm_base_url=self.url,llm_api_key='test-only-key',webhook_url=self.url+'/webhook',webhook_token='test-only-event')
            worker=Worker(store,config)
            with patch('leadharbor.worker.fetch_website',return_value=WEB):
                self.assertTrue(worker.process_one())
            result=store.get(row['id'])
            self.assertEqual(result['status'],'ready')
            self.assertEqual(store.lead_usage(row['id'])['input_tokens'],321)
            self.assertTrue(worker.deliver_one())
            self.assertEqual(store.events()[0]['status'],'delivered')
            call=self.requests[0]
            self.assertEqual(call['path'],'/chat/completions')
            self.assertEqual(call['body']['response_format'],{'type':'json_object'})
            self.assertIn('UNTRUSTED',call['body']['messages'][0]['content'])
            self.assertEqual(self.requests[1]['headers']['Idempotency-Key'],store.events()[0]['id'])


class BackupTests(unittest.TestCase):
    def test_online_backup_restores_committed_records_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3';backup=Path(tmp)/'backup.sqlite3'
            store=Store(path)
            row,_=store.capture(normalize_lead(LEAD),'backup-submission-001')
            # Keep an active connection so committed WAL data is part of the test.
            connection=sqlite3.connect(path)
            connection.execute('PRAGMA journal_mode=WAL')
            env={**os.environ,'DATABASE_PATH':str(path)}
            result=subprocess.run([sys.executable,'scripts/backup.py',str(backup)],cwd=ROOT,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            restored=Store(backup)
            self.assertEqual(restored.get(row['id'])['email'],LEAD['email'])
            again=subprocess.run([sys.executable,'scripts/backup.py',str(backup)],cwd=ROOT,env=env,capture_output=True,text=True)
            self.assertNotEqual(again.returncode,0)
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)
            connection.close()


if __name__=='__main__':unittest.main()
