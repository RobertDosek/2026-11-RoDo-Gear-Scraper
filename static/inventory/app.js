// Inventory: gear you bought, typed in by hand. One row per item, filters, sorting, totals for what
// is shown, and an editor dialog for adding and changing items.
// Uses api(), el(), showToast() and czk from /static/watchlist.js.

const day = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" });
const state = { items: [], sort: "bought_at", dir: -1 };
const filters = document.getElementById("filters");
const rowsBox = document.getElementById("rows");

const MONEY = ["bought_price", "buy_costs", "extra_costs", "sell_min", "sell_expected", "sell_max", "sold_price"];
const FIELDS = ["category", "brand", "model", "bought_at", "listed_at", "sold_at", "note", ...MONEY];
const DEFAULT_CATEGORIES = ["Electric guitar", "Acoustic guitar", "Amp", "Cabinet", "Speaker", "Effect pedal", "Hardware", "Pickups", "Other"];
const DATE_SORTS = ["bought_at", "listed_at", "sold_at"];
const today = () => new Date().toISOString().slice(0, 10);
const daysBetween = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / 86400000);

const folded = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const money = (v) => (v == null ? "" : czk.format(v));
const sum = (list, key) => list.reduce((a, i) => a + (i[key] ?? 0), 0);
const has = (list, key) => list.some((i) => i[key] != null);
const sortedSet = (values) => [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, "cs"));

async function load() {
  const data = await api("/api/inventory");
  state.items = data.items;
  fillOptions();
  render();
}

/* ---------- filters ---------- */

function fillOptions() {
  const sets = { category: sortedSet(state.items.map((i) => i.category)), brand: sortedSet(state.items.map((i) => i.brand)) };
  for (const [name, values] of Object.entries(sets)) {
    const select = filters.elements[name];
    const current = select.value;
    select.replaceChildren(new Option("All", ""), ...values.map((v) => new Option(v, v)));
    select.value = [...select.options].some((o) => o.value === current) ? current : "";
  }
  document.getElementById("category-options").replaceChildren(
    ...sortedSet([...sets.category, ...DEFAULT_CATEGORIES]).map((v) => new Option(v, v)));
  document.getElementById("brand-options").replaceChildren(...sets.brand.map((v) => new Option(v, v)));
}

