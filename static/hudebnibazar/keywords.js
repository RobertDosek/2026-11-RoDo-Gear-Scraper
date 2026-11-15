// Keywords per category (add, edit in place, delete) and category scans (new ads / full).

const KW_API = "/api/hudebnibazar/keywords";
const state = { keywords: [], groups: [], job: null };
const groupsBox = document.getElementById("groups");
let uid = 0;

const splitWords = (text) => text.split(",").map((s) => s.trim()).filter(Boolean);
const priceValue = (input) => (input.value.trim() === "" ? null : parseInt(input.value, 10));
const groupName = (slug) => state.groups.find((g) => g.slug === slug)?.name || slug;

async function load() {
  const data = await api(KW_API);
  state.keywords = data.keywords;
  state.groups = data.groups;
  render();
  showJob(data.job);
}

function render() {
  groupsBox.replaceChildren(...state.groups.map(groupSection));
}

/* ---------- category sections ---------- */

function groupSection(g) {
  const section = el("section", "kw-group");
  section.id = `group-${g.slug}`;
  const head = el("div", "kw-group-head");
  const title = el("div", "kw-group-title");
  title.append(el("h2", null, g.name));
  const links = el("p", "kw-sources");
  g.urls.forEach((u, i) => {
    const a = el("a", null, new URL(u).pathname.split("/")[1].replaceAll("-", " "));
    a.href = u; a.target = "_blank"; a.rel = "noopener";
    if (i) links.append(" and ");
    links.append(a);
  });
  title.append(links);
  head.append(title, scanBox(g));
  section.append(head);

  const kws = state.keywords.filter((k) => k.group_slug === g.slug);
  const table = el("div", "kw-table");
  table.setAttribute("role", "list");
  if (kws.length) table.append(headerRow());
  kws.forEach((k) => table.append(keywordRow(k, g)));
  table.append(addRow(g, !kws.length));
  section.append(table);
  return section;
}

function scanBox(g) {
  const box = el("div", "kw-scan");
  box.dataset.group = g.slug;
  const status = el("p", "kw-scan-status");
  status.setAttribute("aria-live", "polite");
  box.append(status, scanButtons(g));
  fillScanStatus(status, g);
  return box;
}

function scanButtons(g) {
  const wrap = el("div", "kw-scan-actions");
  for (const [mode, label] of [["new", "Scan new ads"], ["full", "Full scan"]]) {
    const b = el("button", mode === "full" ? "btn-quiet" : "btn-plain", label);
    b.type = "button";
    b.dataset.mode = mode;
    b.setAttribute("aria-label", `${label} in ${g.name}`);
    b.addEventListener("click", () => startScan(g.slug, mode));
    wrap.append(b);
  }
  return wrap;
}

function fillScanStatus(out, g) {
  const job = state.job;
  const current = job?.current?.group === g.slug ? job.current : null;
  const queued = job?.queue?.find((t) => t.group === g.slug);
  const lines = [];
  if (current) lines.push(["is-busy", job.message || "Scanning…"]);
  else if (queued) lines.push(["is-busy", `Waiting to ${queued.mode === "full" ? "run a full scan" : "scan new ads"}`]);
  if (!g.last_scan && !current) {
    lines.push(["is-warn", "Not scanned yet. Run a full scan to load this category."]);
  } else if (g.last_scan) {
    const s = g.last_scan;
    if (s.error) lines.push(["is-warn", `Last scan failed ${when.format(new Date(s.finished_at))}: ${s.error}`]);
    else lines.push(["", `Last scan ${when.format(new Date(s.finished_at))}, ${s.mode === "full" ? "full" : "new ads"}: ${s.new_ads} new${s.gone_ads ? `, ${s.gone_ads} gone` : ""}`]);
    lines.push(["", g.last_full ? `Last full scan ${when.format(new Date(g.last_full.finished_at))}, ${g.listed} ads listed` : "No full scan yet"]);
  }
  out.replaceChildren(...lines.map(([cls, text]) => el("span", cls, text)));
  const box = out.parentElement;
  if (box) box.querySelectorAll("button").forEach((b) => { b.disabled = Boolean(current); });
}

