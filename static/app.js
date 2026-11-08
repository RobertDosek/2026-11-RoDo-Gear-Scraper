const state = { ads: [], lastRun: null, tab: "watching" };

const panel = document.getElementById("panel");
const rowTpl = document.getElementById("row-tpl");
const czk = new Intl.NumberFormat("cs-CZ", { style: "currency", currency: "CZK", maximumFractionDigits: 0 });
const when = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

const isDrop = (ad) => ad.status === "price" && ad.target_price != null && ad.price_czk < ad.target_price;

// Which tab and group each ad belongs to.
function groups() {
  const g = { new: [], alerts: [], watch: [], waiting: [], ignore: [], sold: [] };
  for (const ad of state.ads) {
    if (ad.status === "new") g.new.push(ad);
    else if (ad.status === "watch") g.watch.push(ad);
    else if (ad.status === "price") (isDrop(ad) || !ad.listed ? g.alerts : g.waiting).push(ad);
    else if (ad.status === "ignore") g.ignore.push(ad);
    else if (ad.status === "sold") g.sold.push(ad);
  }
  g.sold.sort((a, b) => (b.sold_at || "").localeCompare(a.sold_at || ""));
  return g;
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}

async function load() {
  const data = await api("/api/ads");
  state.ads = data.ads;
  state.lastRun = data.last_run;
  render();
}

async function setStatus(ad, status, targetPrice) {
  const before = { status: ad.status, target_price: ad.target_price };
  const updated = await api(`/api/ads/${ad.id}`, {
    method: "POST",
    body: JSON.stringify({ status, target_price: targetPrice ?? null }),
  });
  Object.assign(ad, updated);
  render(ad.id);
  const labels = { watch: "Watching", price: "Watching price of", ignore: "Not watching", sold: "Moved to sold:", new: "Back in new:" };
  showToast(`${labels[status]} ${ad.name}`, async () => {
    Object.assign(ad, await api(`/api/ads/${ad.id}`, { method: "POST", body: JSON.stringify(before) }));
    render(ad.id);
  });
}

/* ---------- rendering ---------- */

function render(changedId) {
  const g = groups();
  const counts = {
    watching: g.new.length + g.alerts.length + g.watch.length,
    waiting: g.waiting.length,
    ignore: g.ignore.length,
    sold: g.sold.length,
  };
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
    b.querySelector(".count").textContent = counts[b.dataset.tab] ? `(${counts[b.dataset.tab]})` : "";
  });
  renderLastRun();

  panel.replaceChildren();
  if (state.tab === "watching") {
    if (!state.ads.length) {
      panel.append(emptyNote("No ads yet. Use Check for new ads to load the boutique listing."));
      return;
    }
    if (g.new.length) panel.append(group("New ads", "Decide what to do with each one.", g.new));
    if (g.alerts.length) panel.append(group("Price alerts", "Under your target price, or gone from the shop.", g.alerts));
    panel.append(group("Watching", null, g.watch, "Nothing watched yet. Pick Watch on an ad to keep it here."));
  } else if (state.tab === "waiting") {
    panel.append(group("Waiting for a price drop",
      "Still above your target price. They move to Price alerts on the Watching tab when the price goes under it.",
      g.waiting, "No price targets set. Pick Price under… on an ad to add one."));
  } else if (state.tab === "ignore") {
    panel.append(group("Not watching", "Ads you skipped. They move to sold automatically when they leave the shop.", g.ignore, "No skipped ads."));
  } else {
    panel.append(group("Sold", "Ads no longer listed on kytary.cz.", g.sold, "Nothing sold yet."));
  }

  if (changedId) {
    const row = panel.querySelector(`[data-id="${changedId}"]`);
    if (row) row.classList.add("just-changed");
  }
}

function renderLastRun() {
  const el = document.getElementById("last-run");
  const r = state.lastRun;
  el.textContent = r
    ? `Last checked ${when.format(new Date(r.run_at))}. ${r.found} ads, ${r.new_ads} new, ${r.gone_ads} gone.`
    : "Not checked yet.";
}

function emptyNote(text) {
  const p = document.createElement("p");
  p.className = "empty";
  p.textContent = text;
  return p;
}

function group(title, note, ads, emptyText) {
  const section = document.createElement("section");
  section.className = "group";
  const h = document.createElement("h2");
  h.textContent = title;
  section.append(h);
  if (note) {
    const p = document.createElement("p");
    p.className = "group-note";
    p.textContent = note;
    section.append(p);
  }
  if (!ads.length) section.append(emptyNote(emptyText || "Nothing here."));
  ads.forEach((ad) => section.append(row(ad)));
  return section;
}

