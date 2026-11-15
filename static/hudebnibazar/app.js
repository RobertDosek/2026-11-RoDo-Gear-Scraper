// hudebnibazar.cz: ads found by the saved keywords, with a category filter and background searches.

let groupNames = {};
let category = "all";
let groupList = [];

const list = createWatchlist({
  api: "/api/hudebnibazar",
  siteName: "hudebnibazar.cz",
  emptyText: "",
  notes: true,
  filter: (ad) => category === "all" || ad.groups.includes(category),
  describe: (ad) => ({
    name: ad.title,
    lead: ad.title,
    meta: [category === "all" ? ad.groups.map((g) => groupNames[g]).join(", ") : null, ad.location, ad.seller],
    tags: ad.keywords.map((k) => k.phrase),
    avail: ad.posted ? `Posted ${ad.posted}` : "",
    noPrice: ad.price_text || "No price",
  }),
  onLoad: (data) => {
    groupList = data.groups;
    groupNames = Object.fromEntries(data.groups.map((g) => [g.slug, g.name]));
    showJob(data.job);
  },
  lastRunText: (r) => r
    ? `Last scanned ${when.format(new Date(r.finished_at))}.`
    : "No category scanned yet.",
  afterRender: () => {
    renderFilters();
    if (!list.state.ads.length && list.state.tab === "watching") {
      const p = el("p", "empty");
      p.append("No ads yet. ");
      const a = el("a", null, "Add keywords");
      a.href = "/hudebnibazar/keywords";
      p.append(a, " and scan their categories there.");
      document.getElementById("panel").replaceChildren(p);
    }
  },
});

/* Category chips: one per group that has ads, with counts of ads still listed. */
function renderFilters() {
  const box = document.getElementById("filters");
  const counts = {};
  for (const ad of list.state.ads) for (const g of ad.groups) counts[g] = (counts[g] || 0) + 1;
  const options = [["all", "All categories", list.state.ads.length],
    ...groupList.filter((g) => counts[g.slug]).map((g) => [g.slug, g.name, counts[g.slug]])];
  if (options.length <= 2) { box.replaceChildren(); return; }
  box.replaceChildren(...options.map(([slug, name, n]) => {
    const b = el("button", "chip");
    b.type = "button";
    b.setAttribute("aria-pressed", String(category === slug));
    b.append(name, " ", el("span", "count", String(n)));
    b.addEventListener("click", () => { category = slug; list.render(); });
    return b;
  }));
}

/* ---------- background search ---------- */

const refreshBtn = document.getElementById("refresh");
const recheckBtn = document.getElementById("recheck");
let polling = null;

function showJob(job) {
  const running = job && job.running;
  refreshBtn.disabled = running;
  recheckBtn.disabled = running;
  refreshBtn.textContent = running && job.current?.mode !== "recheck" ? "Scanning…" : "Scan new ads";
  recheckBtn.textContent = running && job.current?.mode === "recheck" ? "Checking…" : "Check my ads";
  if (running) {
    const waiting = job.queue?.length ? ` ${job.queue.length} more waiting.` : "";
    document.getElementById("last-run").textContent = (job.message || "Scanning…") + waiting;
    if (!polling) polling = setTimeout(poll, 1500);
  }
}

async function poll() {
  polling = null;
  const job = await api("/api/hudebnibazar/job");
  if (job.running) { showJob(job); return; }
  await list.load();
  const all = (job.results || []).filter((x) => !x.skipped);
  const check = all.find((x) => x.mode === "recheck");
  const r = all.filter((x) => x.mode !== "recheck");
  const failed = r.filter((x) => x.error).map((x) => groupNames[x.group_slug] || x.group_slug);
  if (job.error || failed.length) showToast(`Scan failed${failed.length ? ` in ${failed.join(", ")}` : ""}. See Keywords for details.`, null, true);
  else if (check) showToast(recheckText(check), null, Boolean(check.error));
  else if (r.length) showToast(`Scan finished in ${r.length === 1 ? groupNames[r[0].group_slug] : `${r.length} categories`}. Your keyword matches are up to date.`);
}

function recheckText(c) {
  const parts = [`${c.price_changes} price change${c.price_changes === 1 ? "" : "s"}`, `${c.gone_ads} sold`];
  return `Checked ${c.checked} of your ads: ${parts.join(", ")}.${c.error ? ` ${c.error}.` : ""}`;
}

recheckBtn.addEventListener("click", async () => {
  try {
    showJob(await api("/api/hudebnibazar/scan", { method: "POST", body: JSON.stringify({ mode: "recheck" }) }));
  } catch (e) {
    showToast(`Check failed: ${e.message}`, null, true);
  }
});

refreshBtn.addEventListener("click", async () => {
  try {
    showJob(await api("/api/hudebnibazar/scan", { method: "POST", body: JSON.stringify({ group: null, mode: "new" }) }));
  } catch (e) {
    showToast(`Scan failed: ${e.message}`, null, true);
  }
});

list.load().catch((e) => showToast(`Could not load ads: ${e.message}`, null, true));
