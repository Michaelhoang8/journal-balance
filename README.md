# Journal Balance

A local, integer-based double-entry journal. Balanced postings commit atomically, identical requests replay without duplication, and corrections append reversals instead of editing history.

![Journal Balance](docs/workbench.png)

## Run

```sh
python run.py --port 8000
```

Open http://127.0.0.1:8000. Python 3.10+ and its standard library are sufficient. The database is created at ignored `data/journal.sqlite3`; use `--database` to select a separate journal. No accounts, external services, or payments are involved.

Start with **Post fictional opening entry**, then expand the composer to post the prefilled fictional service invoice. Clicking Commit again with the same request replays the entry. Choose **Prepare new request** before creating a different posting. Select an entry to inspect its lines and append a reversal. Historical revisions show the account balances as they stood before later entries.

## Engineering features

- SQLite `BEGIN IMMEDIATE` serializes validation, revision checks, entry headers, posting lines, and reversal uniqueness in one transaction.
- Optimistic revision checks prevent a stale client from silently writing after another client.
- Globally unique request keys provide exact-content retry semantics. Successful retries return the original entry even after the head advances.
- Immutable headers and lines, enforced by update/delete triggers through ordinary database access. Reversals are new balanced entries linked to originals.
- SHA-256 chaining detects inconsistent entry content or ordering. Reads recompute hashes and balance totals from recorded posting lines.
- Snapshot reads support `through` revisions; JSON evidence and CSV trial balances explain the visible results.

SQLite's [transaction documentation](https://www.sqlite.org/lang_transaction.html) describes the immediate-write and snapshot-read behavior used here. Connections close deterministically, including on Windows; exceptions roll back the entire write.

## API

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/journal` | Current journal, balances, and integrity audit |
| GET | `/api/journal?through=2` | Entries/balances through revision 2, with current head reported |
| POST | `/api/entries` | Append a balanced entry, or replay an identical request |
| POST | `/api/entries/2/reverse` | Append a compensating reversal of entry 2 |

Example posting:

```json
{
  "key": "opening-001",
  "memo": "Fictional opening capital",
  "expected_revision": 0,
  "lines": [
    { "account": "cash", "debit": 100000, "credit": 0 },
    { "account": "capital", "debit": 0, "credit": 100000 }
  ]
}
```

A reversal request contains only `key`, `memo`, and `expected_revision`; the server swaps the original debit/credit sides. An original can be reversed once. Reversing a reversal is deliberately unsupported; post a new explicit entry instead.

New entries return 201; identical retries return 200 with `replayed: true`. Changed content under an existing key, stale revisions, or duplicate reversal attempts return 409. Invalid postings return 400; missing reversal targets return 404. A failed request does not consume its key. `expected_revision` is part of the request identity: preserve the entire original request when retrying. After a stale-revision rejection, refresh, review, and prepare a new request. Refresh also clears a pending reversal attempt so it can be prepared against the refreshed head.

Request equality is canonical JSON object equality with array order preserved. Reordering posting lines under an existing key is considered changed content, even if economically equivalent. Stored entry lines are sorted by account for deterministic evidence hashing.

## Amounts, accounts, and limits

Amounts are **integer minor units**, with no implied currency and no decimal parsing or conversion. Every line has exactly one positive side, at most 1,000,000,000 units. Debits and credits must balance exactly. A posting contains 2–6 distinct known accounts; zero/zero lines are omitted by the UI. JSON booleans and floating-point amounts are rejected by the backend.

The fixed chart contains cash, receivables, supplies expense, payables, contributed capital, and service income. Assets/expenses have debit-normal balances; liabilities/equity/revenue have credit-normal balances. Negative normal balances are allowed and appear on the opposite side of the trial balance. There is no overdraft or credit-limit policy.

The journal is limited to 1,000 entries, request bodies to 16 KiB, keys to 64 ASCII name characters, and memos to 160 printable characters. These bounds keep all totals within JavaScript's exact integer range. The UI's total posted debits is gross activity including reversals; it differs from the sum of positive net account balances in the trial balance. Account filtering affects the entry list only, not journal-wide balances or exports.

## Integrity and scope

Each digest hashes compact, sorted-key, ASCII-escaped JSON of `id`, `key`, `memo`, `createdAt`, `reversalOf`, `previousHash`, and sorted posting `lines`. The first previous hash is 64 zeroes. Recomputing the chain and debit/credit sums checks consistency of the exported record. The internal retry-request column is not part of this evidence format.

Hash chains are not signatures or protection against a database owner who can rewrite the chain, drop triggers, or truncate history. There is no externally anchored head hash, authentication, encryption, account management, currency support, period closing, reconciliation, tax reporting, or accounting-system integration. This is a teaching backend, not a production financial system. Local Host/Origin checks and loopback binding do not replace authentication. Back up the complete data directory with the server stopped.

## Verification

```sh
python -m unittest discover -s tests -v
npm run check
```

Nine tests cover balanced posting, historical snapshots, exact replay after head advancement, changed payloads, concurrent optimistic writes, simultaneous duplicate requests, reversal races, immutable-record triggers, an injected SQL failure after header insertion, corruption detection, strict integer types, large totals, and HTTP boundaries. CI runs Python 3.11/3.13 on Linux and Windows plus Node 22 syntax checks.

Browser verification covered opening/invoice postings, identical retries, unbalanced rejection, reversal, history, filtering, and a second client's stale write. The exported four-entry example contains opening capital 100,000, invoice 25,000, its reversal 25,000, and supplies expense 15,000. Independent Python verification recomputed all four hashes, all six balances, reversal linkage, and CSV results: cash 85,000 debit, supplies 15,000 debit, capital 100,000 credit, other net balances zero. Gross posted debits and credits each total 165,000. Runtime data is not committed.
