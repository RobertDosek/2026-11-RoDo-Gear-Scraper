// Sold gear from every source: one compact row per sale, filters, sorting, a picture on hover,
// and an editor for what you filled in when moving the ad to sold.
// Uses api(), el(), showToast(), czk and when from /static/watchlist.js.

const day = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" });
const state = { items: [], sources: {}, sort: "sold_at", dir: -1 };
const filters = document.getElementById("filters");
const rowsBox = document.getElementById("rows");
const preview = document.getElementById("preview");

const sortValue = (s, key) => s[key];
const folded = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function load() {
  const data = await api("/api/sold");
  state.items = data.items;
  state.sources = data.sources;
  fillOptions();
  render();
}

/* ---------- filters ---------- */

function fillOptions() {
  const sets = {
    brand: [...new Set(state.items.map((s) => s.brand).filter(Boolean))].sort((a, b) => a.localeCompare(b, "cs")),
    where: [...new Set(state.items.map((s) => s.where_sold).filter(Boolean))].sort((a, b) => a.localeCompare(b, "cs")),
  };
  for (const [name, values] of Object.entries(sets)) {
    const select = filters.elements[name];
    const current = select.value;
    select.replaceChildren(new Option("All", ""), ...values.map((v) => new Option(v, v)));
    select.value = [...select.options].some((o) => o.value === current) ? current : "";
  }
  const where = document.getElementById("where-options");
  where.replaceChildren(...[...new Set([...sets.where, "hudebnibazar.cz", "kytary.cz boutique", "bazos.cz", "Facebook Marketplace", "Reverb"])]
    .map((v) => new Option(v, v)));
}