function row(ad) {
  const el = rowTpl.content.firstElementChild.cloneNode(true);
  el.dataset.id = ad.id;
  const gone = !ad.listed;
  el.classList.toggle("is-gone", gone);
  el.classList.toggle("is-drop", isDrop(ad) && !gone);

  const thumb = el.querySelector(".ad-thumb");
  thumb.href = ad.url;
  thumb.setAttribute("aria-label", `Open ${ad.name} on kytary.cz`);
  el.querySelector(".ad-thumb img").src = ad.image_url;

  el.querySelector(".ad-name a").href = ad.url;
  el.querySelector(".ad-brand").textContent = ad.brand;
  el.querySelector(".ad-model").textContent =
    ad.name.startsWith(ad.brand) ? ad.name.slice(ad.brand.length).trim() : ad.name;
  el.querySelector(".ad-type").textContent = ad.type;
  el.querySelector(".ad-id").textContent = ad.id;

  el.querySelector(".price-now").textContent = ad.price_czk != null ? czk.format(ad.price_czk) : "No price";
  if (ad.previous_price && ad.previous_price !== ad.price_czk) {
    el.querySelector(".price-was").textContent = czk.format(ad.previous_price);
  }
  const avail = el.querySelector(".ad-avail");
  if (gone) {
    avail.innerHTML = '<span class="sold-stamp">Sold</span>';
  } else {
    avail.textContent = ad.availability;
  }

  el.querySelector(".ad-controls").append(controls(ad, gone));
  return el;
}

function controls(ad, gone) {
  const wrap = document.createElement("div");
  wrap.className = "controls-stack";

  if (ad.status === "sold") {
    if (ad.listed) wrap.append(choice(ad));
    else wrap.append(small(`Sold ${ad.sold_at ? when.format(new Date(ad.sold_at)) : ""}`));
    return wrap;
  }

  if (gone) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-plain";
    btn.textContent = "Move to sold";
    btn.addEventListener("click", () => setStatus(ad, "sold"));
    wrap.append(btn);
    return wrap;
  }

  wrap.append(choice(ad));
  if (ad.status === "price") wrap.append(targetForm(ad));
  return wrap;
}

function choice(ad) {
  const box = document.createElement("div");
  box.className = "choice";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", `What to do with ${ad.name}`);
  const options = [
    ["watch", "Watch"],
    ["price", "Price under…"],
    ["ignore", "Don't watch"],
  ];
  for (const [status, label] of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.status = status;
    b.textContent = label;
    b.setAttribute("aria-pressed", String(ad.status === status));
    b.addEventListener("click", () => {
      if (status === "price") {
        // Ask for the target first so the row doesn't vanish before a price is set.
        const stack = box.parentElement;
        if (!stack.querySelector(".target")) stack.append(targetForm(ad));
        stack.querySelector(".target input").focus();
      } else if (ad.status !== status) {
        setStatus(ad, status);
      }
    });
    box.append(b);
  }
  return box;
}

function targetForm(ad) {
  const form = document.createElement("form");
  form.className = "target";
  const id = `target-${ad.id}`;
  form.innerHTML = `<label for="${id}">Alert under</label>
    <input id="${id}" type="number" inputmode="numeric" min="1" step="1" required placeholder="Kč">
    <button type="submit" class="btn-plain">Save</button>`;
  const input = form.querySelector("input");
  input.value = ad.target_price ?? "";
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = parseInt(input.value, 10);
    if (value > 0) setStatus(ad, "price", value);
  });
  return form;
}

function small(text) {
  const s = document.createElement("span");
  s.className = "ad-meta";
  s.textContent = text;
  return s;
}

/* ---------- toast ---------- */

const toast = document.getElementById("toast");
let toastTimer;
function showToast(text, undo, isError = false) {
  toast.querySelector(".toast-text").textContent = text;
  toast.classList.toggle("is-error", isError);
  toast.hidden = false;
  toast.querySelector(".toast-undo").onclick = undo
    ? () => { toast.hidden = true; undo().catch((e) => showToast(e.message, null, true)); }
    : null;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, isError ? 8000 : 6000);
}

/* ---------- wiring ---------- */

document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    render();
  })
);

const refreshBtn = document.getElementById("refresh");
refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Checking kytary.cz…";
  try {
    const r = await api("/api/refresh", { method: "POST" });
    await load();
    showToast(`Found ${r.found} ads: ${r.new_ads} new, ${r.gone_ads} gone.`);
  } catch (e) {
    showToast(`Check failed: ${e.message}`, null, true);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Check for new ads";
  }
});

// Errors from row actions surface in the toast instead of failing silently.
window.addEventListener("unhandledrejection", (ev) =>
  showToast(ev.reason?.message || "Something went wrong.", null, true)
);

load().catch((e) => showToast(`Could not load ads: ${e.message}`, null, true));
