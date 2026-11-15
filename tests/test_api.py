import csv
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from leadharbor.security import password_hash
from leadharbor.server import build_server
from test_core import AI, LEAD, settings

PASSWORD='test-workspace-password'
HASH=password_hash(PASSWORD)


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config=settings(Path(self.tmp.name)/'api.sqlite3',port=0,worker_enabled=False)
        self.config.admin_password_hash=HASH
        self.server,self.store,self.worker=build_server(self.config)
        self.port=self.server.server_address[1]
        self.config.public_url=f'http://localhost:{self.port}'
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01),daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,path,method='GET',data=None,auth=False,cookie='',headers=None,raw=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        request_headers={'Content-Type':'application/json','Origin':self.config.public_url,'X-LeadHarbor':'1'}
        if auth:request_headers['Authorization']='Bearer '+self.config.admin_token
        if cookie:request_headers['Cookie']=cookie
        request_headers.update(headers or {})
        body=raw if raw is not None else (json.dumps(data) if data is not None else None)
        conn.request(method,path,body=body,headers=request_headers)
        response=conn.getresponse();status=response.status;response_headers=dict(response.getheaders());body=response.read();conn.close()
        if response_headers.get('Content-Type','').startswith('application/json'):body=json.loads(body)
        return status,body,response_headers

    def submit(self,key='api-submission-001',**changes):
        return self.request('/api/submit','POST',{**LEAD,'consent':True,**changes},headers={'Idempotency-Key':key})

    def test_auth_required_and_secret_not_exposed(self):
        for path in ['/api/leads','/api/config','/api/events','/api/metrics','/api/export']:
            self.assertEqual(self.request(path)[0],401)
        status,result,_=self.request('/api/public-config')
        self.assertEqual(status,200)
        self.assertNotIn('token',str(result))
        status,result,_=self.request('/api/config',auth=True)
        self.assertEqual(status,200)
        self.assertNotIn(self.config.admin_token,json.dumps(result))

    def test_capture_duplicate_and_conflict(self):
        status,result,_=self.submit()
        self.assertEqual(status,202)
        self.assertTrue(result['received'])
        self.assertNotIn('email',result)
        self.assertEqual(self.store.get(result['id'])['status'],'pending')
        self.assertEqual(self.submit()[0],200)
        self.assertEqual(self.submit(message='Different content')[0],409)
        self.assertEqual(self.store.metrics()['total'],1)

    def test_validation_consent_honeypot_and_size(self):
        self.assertEqual(self.submit(consent=False)[0],422)
        self.assertEqual(self.submit(company_fax='spam')[0],422)
        self.assertEqual(self.submit(email='broken')[0],422)
        self.assertEqual(self.submit(website_url='http://127.0.0.1')[0],422)
        self.assertEqual(self.submit(message='x'*6001)[0],422)
        self.assertEqual(self.request('/api/submit','POST',raw='x'*16385)[0],413)
        self.assertEqual(self.request('/api/submit','POST',raw='{"value":NaN}')[0],400)
        self.assertEqual(self.request('/api/submit','POST',raw='[]')[0],400)
        self.assertEqual(self.request('/api/submit','POST',{},headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.store.metrics()['total'],0)

    def test_cross_origin_capture_and_login_blocked(self):
        bad={'Origin':'https://attacker.example','Idempotency-Key':'test-submit-key'}
        self.assertEqual(self.request('/api/submit','POST',{**LEAD,'consent':True},headers=bad)[0],403)
        self.assertEqual(self.request('/api/login','POST',{'password':PASSWORD},headers=bad)[0],403)

    def test_ingestion_requires_different_token(self):
        data={**LEAD,'consent':True}
        headers={'Idempotency-Key':'n8n-submission-001'}
        self.assertEqual(self.request('/api/leads','POST',data,auth=True,headers=headers)[0],401)
        headers['Authorization']='Bearer '+self.config.ingest_token
        self.assertEqual(self.request('/api/leads','POST',data,headers=headers)[0],202)
        self.assertEqual(self.request('/api/metrics',headers=headers)[0],401)

    def test_login_csrf_and_logout_revocation(self):
        status,_,headers=self.request('/api/login','POST',{'password':PASSWORD})
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/leads',cookie=cookie)[0],200)
        _,row,_=self.submit()
        endpoint='/api/leads/'+row['id']
        self.assertEqual(self.request(endpoint,'PATCH',{'owner':'Jane'},cookie=cookie,headers={'X-LeadHarbor':''})[0],403)
        self.assertEqual(self.request(endpoint,'PATCH',{'owner':'Jane'},cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/logout','POST',{},cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/leads',cookie=cookie)[0],401)

    def test_login_rate_limit(self):
        for _ in range(5):self.assertEqual(self.request('/api/login','POST',{'password':'wrong'})[0],401)
        self.assertEqual(self.request('/api/login','POST',{'password':PASSWORD})[0],429)

    def test_patch_approve_feedback_and_delete(self):
        _,result,_=self.submit();lead_id=result['id'];endpoint='/api/leads/'+lead_id
        self.assertEqual(self.request(endpoint+'/approve','POST',{'reply':'Reviewed reply'},auth=True)[0],409)
        job=self.store.claim(3);self.store.finish(job,AI,8,'hot','Sales',4)
        self.assertEqual(self.request(endpoint,'PATCH',{'stage':'won','score_accepted':True},auth=True)[0],200)
        self.assertEqual(self.request(endpoint+'/approve','POST',{'reply':'Reviewed reply'},auth=True)[0],200)
        _,row,_=self.request(endpoint,auth=True)
        self.assertEqual(row['approved_reply'],'Reviewed reply')
        self.assertTrue(row['history'])
        self.assertEqual(self.request(endpoint,'DELETE',auth=True)[0],200)
        self.assertEqual(self.request(endpoint,auth=True)[0],404)
        self.assertEqual(self.store.metrics()['total'],0)

    def test_csv_formula_injection_prevented(self):
        self.submit(full_name='=HYPERLINK("bad")',message='+a spreadsheet formula')
        status,body,headers=self.request('/api/export',auth=True)
        self.assertEqual(status,200)
        row=list(csv.DictReader(io.StringIO(body.decode())))[0]
        self.assertTrue(row['full_name'].startswith("'="))
        self.assertTrue(row['message'].startswith("'+"))

    def test_security_headers_static_paths_and_health(self):
        for path in ['/','/contact','/styles.css','/app.js']:
            status,_,headers=self.request(path)
            self.assertEqual(status,200)
            self.assertEqual(headers['X-Frame-Options'],'DENY')
            self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../.env')[0],404)
        self.assertEqual(self.request('/healthz')[0],200)
        self.config.worker_enabled=True
        self.assertEqual(self.request('/healthz')[0],503)

    def test_pagination_and_missing_record(self):
        self.submit()
        self.assertEqual(self.request('/api/leads?limit=invalid',auth=True)[0],422)
        status,body,_=self.request('/api/leads?search=Jane&limit=1',auth=True)
        self.assertEqual(status,200);self.assertEqual(body['total'],1)
        self.assertEqual(self.request('/api/leads/not-present',auth=True)[0],404)

    def test_automation_error_ingestion(self):
        data={'workflow':'LeadHarbor capture','execution_id':'42','message':'Execution failed'}
        self.assertEqual(self.request('/api/automation-errors','POST',data)[0],401)
        header={'Authorization':'Bearer '+self.config.ingest_token}
        self.assertEqual(self.request('/api/automation-errors','POST',data,headers=header)[0],200)
        _,body,_=self.request('/api/events',auth=True)
        self.assertEqual(len(body['automation_errors']),1)


if __name__=='__main__':unittest.main()
