"""Integer double-entry journal with atomic append and compensating reversals."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ACCOUNTS = [
    {'id':'cash','name':'Cash','type':'asset','normal':'debit'},
    {'id':'receivable','name':'Receivables','type':'asset','normal':'debit'},
    {'id':'supplies','name':'Supplies expense','type':'expense','normal':'debit'},
    {'id':'payable','name':'Payables','type':'liability','normal':'credit'},
    {'id':'capital','name':'Contributed capital','type':'equity','normal':'credit'},
    {'id':'income','name':'Service income','type':'revenue','normal':'credit'},
]
IDS = {a['id'] for a in ACCOUNTS}


class Conflict(ValueError): pass
class Missing(ValueError): pass


class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try: return super().__exit__(*args)
        finally: self.close()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate(body,reverse=False):
    fields={'key','memo','expected_revision'} | (set() if reverse else {'lines'})
    if not isinstance(body,dict) or set(body)!=fields: raise ValueError('Unexpected request fields.')
    if not isinstance(body['key'],str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',body['key']): raise ValueError('Request key must be 1–64 ASCII name characters.')
    memo=body['memo']
    if not isinstance(memo,str) or not 1<=len(memo)<=160 or any(ord(c)<32 or 127<=ord(c)<=159 or 0xD800<=ord(c)<=0xDFFF for c in memo): raise ValueError('Memo must contain 1–160 printable characters.')
    if type(body['expected_revision']) is not int or not 0<=body['expected_revision']<=1000: raise ValueError('Expected revision must be an integer from 0 to 1000.')
    if not reverse:
        lines=body['lines']
        if not isinstance(lines,list) or not 2<=len(lines)<=6: raise ValueError('Provide 2–6 posting lines.')
        seen=set()
        for line in lines:
            if not isinstance(line,dict) or set(line)!={'account','debit','credit'}: raise ValueError('Each line needs account, debit, credit.')
            if not isinstance(line['account'],str) or line['account'] not in IDS or line['account'] in seen: raise ValueError('Accounts must be known and appear once per entry.')
            seen.add(line['account'])
            if any(type(line[k]) is not int or not 0<=line[k]<=10**9 for k in ['debit','credit']) or (line['debit']>0)==(line['credit']>0): raise ValueError('Each line needs one positive integer side, at most 1,000,000,000 minor units.')
        if sum(x['debit'] for x in lines)!=sum(x['credit'] for x in lines): raise ValueError('Debits must equal credits exactly.')
    return body


class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE,
              request TEXT NOT NULL, memo TEXT NOT NULL, created TEXT NOT NULL,
              reversal_of INTEGER UNIQUE REFERENCES entries(id), previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lines(entry_id INTEGER NOT NULL REFERENCES entries(id),
              account TEXT NOT NULL, debit INTEGER NOT NULL, credit INTEGER NOT NULL,
              PRIMARY KEY(entry_id,account), CHECK(debit>=0 AND credit>=0), CHECK((debit>0)!=(credit>0)));
            CREATE TRIGGER IF NOT EXISTS immutable_entries_update BEFORE UPDATE ON entries BEGIN SELECT RAISE(ABORT,'Entries are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_entries_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT,'Entries are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_lines_update BEFORE UPDATE ON lines BEGIN SELECT RAISE(ABORT,'Postings are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_lines_delete BEFORE DELETE ON lines BEGIN SELECT RAISE(ABORT,'Postings are immutable'); END;
            ''')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,factory=Connection)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def entry(self,db,ident):
        row=db.execute('SELECT id,key,memo,created,reversal_of,previous_hash,hash FROM entries WHERE id=?',(ident,)).fetchone()
        if row is None: raise Missing('Entry not found.')
        names=['id','key','memo','createdAt','reversalOf','previousHash','hash']
        entry=dict(zip(names,row))
        entry['lines']=[dict(zip(['account','debit','credit'],line)) for line in db.execute('SELECT account,debit,credit FROM lines WHERE entry_id=? ORDER BY account',(ident,))]
        return entry

    def post(self,body,reversal_of=None):
        validate(body,reversal_of is not None)
        if reversal_of is not None and (type(reversal_of) is not int or reversal_of<1): raise ValueError('Invalid reversal target.')
        request=canonical({'kind':'reverse' if reversal_of is not None else 'post','target':reversal_of,**body})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT id,request FROM entries WHERE key=?',(body['key'],)).fetchone()
            if prior:
                if prior[1]!=request: raise Conflict('This request key was used with different content.')
                return {'entry':self.entry(db,prior[0]),'replayed':True}
            head=db.execute('SELECT id,hash FROM entries ORDER BY id DESC LIMIT 1').fetchone()
            revision=head[0] if head else 0
            if body['expected_revision']!=revision: raise Conflict(f'Stale revision: expected {body["expected_revision"]}, current {revision}. Refresh and review before retrying.')
            if revision>=1000: raise ValueError('The local journal is limited to 1,000 entries.')
            if reversal_of is not None:
                original=self.entry(db,reversal_of)
                if original['reversalOf'] is not None: raise Conflict('Reversing a reversal is not supported; post a new explicit entry.')
                if db.execute('SELECT 1 FROM entries WHERE reversal_of=?',(reversal_of,)).fetchone(): raise Conflict('This entry already has a reversal.')
                lines=[{'account':x['account'],'debit':x['credit'],'credit':x['debit']} for x in original['lines']]
            else: lines=sorted(body['lines'],key=lambda x:x['account'])
            entry={'id':revision+1,'key':body['key'],'memo':body['memo'],'createdAt':datetime.now(timezone.utc).isoformat(timespec='microseconds'),
                   'reversalOf':reversal_of,'previousHash':head[1] if head else '0'*64,'lines':lines}
            entry['hash']=digest(entry)
            db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?)',(entry['id'],entry['key'],request,entry['memo'],entry['createdAt'],reversal_of,entry['previousHash'],entry['hash']))
            db.executemany('INSERT INTO lines VALUES(?,?,?,?)',[(entry['id'],x['account'],x['debit'],x['credit']) for x in lines])
            return {'entry':entry,'replayed':False}

    def snapshot(self,through=None):
        with self.connect() as db:
            db.execute('BEGIN')
            head=db.execute('SELECT COALESCE(MAX(id),0) FROM entries').fetchone()[0]
            if through is None: through=head
            if type(through) is not int or not 0<=through<=head: raise ValueError('As-of revision is outside this journal.')
            entries=[self.entry(db,row[0]) for row in db.execute('SELECT id FROM entries WHERE id<=? ORDER BY id',(through,)).fetchall()]
        errors=[];previous='0'*64
        for i,entry in enumerate(entries,1):
            if entry['id']!=i or entry['previousHash']!=previous or entry['hash']!=digest({k:v for k,v in entry.items() if k!='hash'}): errors.append(f'Entry {entry["id"]}: hash chain mismatch.')
            if sum(x['debit']-x['credit'] for x in entry['lines'])!=0: errors.append(f'Entry {entry["id"]}: unbalanced postings.')
            previous=entry['hash']
        accounts=[]
        for account in ACCOUNTS:
            lines=[line for entry in entries for line in entry['lines'] if line['account']==account['id']]
            debit=sum(x['debit'] for x in lines);credit=sum(x['credit'] for x in lines)
            accounts.append({**account,'debit':debit,'credit':credit,'netDebit':debit-credit,'normalBalance':(debit-credit)*(1 if account['normal']=='debit' else -1)})
        return {'format':'journal-balance-evidence-v1','head':head,'revision':through,'entries':entries,'accounts':accounts,
                'totalDebit':sum(a['debit'] for a in accounts),'totalCredit':sum(a['credit'] for a in accounts),'audit':{'ok':not errors,'errors':errors,'checked':len(entries)}}
