// hudebnibazar.cz: ads found by the saved keywords, with a category filter and background searches.

let groupNames = {};
let category = "all";
let groupList = [];

// What happened to an ad on the direct level (phone alert / automatic answer).
const DIRECT_TEXT = {
  muted: "direct (mode was off)", notified: "sent to phone", waiting: "answer waiting", sending: "sending answer",
  replied: "answered", dry_run: "answer (dry run)", skipped: "skipped", failed: "answer failed",
};

// Brands to spot in ad titles when moving an ad to sold. Longer names first ("Gibson Custom" before "Gibson").
const KNOWN_BRANDS = ["Squier", "Fender", "Gibson", "Epiphone", "PRS", "Ibanez", "Gretsch", "Rickenbacker", "Jackson",
  "ESP", "LTD", "Schecter", "Yamaha", "Martin", "Taylor", "Guild", "Takamine", "Washburn", "Charvel", "G&L", "Music Man",
  "Sterling", "Duesenberg", "Hagstrom", "Harley Benton", "Cort", "Godin", "Larrivee", "Furch", "Lowden", "Eastman",
  "Marshall", "Vox", "Orange", "Mesa Boogie", "Mesa", "Peavey", "Fractal", "Kemper", "Boss", "Strymon", "Electro-Harmonix",
  "MXR", "Seymour Duncan", "DiMarzio", "EMG", "Jolana", "Grover", "Gotoh", "Schaller", "Hiwatt", "Laney", "Blackstar",
  "Two Rock", "Friedman", "Bogner", "Supro", "Danelectro", "Burny", "Fernandes", "Tokai", "Greco"];

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
    tags: [...(ad.direct ? [{ text: `⚡ ${DIRECT_TEXT[ad.direct.state] || ad.direct.state}`, cls: "tag-direct" }] : []),
      ...ad.keywords.map((k) => k.phrase)],
    avail: ad.posted ? `Posted ${ad.posted}` : "",
    noPrice: ad.price_text || "No price",
  }),
  // A first guess you can correct: a known brand anywhere in the title, else the title's first word.
  // The description is the whole text from the ad's page, saved when you watched it; before that is
  // saved, the ~100-character start from the listing.
  saleDefaults: (ad) => {
    const description = ad.full_description || ad.description || "";
    const title = (ad.title || "").trim();
    const known = KNOWN_BRANDS.find((b) => new RegExp(`(^|[^\\p{L}])${b}([^\\p{L}]|$)`, "iu").test(title));
    if (known) {
      const make = title.replace(new RegExp(known, "i"), " ").replace(/\s+/g, " ").trim();
      return { brand: known, make, description };
    }
    const [brand, ...rest] = title.split(/\s+/);
    return { brand, make: rest.join(" "), description };
  },
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

/* Background scans (the hourly task and the quick direct scans) add ads while the page is open, so
   look for them quietly: every minute while the tab is visible, and when you come back to it.
   Skipped while you're typing (a note, a target price) so a refresh never eats your input. */
const AUTO_REFRESH_MS = 60_000;
let lastRefresh = Date.now();

async function autoRefresh() {
  if (document.hidden || polling) return;
  const typing = document.activeElement?.closest?.("#panel input, #panel textarea, #panel select");
  if (typing) return;
  lastRefresh = Date.now();
  try {
    const job = await api("/api/hudebnibazar/job");
    if (!job.running) await list.load();   // mid-scan: wait for the next round, no toast spam
    else lastRefresh = 0;
  } catch { /* the app may be restarting; try again next time */ }
}

setInterval(autoRefresh, AUTO_REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - lastRefresh > 15_000) autoRefresh();
});
