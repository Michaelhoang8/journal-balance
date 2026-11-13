import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path
from store import Store,Conflict,Missing


def request(key='one',revision=0,amount=100):
    return {'key':key,'memo':'Fictional capital contribution','expected_revision':revision,'lines':[{'account':'cash','debit':amount,'credit':0},{'account':'capital','debit':0,'credit':amount}]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db.sqlite3')
    def tearDown(self): self.temp.cleanup()

    def test_balanced_post_replay_and_as_of(self):
        first=self.store.post(request());self.assertFalse(first['replayed'])
        self.store.post(request('two',1,250))
        replay=self.store.post(request());self.assertTrue(replay['replayed']);self.assertEqual(replay['entry'],first['entry'])
        self.assertEqual(self.store.snapshot()['totalDebit'],350)
        self.assertEqual(self.store.snapshot(1)['totalDebit'],100)
        self.assertEqual(self.store.snapshot(0)['entries'],[])
        with self.assertRaises(Conflict):self.store.post(request(amount=101))
        with self.assertRaises(Conflict):self.store.post(request('stale'))
        self.assertTrue(Store(self.store.path).snapshot()['audit']['ok'])

    def test_reversal_compensates_without_erasing_original(self):
        original=self.store.post(request())['entry']
        reverse={'key':'undo','memo':'Correct sample contribution','expected_revision':1}
        result=self.store.post(reverse,1)
        snapshot=self.store.snapshot();self.assertEqual(len(snapshot['entries']),2)
        self.assertEqual(snapshot['entries'][0],original)
        self.assertTrue(all(a['normalBalance']==0 for a in snapshot['accounts']))
        self.assertEqual(result['entry']['reversalOf'],1)
        self.assertTrue(self.store.post(reverse,1)['replayed'])
        with self.assertRaises(Conflict):self.store.post({**reverse,'key':'again','expected_revision':2},1)
        with self.assertRaises(Conflict):self.store.post({**reverse,'key':'again','expected_revision':2},2)

    def test_concurrent_cas_and_retries(self):
        def unique(i):
            try:return not self.store.post(request(f'r{i}'))['replayed']
            except Conflict:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(unique,range(20))),1)
        body=request('duplicate',1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:self.store.post(body),range(16)))
        self.assertEqual(sum(not x['replayed'] for x in results),1)
        self.assertEqual(self.store.snapshot()['head'],2)

    def test_fault_rolls_back_header_and_all_lines(self):
        with self.store.connect() as db:db.execute("CREATE TRIGGER fail_cash BEFORE INSERT ON lines WHEN NEW.account='cash' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.store.post(request())
        self.assertEqual(self.store.snapshot()['entries'],[])

    def test_database_rejects_edits_and_audit_detects_admin_tampering(self):
        self.store.post(request())
        with self.store.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("UPDATE entries SET memo='edit'")
            with self.assertRaises(sqlite3.IntegrityError):db.execute('DELETE FROM lines')
            db.execute('DROP TRIGGER immutable_entries_update');db.execute("UPDATE entries SET memo='tampered'")
        self.assertFalse(self.store.snapshot()['audit']['ok'])

    def test_validation_and_exact_large_integer_totals(self):
        for change in [{'expected_revision':True},{'memo':'\ud800'},{'key':'bad/key'}]:
            with self.assertRaises(ValueError):self.store.post({**request(),**change})
        for line in [{'account':'cash','debit':1.0,'credit':0},{'account':'cash','debit':True,'credit':0},{'account':'cash','debit':1,'credit':1},{'account':'missing','debit':100,'credit':0},{'account':'cash','debit':101,'credit':0}]:
            body=request();body['lines'][0]=line
            with self.assertRaises(ValueError):self.store.post(body)
        for i in range(20):self.store.post(request(f'big{i}',i,10**9))
        snapshot=self.store.snapshot();self.assertEqual(snapshot['totalDebit'],20*10**9)
        self.assertEqual(sum(a['netDebit'] for a in snapshot['accounts']),0)
        self.assertTrue(snapshot['audit']['ok'])
        with self.assertRaises(ValueError):self.store.snapshot(21)
        with self.assertRaises(Missing):self.store.post({'key':'missing','memo':'Missing','expected_revision':20},99)

    def test_reversal_race_has_one_winner(self):
        self.store.post(request())
        def reverse(i):
            try:self.store.post({'key':f'undo{i}','memo':'Undo','expected_revision':1},1);return 1
            except Conflict:return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(reverse,range(12))),1)
        self.assertEqual(self.store.snapshot()['totalDebit'],200)
