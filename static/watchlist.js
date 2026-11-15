/* Shared watchlist page: tabs, ad rows, watch/price/ignore decisions, undo toast.
   Each source page calls createWatchlist(config):
     api          - base path, e.g. "/api/kytary-boutique"
     siteName     - shown in links and notes, e.g. "kytary.cz"
     describe(ad) - { lead, rest, meta: [strings], avail, tags: [strings] } for one row
     filter(ad)   - optional, extra filter (e.g. category) applied before grouping
     emptyText    - shown when nothing has been scraped yet
     afterRender  - optional, called with the groups after each render
*/

const czk = new Intl.NumberFormat("cs-CZ", { style: "currency", currency: "CZK", maximumFractionDigits: 0 });
const when = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}

/* ---------- toast ---------- */

let toastTimer;
function showToast(text, undo, isError = false) {
  const toast = document.getElementById("toast");
  toast.querySelector(".toast-text").textContent = text;
  toast.classList.toggle("is-error", isError);
  toast.classList.toggle("has-undo", Boolean(undo));
  toast.hidden = false;
  toast.querySelector(".toast-undo").onclick = undo
    ? () => { toast.hidden = true; undo().catch((e) => showToast(e.message, null, true)); }
    : null;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, isError ? 8000 : 6000);
}