function matches(i) {
  const f = Object.fromEntries(new FormData(filters));
  if (f.q) {
    const hay = folded([i.category, i.brand, i.model, i.note].join(" "));
    if (!folded(f.q).split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  if (f.status === "stock" && i.sold) return false;
  if (f.status === "sold" && !i.sold) return false;
  if (f.status === "forsale" && (i.sold || !i.for_sale)) return false;
  if (f.status === "notlisted" && (i.sold || i.for_sale)) return false;
  if (f.category && i.category !== f.category) return false;
  if (f.brand && i.brand !== f.brand) return false;
  const bought = i.bought_at || "", sold = i.sold_at || "";
  if (f.bfrom && (!bought || bought < f.bfrom)) return false;
  if (f.bto && (!bought || bought > f.bto)) return false;
  if (f.sfrom && (!sold || sold < f.sfrom)) return false;
  if (f.sto && (!sold || sold > f.sto)) return false;
  return true;
}

filters.addEventListener("input", () => render());
filters.addEventListener("reset", () => setTimeout(render));
filters.addEventListener("submit", (e) => e.preventDefault());

/* ---------- sorting ---------- */

document.querySelectorAll(".inv-table th[data-sort] button").forEach((b) =>
  b.addEventListener("click", () => {
    const key = b.dataset.key || b.parentElement.dataset.sort;
    if (state.sort === key) state.dir = -state.dir;
    else {
      state.sort = key;
      state.dir = DATE_SORTS.includes(key) || MONEY.includes(key) || key.includes("profit") ? -1 : 1;
    }
    render();
  })
);

function compare(a, b) {
  const x = a[state.sort], y = b[state.sort];
  if (x == null && y == null) return b.id - a.id;
  if (x == null) return 1;            // empty values always last
  if (y == null) return -1;
  const r = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "cs");
  return r * state.dir || b.id - a.id;
}

/* ---------- rendering ---------- */

function render() {
  const shown = state.items.filter(matches).sort(compare);
  document.querySelectorAll(".inv-table th[data-sort]").forEach((th) => {
    if (th.dataset.sort.split(" ").includes(state.sort)) th.setAttribute("aria-sort", state.dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });
  // In the stacked headers only the button that sorts shows the arrow.
  document.querySelectorAll(".inv-table th button[data-key]").forEach((b) => {
    if (b.dataset.key === state.sort) b.dataset.dir = state.dir > 0 ? "asc" : "desc";
    else delete b.dataset.dir;
  });
  rowsBox.replaceChildren(...shown.map(row));
  renderFoot(shown);
  renderSummary(shown);

  const empty = document.getElementById("empty");
  empty.hidden = shown.length > 0;
  empty.textContent = state.items.length
    ? "Nothing matches these filters."
    : "Nothing here yet. Use Add item to write down gear you bought.";
  document.querySelector(".sold-scroll").hidden = !shown.length;
}

function stat(label, value, cls, title) {
  const d = el("div", cls);
  if (title) d.title = title;
  d.append(el("dt", null, label), el("dd", null, value));
  return d;
}

const signed = (v) => (v > 0 ? "is-gain" : v < 0 ? "is-loss" : "");

function renderSummary(shown) {
  const stock = shown.filter((i) => !i.sold);
  const sold = shown.filter((i) => i.sold);
  const all = shown.length === state.items.length;

  const withExpected = stock.filter((i) => i.sell_expected != null);
  const expectedProfit = withExpected.reduce((a, i) => a + i.sell_expected - (i.cost ?? 0), 0);
  const range = has(stock, "sell_min") || has(stock, "sell_max")
    ? `${money(sum(stock, "sell_min"))} – ${money(sum(stock, "sell_max"))}` : null;

  document.getElementById("sum-stock").replaceChildren(
    stat("Items", String(stock.length)),
    stat("For sale", String(stock.filter((i) => i.for_sale).length)),
    stat("Cost", money(sum(stock, "cost")), null, "Bought for + buy costs + extra costs of the items in stock"),
    stat("Expected sale", has(stock, "sell_expected") ? money(sum(stock, "sell_expected")) : "–", null,
      range ? `Min – max: ${range}` : "Fill in expected prices to see this"),
    stat("Expected profit", withExpected.length ? money(expectedProfit) : "–", signed(expectedProfit),
      withExpected.length < stock.length ? `From the ${withExpected.length} of ${stock.length} items with an expected price` : "Expected sale − cost"),
  );

  const netProfit = sum(sold, "net_profit");
  document.getElementById("sum-sold").replaceChildren(
    stat("Items", String(sold.length)),
    stat("Revenue", has(sold, "sold_price") ? money(sum(sold, "sold_price")) : "–"),
    stat("Profit", has(sold, "profit") ? money(sum(sold, "profit")) : "–", signed(sum(sold, "profit")), "Sold for − bought for"),
    stat("Net profit", has(sold, "net_profit") ? money(netProfit) : "–", signed(netProfit), "Sold for − bought for − buy costs − extra costs"),
  );
  document.querySelector(".inv-summary").classList.toggle("is-filtered", !all);
}

function renderFoot(shown) {
  const foot = document.getElementById("foot");
  if (!shown.length) { foot.replaceChildren(); return; }
  const tr = el("tr");
  const label = el("td", "inv-foot-label", shown.length === state.items.length
    ? `Total · ${shown.length} ${shown.length === 1 ? "item" : "items"}`
    : `Total · ${shown.length} of ${state.items.length} items`);
  label.colSpan = 4;
  tr.append(label);
  const sumCell = (key, cls = "") => {
    const td = el("td", `num ${cls}`, has(shown, key) ? money(sum(shown, key)) : "");
    if (cls === "profit") td.classList.add(...[signed(sum(shown, key))].filter(Boolean));
    return td;
  };
  const totals = Object.fromEntries(MONEY.map((k) => [k, has(shown, k) ? sum(shown, k) : null]));
  tr.append(stackCell(COST_LINES, totals), stackCell(SELL_LINES, totals, "inv-plan"),
    el("td"), el("td"), sumCell("sold_price"), sumCell("profit", "profit"), sumCell("net_profit", "profit"), el("td"));
  foot.replaceChildren(tr);
}

// Columns that hold three values under each other: [field, small label, class of the line]
const COST_LINES = [["bought_price", "", "is-main"], ["buy_costs", "+ buy", ""], ["extra_costs", "+ extra", ""]];
const SELL_LINES = [["sell_min", "min", ""], ["sell_expected", "exp", "is-main"], ["sell_max", "max", ""]];

function stackCell(lines, values, cls = "") {
  const td = el("td", `num inv-stack ${cls}`);
  if (lines.every(([k]) => values[k] == null)) return td;
  for (const [key, label, lineCls] of lines) {
    const line = el("span", `stack-line ${lineCls}`);
    line.append(el("span", "stack-label", label), el("span", "stack-value", values[key] == null ? "–" : money(values[key])));
    if (values[key] == null) line.classList.add("is-empty");
    td.append(line);
  }
  return td;
}

function cell(text, cls, title) {
  const td = el("td", cls, text ?? "");
  if (title) td.title = title;
  return td;
}

function profitCell(v) {
  const td = cell(money(v), "num c-profit");
  const s = signed(v);
  if (s) td.classList.add(s);
  return td;
}

// For sale: a checkbox you can tick right in the table, and the date it went up for sale.
function forSaleCell(i) {
  const td = el("td", "c-forsale");
  if (i.sold) {
    td.textContent = i.listed_at ? day.format(new Date(i.listed_at)) : "";
    td.classList.add("is-past");
    if (i.listed_at && i.sold_at) td.title = `Sold after ${Math.max(0, daysBetween(i.listed_at, i.sold_at))} days for sale`;
    return td;
  }
  const label = el("label");
  const box = el("input");
  box.type = "checkbox";
  box.checked = i.for_sale;
  box.setAttribute("aria-label", `${i.brand || ""} ${i.model || ""} is for sale`);
  box.addEventListener("change", () => toggleForSale(i, box));
  label.append(box);
  if (i.for_sale && i.listed_at) {
    label.append(el("span", null, day.format(new Date(i.listed_at))));
    const d = daysBetween(i.listed_at, today());
    td.title = d > 0 ? `For sale ${d} ${d === 1 ? "day" : "days"}` : "For sale since today";
  }
  td.append(label);
  return td;
}

async function toggleForSale(i, box) {
  const body = { for_sale: box.checked };
  if (box.checked && !i.listed_at) body.listed_at = today();
  box.disabled = true;
  try {
    Object.assign(i, await api(`/api/inventory/${i.id}`, { method: "PUT", body: JSON.stringify(body) }));
    render();
  } catch (err) {
    box.checked = !box.checked;
    box.disabled = false;
    showToast(`Could not save: ${err.message}`, null, true);
  }
}

function row(i) {
  const tr = el("tr", i.sold ? "is-sold" : "is-stock");
  tr.dataset.id = i.id;
  if (i.note) tr.dataset.note = i.note;

  const model = el("td", "c-model");
  const name = el("span", null, i.model || "–");
  name.title = i.model || "";
  model.append(name);
  if (i.note) model.append(el("span", "has-note", " ✎"));

  const edit = el("button", "btn-quiet", "Edit");
  edit.type = "button";
  edit.setAttribute("aria-label", `Edit ${i.brand || ""} ${i.model || ""}`);
  edit.addEventListener("click", () => openEditor(i));
  const actions = el("td", "c-actions");
  actions.append(edit);

  tr.append(
    cell(i.category, "c-cat", i.category),
    cell(i.brand, "c-brand", i.brand),
    model,
    cell(i.bought_at ? day.format(new Date(i.bought_at)) : "", "c-date"),
    stackCell(COST_LINES, i),
    stackCell(SELL_LINES, i, "inv-plan"),
    forSaleCell(i),
    cell(i.sold_at ? day.format(new Date(i.sold_at)) : (i.sold ? "sold" : ""), "c-date"),
    cell(money(i.sold_price), "num c-sold"),
    profitCell(i.profit),
    profitCell(i.net_profit),
    actions,
  );
  tr.addEventListener("dblclick", (e) => { if (!e.target.closest("button, label")) openEditor(i); });
  return tr;
}

/* ---------- the whole note on hover over a row that has one ---------- */

const notePreview = document.getElementById("note-preview");

function placeNote(x, y) {
  const w = notePreview.offsetWidth, h = notePreview.offsetHeight, gap = 18;
  const left = x + gap + w > innerWidth ? x - gap - w : x + gap;
  const top = Math.min(Math.max(8, y - h / 2), innerHeight - h - 8);
  notePreview.style.transform = `translate(${Math.max(8, left)}px, ${Math.max(8, top)}px)`;
}

rowsBox.addEventListener("mousemove", (e) => {
  const note = e.target.closest(".c-model") && e.target.closest("tr")?.dataset.note;
  if (!note) { notePreview.hidden = true; return; }
  if (notePreview.textContent !== note) notePreview.textContent = note;
  notePreview.hidden = false;
  placeNote(e.clientX, e.clientY);
});
rowsBox.addEventListener("mouseleave", () => { notePreview.hidden = true; });
window.addEventListener("scroll", () => { notePreview.hidden = true; }, { passive: true });

/* ---------- editor ---------- */

const editor = document.getElementById("editor");
const form = editor.querySelector("form");
const errorBox = document.getElementById("editor-error");
let editing = null;   // the item being changed, or null when adding

function openEditor(item) {
  editing = item;
  document.getElementById("editor-title").textContent = item ? "Edit item" : "Add item";
  document.getElementById("delete").hidden = !item;
  for (const k of FIELDS) form.elements[k].value = item?.[k] ?? "";
  form.elements.for_sale.checked = Boolean(item?.for_sale);
  if (!item) form.elements.bought_at.value = today();
  errorBox.hidden = true;
  updateCalc();
  editor.showModal();
  form.elements[item ? "model" : "category"].focus();
}

function formBody() {
  return { ...Object.fromEntries(FIELDS.map((k) => [k, form.elements[k].value])), for_sale: form.elements.for_sale.checked };
}

function updateCalc() {
  const n = (k) => (form.elements[k].value === "" ? null : +form.elements[k].value);
  const cost = (n("bought_price") ?? 0) + (n("buy_costs") ?? 0) + (n("extra_costs") ?? 0);
  const sold = n("sold_price"), expected = n("sell_expected");
  const box = document.getElementById("editor-calc");
  box.replaceChildren();
  const line = (label, v) => {
    const p = el("span", `inv-calc-line ${signed(v)}`);
    p.append(el("span", null, label), el("b", null, money(v)));
    box.append(p);
  };
  if (cost) line("Total cost", cost);
  if (sold != null) {
    if (n("bought_price") != null) line("Profit", sold - n("bought_price"));
    line("Net profit", sold - cost);
  } else if (expected != null) {
    line("Expected net profit", expected - cost);
  }
}

form.addEventListener("input", updateCalc);

// A sold price with no date: assume it sold today. Same for ticking "for sale" with no date.
form.elements.sold_price.addEventListener("change", () => {
  if (form.elements.sold_price.value && !form.elements.sold_at.value) form.elements.sold_at.value = today();
});
form.elements.for_sale.addEventListener("change", () => {
  if (form.elements.for_sale.checked && !form.elements.listed_at.value) form.elements.listed_at.value = today();
});
// Typing a "for sale since" date ticks the box.
form.elements.listed_at.addEventListener("change", () => {
  if (form.elements.listed_at.value) form.elements.for_sale.checked = true;
});

form.addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;   // Cancel closes the dialog as usual
  e.preventDefault();
  const body = formBody();
  try {
    if (editing) {
      Object.assign(editing, await api(`/api/inventory/${editing.id}`, { method: "PUT", body: JSON.stringify(body) }));
    } else {
      state.items.unshift(await api("/api/inventory", { method: "POST", body: JSON.stringify(body) }));
    }
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
    return;
  }
  const added = !editing;
  editor.close();
  fillOptions();
  render();
  showToast(added ? "Item added." : "Saved.");
});

document.getElementById("add").addEventListener("click", () => openEditor(null));

document.getElementById("delete").addEventListener("click", async () => {
  const item = editing;
  if (!item || !confirm(`Delete ${item.brand || ""} ${item.model || ""} from the inventory?`)) return;
  editor.close();
  try {
    await api(`/api/inventory/${item.id}`, { method: "DELETE" });
  } catch (err) {
    showToast(`Could not delete: ${err.message}`, null, true);
    return;
  }
  state.items = state.items.filter((x) => x.id !== item.id);
  fillOptions();
  render();
  showToast("Item deleted.", async () => {
    const back = await api("/api/inventory", { method: "POST", body: JSON.stringify({ ...Object.fromEntries(FIELDS.map((k) => [k, item[k]])), for_sale: item.for_sale }) });
    state.items.push(back);
    fillOptions();
    render();
  });
});

load().catch((e) => showToast(`Could not load the inventory: ${e.message}`, null, true));