async function startScan(group, mode) {
  try {
    showJob(await api("/api/hudebnibazar/scan", { method: "POST", body: JSON.stringify({ group, mode }) }));
  } catch (e) {
    showToast(e.message, null, true);
  }
}

/* ---------- keyword rows ---------- */

function headerRow() {
  const r = el("div", "kw-row kw-head");
  r.setAttribute("aria-hidden", "true");
  ["Look for in title", "Leave out titles with", "Price from", "Price to", "Matching ads", ""].forEach((t) => r.append(el("span", null, t)));
  return r;
}

/* The four inputs shared by existing rows and the add row. */
function fields(k, labelled) {
  const n = ++uid;
  const make = (cls, label, attrs) => {
    const wrap = el("label", `kw-field kw-${cls}`);
    const span = el("span", labelled ? "kw-label" : "kw-label visually-hidden", label);
    const input = el("input");
    Object.assign(input, attrs);
    input.id = `kw-${cls}-${n}`;
    wrap.append(span, input);
    return { wrap, input };
  };
  const phrase = make("phrase", "Look for in title", { type: "text", value: k?.phrase ?? "", placeholder: "e.g. Fender", autocomplete: "off" });
  const excl = make("excl", "Leave out titles with", { type: "text", value: (k?.excludes ?? []).join(", "), placeholder: "e.g. Jazzmaster, Squier", autocomplete: "off" });
  const min = make("min", "Price from (Kč)", { type: "number", min: 0, step: 1, inputMode: "numeric", value: k?.min_price ?? "", placeholder: "Kč" });
  const max = make("max", "Price to (Kč)", { type: "number", min: 0, step: 1, inputMode: "numeric", value: k?.max_price ?? "", placeholder: "Kč" });
  return { phrase, excl, min, max };
}

function payload(f) {
  return {
    phrase: f.phrase.input.value,
    excludes: splitWords(f.excl.input.value),
    min_price: priceValue(f.min.input),
    max_price: priceValue(f.max.input),
  };
}

function keywordRow(k, g) {
  const form = el("form", "kw-row");
  form.setAttribute("role", "listitem");
  form.dataset.id = k.id;
  const f = fields(k, false);
  f.phrase.input.required = true;
  form.append(f.phrase.wrap, f.excl.wrap, f.min.wrap, f.max.wrap, resultCell(k, g));

  const actions = el("div", "kw-actions");
  const save = el("button", "btn-plain btn-save", "Save");
  save.type = "submit";
  save.hidden = true;
  const del = el("button", "btn-quiet btn-danger", "Delete");
  del.type = "button";
  del.setAttribute("aria-label", `Delete keyword ${k.phrase}`);
  actions.append(save, del);
  form.append(actions);

  form.addEventListener("input", () => {
    save.hidden = false;
    form.classList.add("is-dirty");
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const updated = await api(`${KW_API}/${k.id}`, { method: "PUT", body: JSON.stringify(payload(f)) });
      await load();
      showToast(`Saved “${updated.phrase}”.`);
    } catch (err) {
      showToast(err.message, null, true);
    }
  });
  del.addEventListener("click", async () => {
    await api(`${KW_API}/${k.id}`, { method: "DELETE" });
    await load();
    showToast(`Deleted “${k.phrase}”.`, async () => {
      await api(KW_API, { method: "POST", body: JSON.stringify({ group_slug: k.group_slug, phrase: k.phrase, excludes: k.excludes, min_price: k.min_price, max_price: k.max_price }) });
      await load();
    });
  });
  return form;
}

function resultCell(k, g) {
  const cell = el("div", "kw-result");
  if (!g.last_scan) {
    cell.append(el("strong", null, "–"), el("span", null, "Category not scanned"));
    return cell;
  }
  const hidden = k.matched - k.shown;
  cell.append(el("strong", null, `${k.shown} shown`));
  cell.append(el("span", null, hidden > 0 ? `${hidden} filtered out` : k.matched ? "none filtered out" : "no ads match"));
  return cell;
}