window.addEventListener("unhandledrejection", (ev) =>
  showToast(ev.reason?.message || "Something went wrong.", null, true)
);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function createWatchlist(config) {
  const state = { ads: [], lastRun: null, tab: "watching" };
  const panel = document.getElementById("panel");
  const rowTpl = document.getElementById("row-tpl");
  const filter = config.filter || (() => true);

  const isDrop = (ad) => ad.status === "price" && ad.target_price != null && ad.price_czk != null && ad.price_czk < ad.target_price;

  function groups() {
    const g = { new: [], alerts: [], watch: [], waiting: [], ignore: [], sold: [] };
    for (const ad of state.ads) {
      if (!filter(ad)) continue;
      if (ad.status === "new") g.new.push(ad);
      else if (ad.status === "watch") g.watch.push(ad);
      else if (ad.status === "price") (isDrop(ad) || !ad.listed ? g.alerts : g.waiting).push(ad);
      else if (ad.status === "ignore") g.ignore.push(ad);
      else if (ad.status === "sold") g.sold.push(ad);
    }
    g.sold.sort((a, b) => (b.sold_at || "").localeCompare(a.sold_at || ""));
    return g;
  }

  async function load() {
    const data = await api(`${config.api}/ads`);
    state.ads = data.ads;
    state.lastRun = data.last_run;
    if (config.onLoad) config.onLoad(data);
    render();
    return data;
  }

  async function setStatus(ad, status, targetPrice) {
    const before = { status: ad.status, target_price: ad.target_price };
    const post = (body) => api(`${config.api}/ads/${encodeURIComponent(ad.id)}`, { method: "POST", body: JSON.stringify(body) });
    Object.assign(ad, await post({ status, target_price: targetPrice ?? null }));
    render(ad.id);
    const name = config.describe(ad).name;
    const labels = { watch: "Watching", price: "Watching price of", ignore: "Not watching", sold: "Moved to sold:", new: "Back in new:" };
    showToast(`${labels[status]} ${name}`, async () => {
      Object.assign(ad, await post(before));
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
    document.querySelectorAll(".tabs button[data-tab]").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
      b.querySelector(".count").textContent = counts[b.dataset.tab] ? `(${counts[b.dataset.tab]})` : "";
    });
    renderLastRun();

    panel.replaceChildren();
    if (state.tab === "watching") {
      if (!state.ads.length) {
        panel.append(el("p", "empty", config.emptyText));
      } else {
        if (g.new.length) panel.append(group("New ads", "Decide what to do with each one.", g.new));
        if (g.alerts.length) panel.append(group("Price alerts", "Under your target price, or gone from the site.", g.alerts));
        panel.append(group("Watching", null, g.watch, "Nothing watched yet. Pick Watch on an ad to keep it here."));
      }
    } else if (state.tab === "waiting") {
      panel.append(group("Waiting for a price drop",
        "Still above your target price. They move to Price alerts on the Watching tab when the price goes under it.",
        g.waiting, "No price targets set. Pick Price under… on an ad to add one."));
    } else if (state.tab === "ignore") {
      panel.append(group("Not watching", "Ads you skipped. They move to sold automatically when they leave the site.", g.ignore, "No skipped ads."));
    } else {
      panel.append(group("Sold", `Ads no longer listed on ${config.siteName}.`, g.sold, "Nothing sold yet."));
    }

    if (changedId) {
      const row = panel.querySelector(`[data-id="${CSS.escape(changedId)}"]`);
      if (row) row.classList.add("just-changed");
    }
    if (config.afterRender) config.afterRender(g);
  }

  function renderLastRun() {
    const out = document.getElementById("last-run");
    const r = state.lastRun;
    if (config.lastRunText) out.textContent = config.lastRunText(r);
    else out.textContent = r
      ? `Last checked ${when.format(new Date(r.run_at))}. ${r.found} ads, ${r.new_ads} new, ${r.gone_ads} gone.`
      : "Not checked yet.";
  }

  function group(title, note, ads, emptyText) {
    const section = el("section", "group");
    section.append(el("h2", null, title));
    if (note) section.append(el("p", "group-note", note));
    if (!ads.length) section.append(el("p", "empty", emptyText || "Nothing here."));
    ads.forEach((ad) => section.append(row(ad)));
    return section;
  }

  function row(ad) {
    const d = config.describe(ad);
    const node = rowTpl.content.firstElementChild.cloneNode(true);
    node.dataset.id = ad.id;
    const gone = !ad.listed;
    node.classList.toggle("is-gone", gone);
    node.classList.toggle("is-drop", isDrop(ad) && !gone);
    node.classList.toggle("is-direct", Boolean(ad.direct));

    const thumb = node.querySelector(".ad-thumb");
    thumb.href = ad.url;
    thumb.setAttribute("aria-label", `Open ${d.name} on ${config.siteName}`);
    const img = node.querySelector(".ad-thumb img");
    if (ad.image_url) img.src = ad.image_url; else img.remove();

    node.querySelector(".ad-name a").href = ad.url;
    node.querySelector(".ad-brand").textContent = d.lead || "";
    node.querySelector(".ad-model").textContent = d.rest || "";
    const meta = node.querySelector(".ad-meta");
    meta.replaceChildren(...(d.meta || []).filter(Boolean).map((m) => el("span", null, m)));
    const tags = node.querySelector(".ad-tags");
    if (tags) {
      // A tag is a string, or { text, cls } for a styled one (e.g. the direct-alert mark).
      if (d.tags?.length) tags.replaceChildren(...d.tags.map((t) => typeof t === "string" ? el("span", "tag", t) : el("span", `tag ${t.cls}`, t.text)));
      else tags.remove();
    }

    node.querySelector(".price-now").textContent = ad.price_czk != null ? czk.format(ad.price_czk) : (d.noPrice || "No price");
    if (ad.price_czk == null) node.querySelector(".price-now").classList.add("is-text");
    if (ad.price_start != null) {
      // The big number is the price now; under it the first price we saw, and the lowest and highest since.
      node.querySelector(".price-was").replaceWith(priceRange(ad));
    } else if (ad.previous_price && ad.previous_price !== ad.price_czk) {
      node.querySelector(".price-was").textContent = czk.format(ad.previous_price);
    }
    const avail = node.querySelector(".ad-avail");
    if (gone) avail.replaceChildren(el("span", "sold-stamp", "Sold"));
    else avail.textContent = d.avail || "";

    node.querySelector(".ad-controls").append(controls(ad, gone, d.name));
    // Short personal note, right under your decision buttons.
    if (config.notes) node.querySelector(".ad-controls").append(noteBlock(ad));
    return node;
  }

  /* Your own note on an ad: shown under the ad, edited in place. */
  function noteBlock(ad) {
    const box = el("div", "ad-note");
    if (ad.note) {
      box.append(el("p", "ad-note-text", ad.note));
      box.append(noteButton("Edit note", () => box.replaceWith(noteEditor(ad))));
    } else {
      box.append(noteButton("Add note", () => box.replaceWith(noteEditor(ad))));
    }
    return box;
  }

  function noteButton(label, onClick) {
    const b = el("button", "btn-link", label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function noteEditor(ad) {
    const form = el("form", "ad-note is-editing");
    const id = `note-${ad.id}`;
    const label = el("label", "visually-hidden", `Note on ${config.describe(ad).name}`);
    label.htmlFor = id;
    const text = el("textarea");
    text.id = id;
    text.rows = 2;
    text.maxLength = 2000;
    text.placeholder = "e.g. asked about the neck";
    text.value = ad.note || "";
    const actions = el("div", "ad-note-actions");
    const save = el("button", "btn-plain", "Save note");
    save.type = "submit";
    const cancel = noteButton("Cancel", () => form.replaceWith(noteBlock(ad)));
    actions.append(save, cancel, el("span", "ad-note-hint", "Ctrl+Enter saves"));
    form.append(label, text, actions);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      save.disabled = true;
      try {
        const r = await api(`${config.api}/ads/${encodeURIComponent(ad.id)}/note`, {
          method: "PUT", body: JSON.stringify({ note: text.value }),
        });
        ad.note = r.note;
        form.replaceWith(noteBlock(ad));
        showToast(ad.note ? "Note saved." : "Note removed.");
      } catch (err) {
        save.disabled = false;
        showToast(`Could not save the note: ${err.message}`, null, true);
      }
    });
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) form.requestSubmit();
      if (e.key === "Escape") form.replaceWith(noteBlock(ad));
    });
    requestAnimationFrame(() => text.focus());
    return form;
  }

  function priceRange(ad) {
    const dl = el("dl", "price-range");
    const changed = ad.price_count > 1;
    dl.classList.toggle("is-flat", !changed);
    if (!changed) dl.title = "The price hasn't changed since the ad was first seen.";
    const rows = [["Start", ad.price_start], ["Min", ad.price_min], ["Max", ad.price_max]];
    for (const [label, value] of rows) {
      const row = el("div");
      if (changed && label === "Min" && ad.price_czk === ad.price_min && ad.price_czk < ad.price_start) row.className = "is-low";
      row.append(el("dt", null, label), el("dd", null, value != null ? czk.format(value) : "–"));
      dl.append(row);
    }
    return dl;
  }

  function controls(ad, gone, name) {
    const wrap = el("div", "controls-stack");
    if (ad.status === "sold") {
      if (ad.listed) wrap.append(choice(ad, name));
      else wrap.append(el("span", "ad-meta", `Sold ${ad.sold_at ? when.format(new Date(ad.sold_at)) : ""}`));
      return wrap;
    }
    if (gone) {
      const btn = el("button", "btn-plain", "Move to sold");
      btn.type = "button";
      btn.addEventListener("click", () => setStatus(ad, "sold"));
      wrap.append(btn);
      return wrap;
    }
    wrap.append(choice(ad, name));
    if (ad.status === "price") wrap.append(targetForm(ad));
    return wrap;
  }

  function choice(ad, name) {
    const box = el("div", "choice");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", `What to do with ${name}`);
    for (const [status, label] of [["watch", "Watch"], ["price", "Price under…"], ["ignore", "Don't watch"]]) {
      const b = el("button", null, label);
      b.type = "button";
      b.dataset.status = status;
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
    const form = el("form", "target");
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

  document.querySelectorAll(".tabs button[data-tab]").forEach((b) =>
    b.addEventListener("click", () => {
      state.tab = b.dataset.tab;
      render();
    })
  );

  return { state, load, render };
}
