import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from run import Handler
from store import Store
from test_store import request


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.store=Store(Path(self.temp.name)/'db.sqlite3');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def call(self,method,path,body=None,headers=None):
        client=HTTPConnection(*self.server.server_address,timeout=3);client.request(method,path,None if body is None else json.dumps(body),headers or {'Content-Type':'application/json'})
        response=client.getresponse();raw=response.read();status=response.status;mime=response.headers['Content-Type'];client.close()
        return status,json.loads(raw) if raw and mime.startswith('application/json') else raw
    def test_post_reverse_retry_and_history(self):
        self.assertEqual(self.call('POST','/api/entries',request())[0],201)
        self.assertEqual(self.call('POST','/api/entries',request())[0],200)
        body={'key':'reverse','memo':'Undo','expected_revision':1}
        self.assertEqual(self.call('POST','/api/entries/1/reverse',body)[0],201)
        self.assertEqual(self.call('POST','/api/entries/1/reverse',body)[0],200)
        self.assertEqual(self.call('GET','/api/journal?through=1')[1]['totalDebit'],100)
        self.assertEqual(self.call('GET','/api/journal')[1]['head'],2)
        self.assertEqual(self.call('POST','/api/entries',request('stale'))[0],409)
    def test_boundaries(self):
        for path in ['/store.py','/data/journal.sqlite3','/.git/config']:self.assertEqual(self.call('GET',path)[0],404)
        for path in ['/api/journal?through=x','/api/journal?through=0&through=1','/api/journal?unknown=1','/api/journal?through=1']:self.assertEqual(self.call('GET',path)[0],400)
        self.assertEqual(self.call('GET','/',headers={'Host':'external.invalid'})[0],400)
        self.assertEqual(self.call('POST','/api/entries',{}, {'Content-Type':'application/json','Origin':'https://other.invalid'})[0],403)
        self.assertEqual(self.call('POST','/api/entries',{'memo':'a'*17000})[0],413)
        self.assertEqual(self.call('HEAD','/')[1],b'')
