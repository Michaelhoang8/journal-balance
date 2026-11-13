const $ = (id) => document.getElementById(id);
let snapshot = null,
  selected = null,
  busy = false,
  lastReversal = null;
const n = (value) => value.toLocaleString();
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "error" : "";
  for (const id of ["composer-notice", "entry-notice"]) {
    $(id).textContent = text;
    $(id).className = error ? "error" : "";
  }
}
async function api(path, body) {
  const response = await fetch(
    path,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {},
  );
  const data = await response.json();
  if (!response.ok) throw Error(data.error);
  return data;
}
function controls() {
  document
    .querySelectorAll("button,input,select")
    .forEach((e) => (e.disabled = busy));
  $("sample").disabled = busy || !snapshot || snapshot.head > 0;
  $("sample").hidden = Boolean(snapshot && snapshot.head > 0);
  const entry = snapshot?.entries.find((e) => e.id === selected);
  $("reverse-button").disabled =
    busy ||
    !entry ||
    entry.reversalOf !== null ||
    snapshot.entries.some((e) => e.reversalOf === entry.id) ||
    snapshot.revision !== snapshot.head;
  $("export").disabled = $("csv").disabled = busy || !snapshot;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  controls();
  try {
    await fn();
  } catch (e) {
    notice(e.message, true);
  } finally {
    busy = false;
    controls();
  }
}
function key() {
  return `request-${crypto.randomUUID()}`;
}
function prepare() {
  if (!snapshot) return;
  $("key").value = key();
  $("expected").value = snapshot.head;
  notice(
    "New request prepared at the current loaded revision. Review amounts before posting.",
  );
}
async function refresh(through = "") {
  snapshot = await api(
    "/api/journal" + (through === "" ? "" : `?through=${through}`),
  );
  $("through").replaceChildren(new Option("Latest", ""));
  for (let i = 0; i <= snapshot.head; i++)
    $("through").add(new Option(String(i), String(i)));
  $("through").value = through;
  render();
}
function cell(row, text, small) {
  const td = document.createElement("td");
  td.textContent = text;
  if (small) {
    const s = document.createElement("small");
    s.textContent = small;
    td.append(s);
  }
  row.append(td);
}
function render() {
  const s = snapshot;
  $("revision").textContent = `REVISION ${s.revision} / HEAD ${s.head}`;
  $("integrity").textContent = s.audit.ok
    ? `${s.audit.checked} ENTRIES VERIFIED`
    : "INTEGRITY CHECK FAILED";
  $("entries-count").textContent = n(s.entries.length);
  $("turnover").textContent = n(s.totalDebit);
  $("accounts").replaceChildren();
  let debit = 0,
    credit = 0;
  for (const a of s.accounts) {
    const row = document.createElement("tr");
    cell(row, a.name, `${a.type} · ${a.normal} normal`);
    cell(row, n(Math.max(0, a.netDebit)));
    cell(row, n(Math.max(0, -a.netDebit)));
    debit += Math.max(0, a.netDebit);
    credit += Math.max(0, -a.netDebit);
    $("accounts").append(row);
  }
  $("totals").replaceChildren();
  const totals = document.createElement("tr");
  for (const t of ["Trial balance", n(debit), n(credit)]) cell(totals, t);
  $("totals").append(totals);
  if (!$("posting-lines").children.length) {
    for (const a of s.accounts) {
      $("filter").add(new Option(a.name, a.id));
      const row = document.createElement("div");
      row.className = "posting-line";
      const name = document.createElement("span");
      name.textContent = a.name;
      row.append(name);
      for (const side of ["debit", "credit"]) {
        const label = document.createElement("label");
        label.textContent = side;
        const input = document.createElement("input");
        input.type = "number";
        input.min = "0";
        input.max = "1000000000";
        input.step = "1";
        input.value = "0";
        input.id = `${a.id}-${side}`;
        input.setAttribute("aria-label", `${a.name} ${side}`);
        input.oninput = draft;
        label.append(input);
        row.append(label);
      }
      $("posting-lines").append(row);
    }
    $("receivable-debit").value = "25000";
    $("income-credit").value = "25000";
    draft();
  }
  if (!s.entries.some((e) => e.id === selected))
    selected = s.entries.at(-1)?.id ?? null;
  history();
  detail();
}
function history() {
  if (!snapshot) return;
  $("history").replaceChildren();
  const entries = snapshot.entries
    .filter(
      (e) =>
        !$("filter").value ||
        e.lines.some((l) => l.account === $("filter").value),
    )
    .toReversed();
  for (const entry of entries) {
    const button = document.createElement("button");
    button.className = `entry-row ${entry.id === selected ? "selected" : ""}`;
    button.setAttribute("aria-pressed", String(entry.id === selected));
    for (const [text, cls] of [
      [`#${entry.id}`, ""],
      [entry.memo, ""],
      [entry.reversalOf ? `reverses #${entry.reversalOf}` : "posting", "meta"],
      [n(entry.lines.reduce((sum, l) => sum + l.debit, 0)), "sum"],
    ]) {
      const el = document.createElement(cls === "meta" ? "small" : "span");
      el.className = cls;
      el.textContent = text;
      button.append(el);
    }
    button.onclick = () => {
      selected = entry.id;
      history();
      detail();
      $("entry-notice").textContent = "";
      $("entry-details").showModal();
    };
    $("history").append(button);
  }
  if (!entries.length) $("history").textContent = "No entries in this view.";
}
function detail() {
  const entry = snapshot?.entries.find((e) => e.id === selected);
  $("entry").replaceChildren();
  if (!entry) {
    $("entry").textContent = "Choose an entry above.";
    controls();
    return;
  }
  const title = document.createElement("h3");
  title.textContent = `#${entry.id} · ${entry.memo}`;
  $("entry").append(title);
  const reversed = snapshot.entries.find((e) => e.reversalOf === entry.id);
  for (const text of [
    `${entry.createdAt} · request ${entry.key}`,
    entry.reversalOf
      ? `Reversal of #${entry.reversalOf}`
      : reversed
        ? `Reversed by #${reversed.id}`
        : "Original posting",
    ...entry.lines.map(
      (l) => `${l.account}: debit ${n(l.debit)} / credit ${n(l.credit)}`,
    ),
    `SHA-256 ${entry.hash}`,
  ]) {
    const p = document.createElement("p");
    p.textContent = text;
    $("entry").append(p);
  }
  controls();
}
function draft() {
  if (!snapshot) return;
  let debit = 0,
    credit = 0;
  for (const a of snapshot.accounts) {
    debit += Number($(`${a.id}-debit`).value);
    credit += Number($(`${a.id}-credit`).value);
  }
  $("draft-total").textContent =
    `Debits ${n(debit)} / credits ${n(credit)} · difference ${n(debit - credit)}`;
}
$("refresh").onclick = () =>
  action(async () => {
    await refresh($("through").value);
    lastReversal = null;
    notice(
      "Journal refreshed. Prepare a new request to update its expected revision.",
    );
  });