function matches(s) {
  const f = Object.fromEntries(new FormData(filters));
  if (f.q) {
    const hay = folded([s.brand, s.make, s.title, s.note, s.category, s.where_sold].join(" "));
    if (!folded(f.q).split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  if (f.brand && s.brand !== f.brand) return false;
  if (f.where && s.where_sold !== f.where) return false;
  const date = (s.sold_at || "").slice(0, 10);
  if (f.from && date < f.from) return false;
  if (f.to && date > f.to) return false;
  if (f.priced && s.sold_price == null) return false;
  if (f.min !== "" && f.min != null && (s.sold_price == null || s.sold_price < +f.min)) return false;
  if (f.max !== "" && f.max != null && (s.sold_price == null || s.sold_price > +f.max)) return false;
  return true;
}

filters.addEventListener("input", () => render());
filters.addEventListener("reset", () => setTimeout(render));
filters.addEventListener("submit", (e) => e.preventDefault());

/* ---------- sorting ---------- */

document.querySelectorAll(".sold-table th[data-sort] button").forEach((b) =>
  b.addEventListener("click", () => {
    const key = b.parentElement.dataset.sort;
    if (state.sort === key) state.dir = -state.dir;
    else { state.sort = key; state.dir = ["sold_at", "sold_price"].includes(key) ? -1 : 1; }
    render();
  })
);

function compare(a, b) {
  const x = sortValue(a, state.sort), y = sortValue(b, state.sort);
  if (x == null && y == null) return 0;
  if (x == null) return 1;            // empty values always last
  if (y == null) return -1;
  const r = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "cs");
  return r * state.dir;
}

/* ---------- rendering ---------- */

function render() {
  const shown = state.items.filter(matches).sort(compare);
  document.querySelectorAll(".sold-table th[data-sort]").forEach((th) => {
    if (th.dataset.sort === state.sort) th.setAttribute("aria-sort", state.dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });
  rowsBox.replaceChildren(...shown.map(row));
  renderTotals(shown);

  const empty = document.getElementById("empty");
  empty.hidden = shown.length > 0;
  empty.textContent = state.items.length
    ? "Nothing matches these filters."
    : "Nothing sold yet. When an ad you watch is gone, use Move to sold on its watchlist to add it here.";
  document.querySelector(".sold-scroll").hidden = !shown.length;
}

function renderTotals(shown) {
  const priced = shown.map((s) => s.sold_price).filter((p) => p != null).sort((a, b) => a - b);
  const median = priced.length ? (priced.length % 2 ? priced[(priced.length - 1) / 2]
    : Math.round((priced[priced.length / 2 - 1] + priced[priced.length / 2]) / 2)) : null;
  const items = [
    [shown.length === state.items.length ? "Sold" : "Shown", `${shown.length}${shown.length === state.items.length ? "" : ` of ${state.items.length}`}`],
    ["Median price", median != null ? czk.format(median) : "–"],
    ["Total", priced.length ? czk.format(priced.reduce((a, b) => a + b, 0)) : "–"],
  ];
  document.getElementById("totals").replaceChildren(...items.map(([k, v]) => {
    const d = el("div");
    d.append(el("dt", null, k), el("dd", null, v));
    return d;
  }));
}


function cell(text, cls, title) {
  const td = el("td", cls, text ?? "");
  if (title) td.title = title;
  return td;
}

function row(s) {
  const tr = el("tr");
  tr.dataset.id = s.id;
  if (s.image) tr.dataset.image = s.image;

  const make = el("td", "c-make");
  const link = el("a", null, s.make || s.title || "–");
  link.href = s.url || "#";
  link.target = "_blank";
  link.rel = "noopener";
  link.title = s.title || "";
  if (s.image) link.append(el("span", "has-pic", " ◧"));
  make.append(link);


  const edit = el("button", "btn-quiet", "Edit");
  edit.type = "button";
  edit.setAttribute("aria-label", `Edit ${s.brand || ""} ${s.make || s.title || ""}`);
  edit.addEventListener("click", () => openEditor(s));
  const actions = el("td", "c-actions");
  actions.append(edit);

  tr.append(
    cell(s.sold_at ? day.format(new Date(s.sold_at)) : "", "c-date", s.sold_at ? when.format(new Date(s.sold_at)) : ""),
    cell(s.brand, "c-brand"),
    make,
    cell(s.category, "c-cat", s.category),
    cell(s.where_sold, "c-where", s.where_sold),
    cell(s.sold_price != null ? czk.format(s.sold_price) : "", "num c-sold"),
    cell(s.note, "c-note"),
    actions,
  );
  return tr;
}

/* ---------- on hover (rows stay one line high): the whole note over the Note column,
   the picture anywhere else in the row ---------- */

const notePreview = document.getElementById("note-preview");

function place(box, x, y) {
  const w = box.offsetWidth, h = box.offsetHeight, gap = 18;
  const left = x + gap + w > innerWidth ? x - gap - w : x + gap;
  const top = Math.min(Math.max(8, y - h / 2), innerHeight - h - 8);
  box.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, top)}px)`;
}

function hidePreviews() {
  preview.hidden = true;
  notePreview.hidden = true;
}

function showPreview(target, x, y) {
  const tr = target.closest("tr");
  const noteCell = target.closest(".c-note");
  if (noteCell) {
    preview.hidden = true;
    if (!noteCell.textContent) { notePreview.hidden = true; return; }
    if (notePreview.textContent !== noteCell.textContent) notePreview.textContent = noteCell.textContent;
    notePreview.hidden = false;
    place(notePreview, x, y);
    return;
  }
  notePreview.hidden = true;
  if (!tr?.dataset.image) { preview.hidden = true; return; }
  const img = preview.querySelector("img");
  if (img.getAttribute("src") !== tr.dataset.image) img.src = tr.dataset.image;
  preview.hidden = false;
  place(preview, x, y);
}

rowsBox.addEventListener("mousemove", (e) => showPreview(e.target, e.clientX, e.clientY));
rowsBox.addEventListener("mouseleave", hidePreviews);
rowsBox.addEventListener("focusin", (e) => {
  const r = e.target.getBoundingClientRect();
  showPreview(e.target, r.right, r.top + r.height / 2);
});
rowsBox.addEventListener("focusout", hidePreviews);
preview.querySelector("img").addEventListener("error", () => { preview.hidden = true; });
window.addEventListener("scroll", hidePreviews, { passive: true });

/* ---------- editor ---------- */

const editor = document.getElementById("editor");
let editing = null;

function openEditor(s) {
  editing = s;
  const f = editor.querySelector("form");
  editor.querySelector(".sold-editor-ad").textContent = `${s.title || ""} · ${s.source_name}`;
  for (const k of ["brand", "make", "sold_price", "where_sold", "note"]) f.elements[k].value = s[k] ?? "";
  f.elements.sold_at.value = (s.sold_at || "").slice(0, 10);
  if (s.asking_price != null) f.elements.sold_price.placeholder = `asked ${czk.format(s.asking_price)}`;
  editor.showModal();
}

editor.addEventListener("close", async () => {
  if (editor.returnValue !== "save" || !editing) return;
  const f = editor.querySelector("form");
  const body = Object.fromEntries(["brand", "make", "sold_price", "where_sold", "note"].map((k) => [k, f.elements[k].value]));
  // Keep the time of day when only other fields change.
  if (f.elements.sold_at.value && f.elements.sold_at.value !== (editing.sold_at || "").slice(0, 10)) body.sold_at = f.elements.sold_at.value;
  try {
    const updated = await api(`/api/sold/${editing.id}`, { method: "PUT", body: JSON.stringify(body) });
    Object.assign(editing, updated);
    fillOptions();
    render();
    showToast("Saved.");
  } catch (e) {
    showToast(`Could not save: ${e.message}`, null, true);
  }
});
editor.addEventListener("cancel", () => { editor.returnValue = "cancel"; });

document.getElementById("delete").addEventListener("click", async () => {
  const s = editing;
  if (!s || !confirm(`Remove ${s.brand || ""} ${s.make || s.title || ""} from the sold list? The ad stays sold on its watchlist.`)) return;
  editor.returnValue = "cancel";
  editor.close();
  try {
    await api(`/api/sold/${s.id}`, { method: "DELETE" });
    state.items = state.items.filter((x) => x.id !== s.id);
    fillOptions();
    render();
    showToast("Removed from the sold list.");
  } catch (e) {
    showToast(`Could not remove: ${e.message}`, null, true);
  }
});

load().catch((e) => showToast(`Could not load the sold list: ${e.message}`, null, true));