function addRow(g, isFirst) {
  const form = el("form", "kw-row kw-add");
  const f = fields(null, true);
  f.phrase.input.required = true;
  f.phrase.input.placeholder = isFirst ? "Add your first keyword, e.g. Fender" : "Add another keyword";
  const actions = el("div", "kw-actions");
  const add = el("button", "btn-plain", "Add keyword");
  add.type = "submit";
  actions.append(add);
  form.append(f.phrase.wrap, f.excl.wrap, f.min.wrap, f.max.wrap, el("div", "kw-result"), actions);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const k = await api(KW_API, { method: "POST", body: JSON.stringify({ group_slug: g.slug, ...payload(f) }) });
      await load();
      document.querySelector(`#group-${g.slug} .kw-add .kw-phrase input`)?.focus();
      const fresh = state.keywords.find((x) => x.id === k.id);
      const scanned = state.groups.find((x) => x.slug === g.slug)?.last_scan;
      showToast(scanned
        ? `Added “${k.phrase}”: ${fresh?.shown ?? 0} matching ads from the last scan.`
        : `Added “${k.phrase}”. Run a full scan of ${g.name} to find its ads.`);
    } catch (err) {
      showToast(err.message, null, true);
    }
  });
  return form;
}

/* ---------- background scan status ---------- */

const refreshBtn = document.getElementById("refresh");
const jobOut = document.getElementById("job");
let polling = null;
let wasRunning = false;

function showJob(job) {
  state.job = job;
  const running = Boolean(job && job.running);
  refreshBtn.disabled = running;
  refreshBtn.textContent = running ? "Scanning…" : "Scan new ads everywhere";
  const waiting = job?.queue?.length ? ` ${job.queue.length} more waiting.` : "";
  jobOut.textContent = running ? `${job.message || "Scanning…"}${waiting}` : "";
  document.querySelectorAll(".kw-scan").forEach((box) => {
    const g = state.groups.find((x) => x.slug === box.dataset.group);
    if (g) fillScanStatus(box.querySelector(".kw-scan-status"), g);
  });
  if (running) {
    wasRunning = true;
    if (!polling) polling = setTimeout(poll, 1500);
  }
}

async function poll() {
  polling = null;
  const job = await api("/api/hudebnibazar/job");
  if (job.running) {
    // Pick up finished categories as the queue moves on.
    if (job.results.length !== (state.job?.results?.length ?? 0)) await refreshData(job);
    showJob(job);
    return;
  }
  await refreshData(job);
  showJob(job);
  if (wasRunning) {
    wasRunning = false;
    const r = job.results.filter((x) => !x.skipped && x.mode !== "recheck");
    const failed = r.filter((x) => x.error);
    const newAds = r.reduce((n, x) => n + (x.new_ads || 0), 0);
    if (failed.length) showToast(`Scan failed in ${failed.map((x) => groupName(x.group_slug)).join(", ")}.`, null, true);
    else if (r.length) showToast(`Scan finished: ${newAds} new ads in ${r.length === 1 ? groupName(r[0].group_slug) : `${r.length} categories`}.`);
  }
}

/* Reload counts and statuses without wiping a row that is being edited. */
async function refreshData(job) {
  const data = await api(KW_API);
  state.keywords = data.keywords;
  state.groups = data.groups;
  state.job = job;
  if (!document.querySelector(".kw-row.is-dirty, .kw-add input:focus")) { render(); return; }
  for (const k of data.keywords) {
    const cell = groupsBox.querySelector(`.kw-row[data-id="${k.id}"] .kw-result`);
    const g = data.groups.find((x) => x.slug === k.group_slug);
    if (cell && g) cell.replaceWith(resultCell(k, g));
  }
}

refreshBtn.addEventListener("click", () => startScan(null, "new"));

load().catch((e) => showToast(`Could not load keywords: ${e.message}`, null, true));