$("through").onchange = () =>
  action(async () => {
    await refresh($("through").value);
    notice(
      "Historical snapshot loaded. New postings still require the latest head revision.",
    );
  });
$("filter").onchange = history;
$("new-key").onclick = prepare;
$("post").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    const lines = snapshot.accounts
      .map((a) => ({
        account: a.id,
        debit: Number($(`${a.id}-debit`).value),
        credit: Number($(`${a.id}-credit`).value),
      }))
      .filter((l) => l.debit !== 0 || l.credit !== 0);
    const result = await api("/api/entries", {
      key: $("key").value,
      memo: $("memo").value,
      expected_revision: Number($("expected").value),
      lines,
    });
    selected = result.entry.id;
    await refresh();
    $("composer").close();
    notice(
      result.replayed
        ? `Entry #${selected} replayed; no new posting.`
        : `Entry #${selected} committed atomically. Prepare a new request for another entry.`,
    );
  });
};
$("sample").onclick = () =>
  action(async () => {
    const result = await api("/api/entries", {
      key: "fictional-opening",
      memo: "Fictional opening capital",
      expected_revision: 0,
      lines: [
        { account: "cash", debit: 100000, credit: 0 },
        { account: "capital", debit: 0, credit: 100000 },
      ],
    });
    selected = result.entry.id;
    await refresh();
    prepare();
    notice(
      "Fictional opening entry committed. The composer is ready for a sample service invoice.",
    );
  });
$("reverse").onsubmit = (e) => {
  e.preventDefault();
  action(async () => {
    const target = selected,
      memo = $("reason").value;
    if (
      !lastReversal ||
      lastReversal.target !== target ||
      lastReversal.body.memo !== memo
    )
      lastReversal = {
        target,
        body: { key: key(), memo, expected_revision: snapshot.head },
      };
    const result = await api(
      `/api/entries/${target}/reverse`,
      lastReversal.body,
    );
    selected = result.entry.id;
    lastReversal = null;
    await refresh();
    notice(
      `Reversal #${selected} committed; original #${target} is preserved.`,
    );
  });
};
function download(name, data, mime) {
  const url = URL.createObjectURL(new Blob([data], { type: mime })),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("export").onclick = () =>
  download(
    `journal-balance-r${snapshot.revision}.json`,
    JSON.stringify(snapshot, null, 2),
    "application/json",
  );
$("csv").onclick = () =>
  download(
    `journal-balance-r${snapshot.revision}.csv`,
    [
      "account,type,normal,debit,credit,net_debit,normal_balance",
      ...snapshot.accounts.map((a) =>
        [
          a.id,
          a.type,
          a.normal,
          a.debit,
          a.credit,
          a.netDebit,
          a.normalBalance,
        ].join(","),
      ),
    ].join("\r\n"),
    "text/csv",
  );
action(async () => {
  await refresh();
  prepare();
  notice("Local journal loaded. All amounts are integer minor units.");
});

$("composer-open").onclick = () => {
  $("composer-notice").textContent = "";
  $("composer").showModal();
};
$("composer-close").onclick = () => $("composer").close();
$("entry-close").onclick = () => $("entry-details").close();
for (const view of ["balance", "journal"])
  $(view + "-view").onclick = () => {
    for (const name of ["balance", "journal"]) {
      $(name + "-panel").hidden = name !== view;
      $(name + "-view").setAttribute("aria-pressed", String(name === view));
    }
  };
