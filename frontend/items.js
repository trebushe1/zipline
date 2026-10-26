const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ICON_SIZES = [100, 300, 500];
const STORE = "zipline.builds";
const OPEN_BUILD = "zipline.builds.open";

const SLOTS = {
  weapon: { name: "Оружие", short: "оруж." },
  vitality: { name: "Живучесть", short: "живуч." },
  spirit: { name: "Дух", short: "дух" },
};
const SLOT_ORDER = ["weapon", "vitality", "spirit"];
const TIER = ["", "I", "II", "III", "IV", "★"];

// The game's shop filters (EShopFilter…), grouped the way its filter panel groups them.
const FILTER_GROUPS = [
  ["Оружие", [["WeaponDamage", "Урон оружия"], ["WeaponAmmo", "Патроны"], ["WeaponFireRate", "Скорострельность"],
    ["WeaponBulletVelocity", "Скорость пуль"], ["WeaponRange", "Дальность"], ["Melee", "Ближний бой"],
    ["PhysicalAdditionalDamage", "Доп. физический урон"]]],
  ["Способности", [["SpiritDamage", "Сила духа"], ["SpiritCooldownAndCharges", "Перезарядка и заряды"],
    ["SpiritDuration", "Длительность"], ["SpiritRange", "Дальность"], ["SpiritAdditionalDamage", "Урон духом"],
    ["SpiritAdditionalDamagePct", "Урон % от здоровья"]]],
  ["Здоровье", [["HP", "Здоровье"], ["Regen", "Регенерация"], ["OutOfCombatRegen", "Регенерация вне боя"],
    ["Barrier", "Барьер"], ["Healing", "Лечение"], ["Lifesteal", "Вампиризм"]]],
  ["Защита", [["PhysicalResist", "Сопр. пулям"], ["SpiritResist", "Сопр. духу"], ["MeleeResist", "Сопр. ближнему бою"],
    ["DebuffResist", "Сопр. эффектам"], ["SlowResist", "Сопр. замедлению"], ["AntiCC", "Против контроля"],
    ["Invulnerability", "Неуязвимость"]]],
  ["Передвижение", [["MoveSpeed", "Скорость"], ["Sprint", "Спринт"], ["Stamina", "Выносливость"],
    ["JumpAndDash", "Прыжок, рывок, подкат"], ["Teleport", "Телепорт"], ["Stealth", "Невидимость"]]],
  ["Ослабление врага", [["BulletVuln", "Уязвимость к пулям"], ["SpiritVuln", "Уязвимость к духу"], ["AntiHeal", "Против лечения"],
    ["FireRateReduction", "Медленнее стреляет"], ["SpiritDamageReduction", "Меньше урон духом"], ["MobilityReduction", "Замедление"]]],
  ["Контроль", [["Status_Stun", "Оглушение"], ["Status_Disarm", "Обезоруживание"], ["Status_Silence", "Безмолвие"],
    ["Status_Curse", "Проклятие"], ["Status_Immobilize", "Обездвиживание"], ["Status_Grounded", "Приземление"]]],
  ["Прочее", [["Active", "Активные"], ["Imbue", "Усиливают способность"]]],
];
const FILTER_NAME = Object.fromEntries(FILTER_GROUPS.flatMap(([, list]) => list));

const state = {
  index: null, items: [], byKey: new Map(), upgrades: new Map(), heroes: [], heroByKey: new Map(),
  tab: "all", q: "", sort: "tier", lang: "ru", filters: new Set(), mode: "or", quick: new Set(), legendary: true,
  builds: [], editing: null, target: null, view: null, zipSize: 500,
};

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  return `${n} ${n10 === 1 && n100 !== 11 ? one : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? few : many}`;
}

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 2600);
}

const souls = (n) => n == null ? "—" : n.toLocaleString("ru-RU");
const SOUL = '<svg class="soul" viewBox="0 0 12 14" aria-hidden="true"><path d="M6 0c1.6 2.6 5 4.8 5 8.3A5 5 0 0 1 1 8.3C1 5.6 3 4.6 3.6 2.4c.9 1.3 1.5 2.3 1.4 3.6C6.4 4.6 6.6 2.4 6 0Z" fill="currentColor"/></svg>';
const norm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е");
// Text from the game in the chosen language, English where there is no Russian.
const t = (x) => x == null ? "" : typeof x === "string" ? x : (state.lang === "ru" && x.ru) || x.en || "";
const nameOf = (item) => t(item.name);
const slugOf = (item) => (item.name.en || item.key).toLowerCase().replace(/['’]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || item.key;
const iconUrl = (item) => item.icon ? `items/icons/${item.icon}` : null;
// The grid and the build show a small WebP; the full PNG is for the item card and downloads.
const thumbUrl = (item) => item.thumb ? `items/thumbs/${item.thumb}` : iconUrl(item);

// ── Shop: what to show ─────────────────────────────────
function prepare(index) {
  state.items = index.items;
  for (const it of state.items) {
    state.byKey.set(it.key, it);
    it._search = norm([it.name.en, it.name.ru, it.search?.en, it.search?.ru].filter(Boolean).join(" "));
    it._filters = new Set(it.filters || []);
  }
  // What each item turns into: the reverse of its components.
  for (const it of state.items) {
    for (const c of it.components || []) {
      if (!state.upgrades.has(c)) state.upgrades.set(c, []);
      state.upgrades.get(c).push(it.key);
    }
  }
}

function matches(it) {
  if (state.tab !== "all" && it.slot !== state.tab) return false;
  if (!state.legendary && it.tier >= 5) return false;
  if (state.q && !it._search.includes(norm(state.q).trim())) return false;
  if (state.quick.has("active") && !it.active) return false;
  if (state.quick.has("passive") && it.active) return false;
  if (state.quick.has("components") && !it.components) return false;
  if (state.filters.size) {
    const hits = [...state.filters].filter((f) => it._filters.has(f)).length;
    if (state.mode === "and" ? hits < state.filters.size : !hits) return false;
  }
  return true;
}

const byName = (a, b) => nameOf(a).localeCompare(nameOf(b), state.lang === "ru" ? "ru" : "en");
const SORTS = {
  tier: (a, b) => a.tier - b.tier || byName(a, b),
  "cost-asc": (a, b) => (a.cost ?? 0) - (b.cost ?? 0) || byName(a, b),
  "cost-desc": (a, b) => (b.cost ?? 0) - (a.cost ?? 0) || byName(a, b),
  name: byName,
  slot: (a, b) => SLOT_ORDER.indexOf(a.slot) - SLOT_ORDER.indexOf(b.slot) || a.tier - b.tier || byName(a, b),
};

function visibleItems() {
  return state.items.filter(matches).sort(SORTS[state.sort] || SORTS.tier);
}

// ── Shop: drawing ──────────────────────────────────────
function inBuild(key) {
  const b = currentBuild();
  return !!b && b.cats.some((c) => c.items.some((i) => i.k === key));
}

function cardHtml(it, { cost = false } = {}) {
  const url = thumbUrl(it);
  const initials = (it.name.en || it.key).split(/[\s-]+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return `
    <button class="item${inBuild(it.key) ? " in-build" : ""}" type="button" data-key="${it.key}" data-slot="${it.slot}"${state.editing ? ' draggable="true"' : ""}>
      <span class="item-art">${url ? `<img src="${url}" alt="" loading="lazy" decoding="async" draggable="false">` : `<i>${escapeHtml(initials)}</i>`}</span>
      <span class="item-name">${escapeHtml(nameOf(it))}</span>
      ${it.active ? `<span class="item-active" title="${it.active === "toggle" ? "Переключаемый" : "Активный"} предмет">${it.active === "toggle" ? "вкл" : "акт"}</span>` : ""}
      ${cost ? `<span class="item-cost">${SOUL}${souls(it.cost)}</span>` : ""}
      <span class="item-check" aria-hidden="true">✓</span>
    </button>`;
}

function tierRow(row, tier, attrs = "") {
  const cost = state.index.prices[tier - 1];
  return `
    <div class="tier-row${tier >= 5 ? " legendary" : ""}" data-tier="${tier}"${attrs}>
      <div class="tier-label" title="${tier >= 5 ? "Легендарные предметы: только в режиме «Уличная драка»" : `Тир ${TIER[tier]}`}">
        <b>${SOUL}${souls(cost)}</b><span>${tier >= 5 ? "легенд." : `тир ${TIER[tier]}`}</span>
      </div>
      <div class="tier-items">${row.map((it) => cardHtml(it)).join("")}</div>
    </div>`;
}

const tiersOf = (items) => [...new Set(items.map((i) => i.tier))].sort((a, b) => a - b);

function tierRows(items) {
  return tiersOf(items).map((tier) => tierRow(items.filter((i) => i.tier === tier), tier)).join("");
}

// All three sections side by side, a tier per row across them, like the game's "All items" page.
// On narrow screens the cells fall back into one column per section (--o orders them).
function tierColumns(items) {
  const heads = SLOT_ORDER.map((slot, s) =>
    `<h3 class="slot-head" data-slot="${slot}" style="--o:${s * 10}">${SLOTS[slot].name}</h3>`).join("");
  return heads + tiersOf(items).map((tier) => SLOT_ORDER.map((slot, s) => {
    const row = items.filter((i) => i.slot === slot && i.tier === tier);
    return row.length ? tierRow(row, tier, ` data-slot="${slot}" style="--o:${s * 10 + tier}"`)
      : `<div class="tier-row empty" style="--o:${s * 10 + tier}"></div>`;
  }).join("")).join("");
}

function renderBoard() {
  const items = visibleItems();
  const board = $("#board");
  hideTip();
  board.dataset.mode = state.sort === "tier" ? (state.tab === "all" ? "columns" : "rows") : "flat";
  if (!items.length) {
    board.innerHTML = `<p class="board-empty">Ничего не нашлось. Попробуйте другое название или сбросьте фильтры.</p>`;
  } else if (state.sort === "tier" && state.tab === "all") {
    board.innerHTML = tierColumns(items);
  } else if (state.sort === "tier") {
    board.innerHTML = `<section class="slot-col wide" data-slot="${state.tab}">${tierRows(items)}</section>`;
  } else if (state.sort === "slot") {
    board.innerHTML = SLOT_ORDER.filter((s) => items.some((i) => i.slot === s)).map((slot) => `
      <section class="slot-col wide" data-slot="${slot}">
        <h3 class="slot-head">${SLOTS[slot].name}</h3>
        <div class="flat-items">${items.filter((i) => i.slot === slot).map((it) => cardHtml(it, { cost: true })).join("")}</div>
      </section>`).join("");
  } else {
    board.innerHTML = `<div class="flat-items">${items.map((it) => cardHtml(it, { cost: true })).join("")}</div>`;
  }
  const total = state.items.filter((i) => state.legendary || i.tier < 5).length;
  $("#shop-count").textContent = items.length === total ? plural(total, "предмет", "предмета", "предметов") : `${items.length} из ${total}`;
  $("#zip-shown").disabled = !items.length;
}

function renderFilters() {
  $("#filter-groups").innerHTML = FILTER_GROUPS.map(([group, list]) => {
    const present = list.filter(([f]) => state.items.some((i) => i._filters.has(f)));
    if (!present.length) return "";
    return `
      <div class="filter-group">
        <span class="filter-group-name">${group}</span>
        <div class="topics">${present.map(([f, label]) => `<button type="button" data-filter="${f}" aria-pressed="${state.filters.has(f)}">${label}</button>`).join("")}</div>
      </div>`;
  }).join("");
  $("#filters-count").textContent = state.filters.size || "";
  $("#active-filters").innerHTML = [...state.filters].map((f) =>
    `<button class="chip" type="button" data-unfilter="${f}" title="Убрать фильтр">${escapeHtml(FILTER_NAME[f] || f)}</button>`).join("");
  for (const btn of $$("#filter-mode button")) btn.setAttribute("aria-pressed", btn.dataset.mode === state.mode);
}

// ── Tooltip ────────────────────────────────────────────
function statHtml(s) {
  const pre = escapeHtml(t(s.pre)), post = escapeHtml(t(s.post));
  return `
    <div class="st">
      <b class="st-v">${pre}${escapeHtml(s.v)}${post}</b>
      <span class="st-l">${escapeHtml(t(s.l))}${s.cond ? ' <i class="st-cond">условно</i>' : ""}</span>
      ${s.spirit ? `<i class="st-spirit" title="Растёт от силы духа: +${s.spirit} за каждую единицу">×${s.spirit}</i>` : ""}
    </div>`;
}

function boxHtml(s) {
  const pre = escapeHtml(t(s.pre)), post = escapeHtml(t(s.post));
  return `
    <div class="box">
      <b>${pre}${escapeHtml(s.v)}<small>${post}</small></b>
      <span>${escapeHtml(t(s.l))}</span>
      ${s.spirit ? `<i class="st-spirit" title="Растёт от силы духа">×${s.spirit}</i>` : ""}
      ${s.cond ? '<i class="st-cond">условно</i>' : ""}
    </div>`;
}

const SECTION_NAME = { passive: "Пассивно", active: "Активно" };

function sectionHtml(sec, item) {
  const body = sec.blocks.map((b) => `
    ${b.text ? `<p class="tip-text">${t(b.text)}</p>` : ""}
    ${b.big?.length || b.elevated?.length ? `<div class="boxes">${[...(b.elevated || []), ...(b.big || [])].map(boxHtml).join("")}</div>` : ""}
    ${b.stats?.length ? `<div class="stats-list">${b.stats.map(statHtml).join("")}</div>` : ""}`).join("");
  if (sec.type === "innate") return `<div class="tip-sec innate">${body}</div>`;
  const name = sec.type === "active" && item.active === "toggle" ? "Переключаемо" : SECTION_NAME[sec.type];
  return `
    <div class="tip-sec" data-type="${sec.type}">
      <header><span>${name}</span>${sec.cooldown ? `<span class="cd" title="Перезарядка">⟳ ${escapeHtml(sec.cooldown)} с</span>` : ""}</header>
      ${body}
    </div>`;
}

function miniHtml(key) {
  const it = state.byKey.get(key);
  if (!it) return "";
  return `<button class="mini" type="button" data-open="${it.key}" data-slot="${it.slot}">${it.icon ? `<img src="${thumbUrl(it)}" alt="">` : ""}<span>${escapeHtml(nameOf(it))}</span></button>`;
}

function tipHtml(it, { full = false } = {}) {
  const ups = state.upgrades.get(it.key) || [];
  const other = state.lang === "ru" && it.name.ru ? it.name.en : null;
  const note = full ? null : buildNote(it.key);
  return `
    <div class="tip" data-slot="${it.slot}">
      <header class="tip-head">
        <div class="tip-title"><strong>${escapeHtml(nameOf(it))}</strong>${other ? `<small>${escapeHtml(other)}</small>` : ""}</div>
        <div class="tip-cost">${SOUL}${souls(it.cost)}</div>
      </header>
      <div class="tip-sub">
        <span>${SLOTS[it.slot].name}</span><span>${it.tier >= 5 ? "легендарный · уличная драка" : `тир ${TIER[it.tier]}`}</span>
        ${it.active ? `<span class="tip-badge">${it.active === "toggle" ? "переключаемый" : "активный"}</span>` : ""}
      </div>
      ${note ? `<div class="tip-note"><b>Заметка в билде:</b> ${escapeHtml(note)}</div>` : ""}
      ${it.sections.map((s) => sectionHtml(s, it)).join("")}
      ${it.components?.length ? `<div class="tip-comp"><span>Собирается из</span><div>${it.components.map(miniHtml).join("")}</div></div>` : ""}
      ${full && ups.length ? `<div class="tip-comp"><span>Улучшается в</span><div>${ups.map(miniHtml).join("")}</div></div>` : ""}
    </div>`;
}

let tipFor = null;
function showTip(card) {
  const it = state.byKey.get(card.dataset.key);
  if (!it) return;
  const tip = $("#tip");
  if (tipFor !== it.key) {
    tip.innerHTML = tipHtml(it);
    tipFor = it.key;
  }
  tip.hidden = false;
  const r = card.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
  let x = r.right + 10;
  if (x + w > innerWidth - 8) x = r.left - w - 10;
  if (x < 8) x = Math.min(innerWidth - w - 8, Math.max(8, r.left));
  let y = Math.min(r.top, innerHeight - h - 8);
  if (y < 8) y = 8;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}

function hideTip() {
  $("#tip").hidden = true;
  tipFor = null;
}

// ── Item card (dialog) ─────────────────────────────────
function openItem(key, push = true) {
  const it = state.byKey.get(key);
  if (!it) return;
  hideTip();
  const dialog = $("#item-dialog");
  const url = iconUrl(it);
  dialog.innerHTML = `
    <div class="cmp item-view">
      <div class="cmp-head">
        <strong>${escapeHtml(nameOf(it))}</strong>
        <button class="link-btn" type="button" data-copy-link="${it.key}">ссылка на предмет</button>
        <button class="cmp-close" type="button" data-close aria-label="Закрыть">×</button>
      </div>
      <div class="item-view-body">
        <div class="item-view-art">
          <div class="art-frame" data-slot="${it.slot}">${url ? `<img src="${url}" alt="${escapeHtml(nameOf(it))}">` : `<span>Иконка появится после обновления данных</span>`}</div>
          ${url ? `
            <span class="ctl-label">Скачать иконку · PNG</span>
            <div class="dl-row">${ICON_SIZES.map((n) => `<button class="btn" type="button" data-dl="${it.key}" data-size="${n}">${n}×${n}</button>`).join("")}</div>
            <span class="art-native" id="art-native"></span>` : ""}
          ${state.editing ? `<button class="btn export-btn" type="button" data-add="${it.key}">+ В билд${currentCat() ? ` · ${escapeHtml(currentCat().name)}` : ""}</button>` : ""}
        </div>
        ${tipHtml(it, { full: true })}
      </div>
    </div>`;
  if (!dialog.open) dialog.showModal();
  if (url) loadImage(url).then((img) => {
    const el = $("#art-native");
    if (el) el.textContent = `В файлах игры: ${img.naturalWidth}×${img.naturalHeight}`;
  }).catch(() => {});
  if (push) history.replaceState(null, "", `#item=${it.key}`);
}

function closeItem() {
  const dialog = $("#item-dialog");
  if (dialog.open) dialog.close();
}

// ── Icons: PNG at 100, 300 and 500 ─────────────────────
const images = new Map();
function loadImage(src) {
  if (!images.has(src)) {
    // onload rather than decode(): decode() can stall in a background tab.
    const img = new Image();
    images.set(src, new Promise((resolve, reject) => {
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`${src}: not loaded`));
      img.src = src;
    }).catch((err) => { images.delete(src); throw err; }));
  }
  return images.get(src);
}

// Halve in steps on the way down (one big jump looks grainy), smooth on the way up.
async function iconBlob(item, size) {
  const img = await loadImage(iconUrl(item));
  let src = img, w = img.naturalWidth, h = img.naturalHeight;
  while (w / 2 >= size && h / 2 >= size) {
    const c = document.createElement("canvas");
    c.width = Math.round(w / 2);
    c.height = Math.round(h / 2);
    const ctx = c.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(src, 0, 0, c.width, c.height);
    src = c; w = c.width; h = c.height;
  }
  const out = document.createElement("canvas");
  out.width = out.height = size;
  const ctx = out.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, 0, 0, size, size);
  return new Promise((resolve) => out.toBlob(resolve, "image/png"));
}

function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

async function downloadIcon(key, size) {
  const it = state.byKey.get(key);
  if (!it?.icon) return;
  try {
    saveBlob(await iconBlob(it, size), `${slugOf(it)}-${size}x${size}.png`);
  } catch (err) {
    console.error(err);
    toast("Не удалось подготовить иконку");
  }
}

// A plain ZIP (stored, no compression: PNG is compressed already).
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function zipBlob(files) {
  const enc = new TextEncoder(), parts = [], central = [];
  const now = new Date();
  const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
  const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
  let offset = 0, centralSize = 0;
  for (const f of files) {
    const name = enc.encode(f.name), crc = crc32(f.data), size = f.data.length;
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // names in UTF-8
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, size, true);
    local.setUint32(22, size, true);
    local.setUint16(26, name.length, true);
    parts.push(local, name, f.data);
    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true);
    entry.setUint16(4, 20, true);
    entry.setUint16(6, 20, true);
    entry.setUint16(8, 0x0800, true);
    entry.setUint16(12, time, true);
    entry.setUint16(14, date, true);
    entry.setUint32(16, crc, true);
    entry.setUint32(20, size, true);
    entry.setUint32(24, size, true);
    entry.setUint16(28, name.length, true);
    entry.setUint32(42, offset, true);
    central.push(entry, name);
    offset += 30 + name.length + size;
    centralSize += 46 + name.length;
  }
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/zip" });
}

let zipping = false;
async function downloadZip(items, label) {
  if (zipping) return;
  const list = items.filter((i) => i.icon);
  if (!list.length) {
    toast("Иконок пока нет: они появятся после обновления данных");
    return;
  }
  zipping = true;
  const size = state.zipSize, progress = $("#zip-progress");
  progress.hidden = false;
  $("#zip-all").disabled = $("#zip-shown").disabled = true;
  try {
    const files = [];
    let done = 0;
    // A few at a time: decoding all of them at once stalls the page.
    const queue = [...list];
    const worker = async () => {
      while (queue.length) {
        const it = queue.shift();
        const blob = await iconBlob(it, size);
        files.push({ it, name: `${it.slot}/${slugOf(it)}.png`, data: new Uint8Array(await blob.arrayBuffer()) });
        progress.textContent = `Готовим ${++done} из ${list.length}…`;
      }
    };
    await Promise.all(Array.from({ length: 6 }, worker));
    files.sort((a, b) => a.name.localeCompare(b.name));
    saveBlob(zipBlob(files), `deadlock-items-${label}-${size}x${size}.zip`);
    toast(`Архив готов: ${plural(files.length, "иконка", "иконки", "иконок")} ${size}×${size}`);
  } catch (err) {
    console.error(err);
    toast("Не получилось собрать архив");
  } finally {
    zipping = false;
    progress.hidden = true;
    $("#zip-all").disabled = false;
    $("#zip-shown").disabled = !visibleItems().length;
  }
}

// ── Builds: storage ────────────────────────────────────
const uid = () => Math.random().toString(36).slice(2, 10);
const DEFAULT_CATS = ["Ранняя игра", "Середина игры", "Поздняя игра"];

function loadBuilds() {
  try {
    const list = JSON.parse(localStorage.getItem(STORE) || "[]");
    state.builds = Array.isArray(list) ? list.filter((b) => b && Array.isArray(b.cats)) : [];
  } catch {
    state.builds = [];
  }
}

function saveBuilds() {
  try {
    localStorage.setItem(STORE, JSON.stringify(state.builds));
    localStorage.setItem(OPEN_BUILD, state.editing || "");
  } catch {
    toast("Браузер не дал сохранить билд (закрытый режим?)");
  }
}

const currentBuild = () => state.builds.find((b) => b.id === state.editing) || null;
const currentCat = () => {
  const b = currentBuild();
  return b && (b.cats.find((c) => c.id === state.target) || b.cats[0]) || null;
};

function buildNote(key) {
  const b = currentBuild();
  for (const c of b?.cats || []) {
    const found = c.items.find((i) => i.k === key && i.note);
    if (found) return found.note;
  }
  return null;
}

function touch(b) {
  b.updated = Date.now();
  saveBuilds();
}

function newBuild(from = {}) {
  const b = {
    id: uid(), hero: from.hero || null, name: from.name || "Новый билд", desc: from.desc || "",
    cats: (from.cats || DEFAULT_CATS.map((name) => ({ name }))).map((c) => ({
      id: uid(), name: c.name || "Категория", desc: c.desc || "", items: (c.items || []).map((i) => ({ k: i.k, note: i.note || "" })),
    })),
    ab: Array.isArray(from.ab) ? from.ab.filter((n) => n >= 1 && n <= 4) : [],
    created: Date.now(), updated: Date.now(),
  };
  state.builds.unshift(b);
  return b;
}

function buildCost(b) {
  const keys = new Set(b.cats.flatMap((c) => c.items.map((i) => i.k)));
  let total = 0;
  const bySlot = { weapon: 0, vitality: 0, spirit: 0 };
  for (const k of keys) {
    const it = state.byKey.get(k);
    if (!it) continue;
    total += it.cost || 0;
    bySlot[it.slot] += it.cost || 0;
  }
  return { total, bySlot, count: keys.size };
}

// ── Builds: editor ─────────────────────────────────────
function openEditor(id) {
  state.editing = id;
  const b = currentBuild();
  state.target = b?.cats[0]?.id || null;
  saveBuilds();
  renderEditor();
  renderBoard();
  renderBuildCards();
  if (b && innerWidth < 1100) $("#build-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeEditor() {
  state.editing = null;
  state.target = null;
  saveBuilds();
  renderEditor();
  renderBoard();
  renderBuildCards();
}

function heroPickHtml(b) {
  const hero = state.heroByKey.get(b.hero);
  if (!state.heroes.length) return "";
  return `
    <div class="be-field">
      <span class="ctl-label">Герой</span>
      <button class="hero-pick" type="button" data-act="hero" aria-expanded="false">
        ${hero?.icon ? `<img src="heroes/icons/${hero.icon}" alt="">` : '<i aria-hidden="true">?</i>'}
        <span>${hero ? escapeHtml(hero.name) : "Выберите героя"}</span>
      </button>
      <div class="hero-grid" id="hero-grid" hidden>
        <button type="button" data-hero="" class="hero-none">Без героя</button>
        ${state.heroes.map((h) => `<button type="button" data-hero="${h.key}" title="${escapeHtml(h.name)}" aria-pressed="${h.key === b.hero}">${h.icon ? `<img src="heroes/icons/${h.icon}" alt="">` : ""}<span>${escapeHtml(h.name)}</span></button>`).join("")}
      </div>
    </div>`;
}

function beItemHtml(cat, entry, index) {
  const it = state.byKey.get(entry.k);
  if (!it) {
    return `<li class="be-item missing" data-cat="${cat.id}" data-index="${index}" draggable="true" title="Этого предмета больше нет в игре: ${escapeHtml(entry.k)}"><span>?</span></li>`;
  }
  return `
    <li class="be-item" data-cat="${cat.id}" data-index="${index}" data-key="${it.key}" data-slot="${it.slot}" draggable="true" tabindex="0"
        title="${escapeHtml(nameOf(it))}${entry.note ? ` — ${escapeHtml(entry.note)}` : ""}">
      ${it.icon ? `<img src="${thumbUrl(it)}" alt="" draggable="false">` : `<span>${escapeHtml(nameOf(it).slice(0, 2))}</span>`}
      ${entry.note ? '<i class="be-note-dot" aria-label="Есть заметка"></i>' : ""}
    </li>`;
}

function catCost(cat) {
  return cat.items.reduce((sum, i) => sum + (state.byKey.get(i.k)?.cost || 0), 0);
}

const AP_COST = [0, 1, 2, 5]; // an upgrade's price in ability points (the unlock is a separate currency)
const LEVEL_MARK = ["", "откр.", "I", "II", "III"];

function abilityHtml(b, readOnly = false) {
  const hero = state.heroByKey.get(b.hero);
  if (!hero?.abilities?.length) {
    if (readOnly) return "";
    return `<section class="be-ab"><span class="ctl-label">Порядок прокачки способностей</span><p class="be-hint">Выберите героя — появятся его способности.</p></section>`;
  }
  const abilities = [...hero.abilities].sort((x, y) => x.slot - y.slot);
  const levels = { 1: 0, 2: 0, 3: 0, 4: 0 };
  let points = 0;
  const steps = b.ab.map((slot) => {
    levels[slot] = Math.min(4, levels[slot] + 1);
    const level = levels[slot];
    if (level > 1) points += AP_COST[level - 1];
    return { slot, level };
  });
  const bySlot = Object.fromEntries(abilities.map((a) => [a.slot, a]));
  return `
    <section class="be-ab">
      <div class="be-ab-head">
        <span class="ctl-label">Порядок прокачки</span>
        <span class="be-ab-points">${plural(points, "очко", "очка", "очков")} способностей</span>
      </div>
      ${readOnly ? "" : `<div class="ab-pick">
        ${abilities.map((a) => `
          <button type="button" class="ab-btn" data-ab="${a.slot}" ${levels[a.slot] >= 4 ? "disabled" : ""} title="${escapeHtml(a.name)}: следующий шаг — ${levels[a.slot] ? `улучшение ${LEVEL_MARK[levels[a.slot] + 1]} (${AP_COST[levels[a.slot]]} очк.)` : "открыть"}">
            <img src="heroes/abilities/${a.icon}" alt="">
            <span class="ab-slot">${a.slot === 4 ? "ульт" : a.slot}</span>
            <span class="ab-pips">${[1, 2, 3, 4].map((n) => `<i class="${n <= levels[a.slot] ? "on" : ""}"></i>`).join("")}</span>
          </button>`).join("")}
      </div>`}
      ${steps.length ? `
        <ol class="ab-seq">
          ${steps.map((s, i) => `<li title="${escapeHtml(bySlot[s.slot]?.name || "")}: ${LEVEL_MARK[s.level]}"><b>${i + 1}</b><img src="heroes/abilities/${bySlot[s.slot]?.icon}" alt=""><i>${LEVEL_MARK[s.level]}</i></li>`).join("")}
        </ol>
        ${readOnly ? "" : `<div class="be-ab-actions"><button class="link-btn" type="button" data-act="ab-undo">Убрать последний шаг</button><button class="link-btn" type="button" data-act="ab-clear">Очистить</button></div>`}`
        : readOnly ? "" : `<p class="be-hint">Нажимайте на способности по порядку: первый клик открывает способность, следующие — улучшения за 1, 2 и 5 очков.</p>`}
    </section>`;
}

function renderEditor() {
  const b = currentBuild();
  const panel = $("#build-panel");
  $("#shop-layout").classList.toggle("editing", !!b);
  panel.hidden = !b;
  $("#build-new").textContent = b ? "+ Новый билд" : "+ Создать билд";
  $("#shop-note").textContent = b
    ? "Клик по предмету — в выбранную категорию билда. Можно перетащить в любую категорию. Правый клик или Shift+клик — карточка предмета."
    : "Наведите на предмет — подсказка, как в игре. Нажмите — карточка с иконкой для скачивания.";
  updateFab();
  if (!b) {
    $("#build-editor").innerHTML = "";
    return;
  }
  if (!b.cats.some((c) => c.id === state.target)) state.target = b.cats[0]?.id || null;
  const cost = buildCost(b);
  const bars = SLOT_ORDER.map((s) => cost.total ? `<i data-slot="${s}" style="flex:${cost.bySlot[s]}" title="${SLOTS[s].name}: ${souls(cost.bySlot[s])}"></i>` : "").join("");
  $("#build-editor").innerHTML = `
    ${heroPickHtml(b)}
    <label class="be-field">
      <span class="ctl-label">Название</span>
      <input class="ctl-input be-name" data-field="name" value="${escapeHtml(b.name)}" maxlength="80" placeholder="Название билда">
    </label>
    <label class="be-field">
      <span class="ctl-label">Описание</span>
      <textarea class="ctl-input be-desc" data-field="desc" rows="2" maxlength="2000" placeholder="Как играть, что важно — по желанию">${escapeHtml(b.desc)}</textarea>
    </label>
    <div class="be-sum">
      <div><b>${SOUL}${souls(cost.total)}</b><span>${plural(cost.count, "предмет", "предмета", "предметов")}</span></div>
      <div class="be-bars">${bars}</div>
    </div>
    <ol class="be-cats">
      ${b.cats.map((c, ci) => `
        <li class="be-cat${c.id === state.target ? " target" : ""}" data-cat="${c.id}">
          <div class="be-cat-head">
            <input class="be-cat-name" data-cat-field="name" value="${escapeHtml(c.name)}" maxlength="60" aria-label="Название категории">
            <span class="be-cat-cost">${SOUL}${souls(catCost(c))}</span>
            <span class="be-cat-tools">
              <button type="button" data-act="cat-up" title="Выше" ${ci === 0 ? "disabled" : ""}>↑</button>
              <button type="button" data-act="cat-down" title="Ниже" ${ci === b.cats.length - 1 ? "disabled" : ""}>↓</button>
              <button type="button" data-act="cat-desc" title="Описание категории" aria-pressed="${!!c.desc || !!c.showDesc}">✎</button>
              <button type="button" data-act="cat-del" title="Удалить категорию">×</button>
            </span>
          </div>
          ${c.desc || c.showDesc ? `<textarea class="ctl-input be-cat-desc" data-cat-field="desc" rows="2" maxlength="500" placeholder="Когда покупать, зачем">${escapeHtml(c.desc)}</textarea>` : ""}
          <ul class="be-items" data-cat="${c.id}">${c.items.map((e, i) => beItemHtml(c, e, i)).join("")}</ul>
          ${c.items.length ? "" : `<p class="be-hint">Пусто. Выберите категорию и нажимайте на предметы в магазине — или перетащите их сюда.</p>`}
        </li>`).join("")}
    </ol>
    <button class="btn be-add-cat" type="button" data-act="cat-add">+ Добавить категорию</button>
    ${abilityHtml(b)}
    <div class="be-foot">
      <button class="link-btn" type="button" data-act="duplicate">Сделать копию</button>
      <button class="link-btn danger" type="button" data-act="delete">Удалить билд</button>
    </div>`;
}

function refreshEditor() {
  renderEditor();
  renderBoard();
  renderBuildCards();
}

function addToBuild(key, catId = null, index = null) {
  const b = currentBuild();
  const it = state.byKey.get(key);
  if (!b || !it) return;
  if (!b.cats.length) b.cats.push({ id: uid(), name: "Категория 1", desc: "", items: [] });
  const cat = b.cats.find((c) => c.id === (catId || state.target)) || b.cats[0];
  if (cat.items.some((i) => i.k === key)) {
    toast(`«${nameOf(it)}» уже есть в «${cat.name}»`);
    return;
  }
  const entry = { k: key, note: "" };
  if (index == null || index > cat.items.length) cat.items.push(entry);
  else cat.items.splice(index, 0, entry);
  state.target = cat.id;
  touch(b);
  refreshEditor();
  toast(`+ ${nameOf(it)} → ${cat.name}`);
}

function moveEntry(fromCat, fromIndex, toCat, toIndex) {
  const b = currentBuild();
  const src = b?.cats.find((c) => c.id === fromCat), dst = b?.cats.find((c) => c.id === toCat);
  if (!src || !dst || !src.items[fromIndex]) return;
  const [entry] = src.items.splice(fromIndex, 1);
  if (src !== dst && dst.items.some((i) => i.k === entry.k)) {
    src.items.splice(fromIndex, 0, entry);
    toast("Этот предмет уже есть в той категории");
    return;
  }
  if (src === dst && toIndex > fromIndex) toIndex--;
  dst.items.splice(Math.max(0, Math.min(toIndex ?? dst.items.length, dst.items.length)), 0, entry);
  touch(b);
  refreshEditor();
}

function editorAction(act, catId) {
  const b = currentBuild();
  if (!b) return;
  const ci = b.cats.findIndex((c) => c.id === catId);
  const cat = b.cats[ci];
  switch (act) {
    case "hero": {
      const grid = $("#hero-grid"), btn = $("[data-act='hero']");
      grid.hidden = !grid.hidden;
      btn.setAttribute("aria-expanded", !grid.hidden);
      return;
    }
    case "cat-add":
      b.cats.push({ id: uid(), name: `Категория ${b.cats.length + 1}`, desc: "", items: [] });
      state.target = b.cats.at(-1).id;
      break;
    case "cat-up":
      if (ci > 0) [b.cats[ci - 1], b.cats[ci]] = [b.cats[ci], b.cats[ci - 1]];
      break;
    case "cat-down":
      if (ci < b.cats.length - 1) [b.cats[ci + 1], b.cats[ci]] = [b.cats[ci], b.cats[ci + 1]];
      break;
    case "cat-desc":
      cat.showDesc = !(cat.showDesc || cat.desc);
      if (!cat.showDesc) cat.desc = "";
      break;
    case "cat-del":
      if (cat.items.length && !confirm(`Удалить категорию «${cat.name}» вместе с ${plural(cat.items.length, "предметом", "предметами", "предметами")}?`)) return;
      b.cats.splice(ci, 1);
      break;
    case "ab-undo":
      b.ab.pop();
      break;
    case "ab-clear":
      b.ab = [];
      break;
    case "duplicate": {
      const copy = newBuild({ ...structuredClone(b), name: `${b.name} (копия)` });
      openEditor(copy.id);
      toast("Копия создана");
      return;
    }
    case "delete":
      if (!confirm(`Удалить билд «${b.name}»?`)) return;
      state.builds = state.builds.filter((x) => x.id !== b.id);
      saveBuilds();
      closeEditor();
      toast("Билд удалён");
      return;
    default:
      return;
  }
  touch(b);
  refreshEditor();
}

// The menu of an item inside the build: note, move, remove.
function openItemMenu(li) {
  const b = currentBuild();
  const cat = b?.cats.find((c) => c.id === li.dataset.cat);
  const index = Number(li.dataset.index);
  const entry = cat?.items[index];
  if (!entry) return;
  const it = state.byKey.get(entry.k);
  const menu = $("#item-menu");
  menu.innerHTML = `
    <strong>${escapeHtml(it ? nameOf(it) : entry.k)}</strong>
    ${it ? `<button type="button" data-menu="open">Подробнее о предмете</button>` : ""}
    <label class="menu-note"><span class="ctl-label">Заметка</span>
      <textarea class="ctl-input" rows="2" maxlength="300" placeholder="Например: против Хейз">${escapeHtml(entry.note)}</textarea>
    </label>
    <div class="menu-row">
      <button type="button" data-menu="left" ${index === 0 ? "disabled" : ""}>← Раньше</button>
      <button type="button" data-menu="right" ${index === cat.items.length - 1 ? "disabled" : ""}>Позже →</button>
    </div>
    ${b.cats.length > 1 ? `<span class="ctl-label">Перенести в</span>${b.cats.filter((c) => c !== cat).map((c) => `<button type="button" data-menu="move" data-to="${c.id}">${escapeHtml(c.name)}</button>`).join("")}` : ""}
    <button type="button" data-menu="remove" class="danger">Убрать из билда</button>`;
  menu.hidden = false;
  menu.dataset.cat = cat.id;
  menu.dataset.index = index;
  const r = li.getBoundingClientRect();
  const w = menu.offsetWidth, h = menu.offsetHeight;
  menu.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left))}px`;
  menu.style.top = `${r.bottom + 6 + h > innerHeight ? Math.max(8, r.top - h - 6) : r.bottom + 6}px`;
  $("textarea", menu).addEventListener("input", (e) => {
    entry.note = e.target.value.slice(0, 300);
    li.querySelector(".be-note-dot")?.remove();
    if (entry.note) li.insertAdjacentHTML("beforeend", '<i class="be-note-dot"></i>');
    clearTimeout(openItemMenu.timer);
    openItemMenu.timer = setTimeout(() => touch(b), 400);
  });
}

function closeItemMenu() {
  const menu = $("#item-menu");
  if (menu.hidden) return;
  menu.hidden = true;
  const b = currentBuild();
  if (b) touch(b);
}

function itemMenuAction(btn) {
  const menu = $("#item-menu");
  const b = currentBuild();
  const cat = b?.cats.find((c) => c.id === menu.dataset.cat);
  const index = Number(menu.dataset.index);
  if (!cat) return;
  const entry = cat.items[index];
  menu.hidden = true;
  switch (btn.dataset.menu) {
    case "open":
      openItem(entry.k);
      return;
    case "left":
      moveEntry(cat.id, index, cat.id, index - 1);
      return;
    case "right":
      moveEntry(cat.id, index, cat.id, index + 2);
      return;
    case "move":
      moveEntry(cat.id, index, btn.dataset.to, null);
      return;
    case "remove":
      cat.items.splice(index, 1);
      touch(b);
      refreshEditor();
  }
}

// ── Builds: list ───────────────────────────────────────
function renderBuildCards() {
  const list = $("#build-cards");
  if (!state.builds.length) {
    list.innerHTML = `<li class="build-empty">Билдов пока нет. Нажмите «Создать билд» — магазин станет редактором: кликайте по предметам, и они сложатся по категориям.</li>`;
    return;
  }
  list.innerHTML = state.builds.map((b) => {
    const hero = state.heroByKey.get(b.hero);
    const cost = buildCost(b);
    const keys = [...new Set(b.cats.flatMap((c) => c.items.map((i) => i.k)))];
    return `
      <li class="bcard${b.id === state.editing ? " open" : ""}" data-build="${b.id}">
        <span class="bcard-hero">${hero?.icon ? `<img src="heroes/icons/${hero.icon}" alt="">` : "<i>?</i>"}</span>
        <div class="bcard-main">
          <strong>${escapeHtml(b.name)}</strong>
          <span class="bcard-meta">${hero ? `${escapeHtml(hero.name)} · ` : ""}${plural(cost.count, "предмет", "предмета", "предметов")} · ${souls(cost.total)} душ</span>
          <span class="bcard-strip">${keys.slice(0, 12).map((k) => {
            const it = state.byKey.get(k);
            return it?.icon ? `<img src="${thumbUrl(it)}" alt="" title="${escapeHtml(nameOf(it))}" data-slot="${it.slot}">` : "";
          }).join("")}${keys.length > 12 ? `<em>+${keys.length - 12}</em>` : ""}</span>
        </div>
        <div class="bcard-actions">
          <button class="btn" type="button" data-build-act="edit">${b.id === state.editing ? "Открыт" : "Открыть"}</button>
          <span class="bcard-links"><button class="link-btn" type="button" data-build-act="view">На всю страницу</button><button class="link-btn" type="button" data-build-act="share">Ссылка</button></span>
        </div>
      </li>`;
  }).join("");
}

// ── Builds: links and files ────────────────────────────
const short = (key) => key.replace(/^upgrade_/, "");
const long = (key) => state.byKey.has(`upgrade_${key}`) ? `upgrade_${key}` : key;

function toBase64Url(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text) {
  const s = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function packBuild(b) {
  const data = {
    v: 1, h: b.hero || undefined, n: b.name, d: b.desc || undefined, a: b.ab.join("") || undefined,
    c: b.cats.map((c) => [c.name, c.desc || "", c.items.map((i) => i.note ? [short(i.k), i.note] : short(i.k))]),
  };
  const bytes = new TextEncoder().encode(JSON.stringify(data));
  if (typeof CompressionStream === "function") {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw"));
    return `z${toBase64Url(new Uint8Array(await new Response(stream).arrayBuffer()))}`;
  }
  return `j${toBase64Url(bytes)}`;
}

async function unpackBuild(code) {
  let bytes = fromBase64Url(code.slice(1));
  if (code[0] === "z") {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    bytes = new Uint8Array(await new Response(stream).arrayBuffer());
  }
  const data = JSON.parse(new TextDecoder().decode(bytes));
  return {
    hero: typeof data.h === "string" ? data.h : null,
    name: String(data.n || "Билд по ссылке").slice(0, 80),
    desc: String(data.d || "").slice(0, 2000),
    ab: String(data.a || "").split("").map(Number),
    cats: (Array.isArray(data.c) ? data.c : []).slice(0, 30).map(([name, desc, items]) => ({
      name: String(name || "Категория").slice(0, 60), desc: String(desc || "").slice(0, 500),
      items: (Array.isArray(items) ? items : []).slice(0, 60).map((i) => Array.isArray(i)
        ? { k: long(String(i[0])), note: String(i[1] || "").slice(0, 300) } : { k: long(String(i)) }),
    })),
  };
}

async function shareBuild(b) {
  try {
    const url = `${location.origin}${location.pathname}#b=${await packBuild(b)}`;
    await navigator.clipboard.writeText(url);
    toast("Ссылка на билд скопирована");
  } catch (err) {
    console.error(err);
    toast("Не удалось скопировать ссылку");
  }
}

function exportBuild(b) {
  const data = {
    format: "zipline-build", version: 1, game_build: state.index?.build,
    hero: b.hero, name: b.name, description: b.desc,
    categories: b.cats.map((c) => ({
      name: c.name, description: c.desc,
      items: c.items.map((i) => ({ key: i.k, name: state.byKey.get(i.k)?.name.en, note: i.note || undefined })),
    })),
    ability_order: b.ab,
  };
  const slug = b.name.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-").replace(/^-|-$/g, "") || "build";
  saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), `${slug}.json`);
}

function importBuild(data) {
  const list = Array.isArray(data) ? data : [data];
  let added = null;
  for (const d of list) {
    if (!d || !Array.isArray(d.categories)) continue;
    added = newBuild({
      hero: typeof d.hero === "string" ? d.hero : null, name: String(d.name || "Импорт").slice(0, 80), desc: String(d.description || "").slice(0, 2000),
      ab: Array.isArray(d.ability_order) ? d.ability_order.map(Number) : [],
      cats: d.categories.slice(0, 30).map((c) => ({
        name: String(c.name || "Категория").slice(0, 60), desc: String(c.description || "").slice(0, 500),
        items: (Array.isArray(c.items) ? c.items : []).slice(0, 60).map((i) => ({ k: String(i.key || i), note: String(i.note || "").slice(0, 300) })),
      })),
    });
  }
  if (!added) throw new Error("no builds in the file");
  saveBuilds();
  openEditor(added.id);
}

async function importFromHash() {
  const m = location.hash.match(/^#b=([zj][\w-]+)$/);
  if (!m) return false;
  try {
    openView(await unpackBuild(m[1]), true);
  } catch (err) {
    console.error(err);
    toast("Ссылка на билд повреждена");
  }
  return true;
}

// ── Builds: the whole page for one build ───────────────
// A shared link opens here, read-only; "Save" copies it into the visitor's own builds.
function viewItemHtml(entry) {
  const it = state.byKey.get(entry.k);
  if (!it) return `<li class="vi-wrap"><span class="vi-missing" title="${escapeHtml(entry.k)}">Предмета больше нет в игре</span></li>`;
  const url = thumbUrl(it);
  return `
    <li class="vi-wrap">
      <button class="item vi" type="button" data-key="${it.key}" data-slot="${it.slot}">
        <span class="item-art">${url ? `<img src="${url}" alt="" loading="lazy">` : ""}</span>
        <span class="item-name">${escapeHtml(nameOf(it))}</span>
        ${it.active ? `<span class="item-active">${it.active === "toggle" ? "вкл" : "акт"}</span>` : ""}
        <span class="item-cost">${SOUL}${souls(it.cost)}</span>
      </button>
      ${entry.note ? `<p class="vi-note">${escapeHtml(entry.note)}</p>` : ""}
    </li>`;
}

function viewHtml(b, shared) {
  const hero = state.heroByKey.get(b.hero);
  const cost = buildCost(b);
  return `
    <header class="bv-head">
      ${hero?.icon ? `<figure class="bv-hero"><img src="heroes/icons/${hero.icon}" alt="${escapeHtml(hero.name)}"></figure>` : ""}
      <div class="bv-title">
        <p class="eyebrow">${shared ? "Билд по ссылке" : "Мой билд"}${hero ? ` · ${escapeHtml(hero.name)}` : ""}</p>
        <h1><span class="tag tag-ink">${escapeHtml(b.name)}</span></h1>
        ${b.desc ? `<p class="lede bv-desc">${escapeHtml(b.desc)}</p>` : ""}
        <div class="bv-sum">
          <div class="bv-total"><b>${SOUL}${souls(cost.total)}</b><span>${plural(cost.count, "предмет", "предмета", "предметов")}</span></div>
          ${SLOT_ORDER.map((s) => cost.bySlot[s] ? `<div class="bv-slot" data-slot="${s}"><i></i><span>${SLOTS[s].name}</span><b>${souls(cost.bySlot[s])}</b></div>` : "").join("")}
        </div>
        <div class="bv-actions">
          ${shared ? `<button class="btn export-btn" type="button" data-view="save">Сохранить себе и редактировать</button>`
            : `<button class="btn export-btn" type="button" data-view="edit">Редактировать</button>`}
          <button class="btn" type="button" data-view="share">Скопировать ссылку</button>
          <span class="bv-shot">
            <span class="ctl-label">Картинка · PNG</span>
            <button class="btn" type="button" data-shot="1080x1080">1080×1080</button>
            <button class="btn" type="button" data-shot="1920x1080">1920×1080</button>
          </span>
          <button class="btn" type="button" data-view="close">К магазину</button>
        </div>
      </div>
    </header>
    <div class="bv-cats">
      ${b.cats.filter((c) => c.items.length || c.desc).map((c) => `
        <section class="panel bv-cat">
          <header class="bv-cat-head"><h2>${escapeHtml(c.name)}</h2><span class="bv-cat-cost">${SOUL}${souls(catCost(c))}</span></header>
          ${c.desc ? `<p class="bv-cat-desc">${escapeHtml(c.desc)}</p>` : ""}
          <ul class="bv-items">${c.items.map(viewItemHtml).join("")}</ul>
        </section>`).join("") || `<p class="empty">В этом билде пока нет предметов.</p>`}
    </div>
    ${b.ab?.length && hero ? `<section class="panel bv-ab">${abilityHtml(b, true)}</section>` : ""}`;
}

function openView(b, shared) {
  closeEditor();
  state.view = { build: b, shared };
  const view = $("#build-view");
  view.innerHTML = viewHtml(b, shared);
  view.hidden = false;
  document.body.classList.add("viewing");
  document.title = `${b.name} — билд Deadlock · zipline`;
  scrollTo(0, 0);
}

function closeView() {
  if (!state.view) return;
  state.view = null;
  $("#build-view").hidden = true;
  $("#build-view").innerHTML = "";
  document.body.classList.remove("viewing");
  document.title = "zipline — магазин и билды Deadlock";
  if (location.hash.startsWith("#b=")) history.replaceState(null, "", location.pathname);
}

function viewAction(act) {
  const { build: b, shared } = state.view || {};
  if (!b) return;
  if (act === "share") return shareBuild(b);
  if (act === "close") return closeView();
  if (act.startsWith("shot:")) return downloadShot(b, ...act.slice(5).split("x").map(Number));
  closeView();
  if (act === "save") {
    const copy = newBuild(b);
    saveBuilds();
    openEditor(copy.id);
    toast("Билд сохранён в «Мои билды»");
  } else if (act === "edit" && !shared) {
    openEditor(b.id);
  }
  $("#shop-layout").scrollIntoView({ block: "start" });
}

// ── Builds: a picture of the whole build ───────────────
// Drawn on a canvas of the exact size, so it holds only the build: no buttons, no page around it.
// The tile size is the largest at which every category (and the ability order) fits.
const SLOT_COLOR = {
  weapon: ["#d38a39", "#5c3a14"], vitality: ["#7cae46", "#33501b"], spirit: ["#a77ddc", "#45306a"],
};
const MONO = '"JetBrains Mono", ui-monospace, monospace';
const SANS = '"IBM Plex Sans", system-ui, sans-serif';

function fitLines(ctx, text, width, max) {
  const words = String(text).split(/\s+/).filter(Boolean), lines = [];
  let line = "";
  for (let i = 0; i < words.length; i++) {
    const next = line ? `${line} ${words[i]}` : words[i];
    if (ctx.measureText(next).width <= width || !line) {
      line = next;
      continue;
    }
    lines.push(line);
    line = words[i];
    if (lines.length === max) break;
  }
  if (lines.length < max && line) lines.push(line);
  const cut = lines.length === max && lines.join(" ").length < words.join(" ").length;
  return lines.map((l, i) => {
    if (!(cut && i === max - 1) && ctx.measureText(l).width <= width) return l;
    while (l.length > 1 && ctx.measureText(`${l}…`).width > width) l = l.slice(0, -1);
    return `${l.trimEnd()}…`;
  });
}

const tryImage = (src) => src ? Promise.race([loadImage(src).catch(() => null), new Promise((r) => setTimeout(() => r(null), 10_000))]) : Promise.resolve(null);

function shotLayout(cats, W, top, bottom, pad, gap) {
  // Categories go into columns (each into the shortest one); try 1–4 columns and every tile size.
  const contentW = W - pad * 2, room = bottom - top;
  let best = null;
  for (let cols = 1; cols <= Math.min(4, cats.length); cols++) {
    const colW = (contentW - gap * (cols - 1)) / cols;
    for (let size = 230; size >= 34; size -= 2) {
      const tg = Math.max(6, size * 0.08), head = Math.max(26, size * 0.24);
      const perRow = Math.max(1, Math.floor((colW + tg) / (size + tg)));
      const heights = Array(cols).fill(0), place = [];
      for (const c of cats) {
        const notes = c.items.some((i) => i.note);
        const tileH = size * (1.42 + (notes ? 0.3 : 0));
        const rows = Math.max(1, Math.ceil(c.items.length / perRow));
        const h = head + (c.desc ? size * 0.2 : 0) + rows * tileH + (rows - 1) * tg;
        const col = heights.indexOf(Math.min(...heights));
        place.push({ c, col, y: heights[col], h, tileH, perRow, notes });
        heights[col] += h + gap;
      }
      if (Math.max(...heights) - gap <= room) {
        if (!best || size > best.size) best = { cols, colW, size, tg, head, place };
        break;
      }
    }
  }
  return best;
}

async function drawBuildShot(b, W, H) {
  await Promise.all([`800 20px ${MONO}`, `600 20px ${SANS}`, `400 20px ${SANS}`].map((f) => document.fonts.load(f).catch(() => null)));
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  const wide = W > H, pad = Math.round(W * (wide ? 0.03 : 0.04));
  const hero = state.heroByKey.get(b.hero);
  const cats = b.cats.filter((c) => c.items.length);
  const cost = buildCost(b);

  // Paper-dark background like the shop board.
  const bg = ctx.createRadialGradient(W / 2, 0, 0, W / 2, 0, Math.max(W, H));
  bg.addColorStop(0, "#2a241c");
  bg.addColorStop(0.7, "#15120e");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // Header: portrait, eyebrow, the name on a white label, totals.
  const headH = Math.round(H * (wide ? 0.2 : 0.2));
  let x = pad;
  const heroImg = hero?.icon ? await tryImage(`heroes/icons/${hero.icon}`) : null;
  if (heroImg) {
    const h = headH - pad * 0.3, w = h * 0.74;
    ctx.fillStyle = "#007d59";
    ctx.fillRect(x, pad, w, h);
    const scale = Math.max((w - 8) / heroImg.naturalWidth, (h - 8) / heroImg.naturalHeight);
    const sw = (w - 8) / scale, sh = (h - 8) / scale;
    ctx.drawImage(heroImg, (heroImg.naturalWidth - sw) / 2, 0, sw, sh, x + 4, pad + 4, w - 8, h - 8);
    x += w + pad * 0.7;
  }
  const textW = W - x - pad;
  const eyebrow = Math.round(H * 0.017);
  ctx.textBaseline = "top";
  ctx.font = `700 ${eyebrow}px ${MONO}`;
  ctx.fillStyle = "#3cc493";
  ctx.fillText(`БИЛД DEADLOCK${hero ? ` · ${hero.name.toUpperCase()}` : ""}`, x, pad);
  let titleSize = Math.round(H * (wide ? 0.05 : 0.045));
  ctx.font = `800 ${titleSize}px ${MONO}`;
  while (ctx.measureText(b.name.toUpperCase()).width > textW - 20 && titleSize > 18) ctx.font = `800 ${--titleSize}px ${MONO}`;
  const title = fitLines(ctx, b.name.toUpperCase(), textW - 20, 1)[0];
  const ty = pad + eyebrow * 1.9, tw = ctx.measureText(title).width;
  ctx.fillStyle = "#f6f6f4";
  ctx.fillRect(x, ty, tw + titleSize * 0.6, titleSize * 1.3);
  ctx.fillStyle = "#111";
  ctx.fillText(title, x + titleSize * 0.3, ty + titleSize * 0.18);
  let y = ty + titleSize * 1.3 + H * 0.016;
  if (b.desc) {
    const ds = Math.round(H * 0.018);
    ctx.font = `400 ${ds}px ${SANS}`;
    ctx.fillStyle = "#c4c7c0";
    for (const line of fitLines(ctx, b.desc.replace(/\s+/g, " "), textW, wide ? 1 : 2)) {
      ctx.fillText(line, x, y);
      y += ds * 1.35;
    }
    y += H * 0.006;
  }
  // Totals: souls, then each section; the sections move to a second row when one does not hold them.
  const ts = Math.round(H * 0.022), chipH = ts * 1.7, chipGap = ts * 0.5;
  ctx.font = `800 ${ts}px ${MONO}`;
  const total = `${souls(cost.total)} душ · ${plural(cost.count, "предмет", "предмета", "предметов")}`;
  const totalW = ctx.measureText(total).width + ts * 1.2;
  ctx.font = `700 ${Math.round(ts * 0.75)}px ${MONO}`;
  const chips = SLOT_ORDER.filter((sl) => cost.bySlot[sl]).map((sl) => {
    const label = `${SLOTS[sl].name.toUpperCase()} ${souls(cost.bySlot[sl])}`;
    return { sl, label, w: ctx.measureText(label).width + ts * 1.6 };
  });
  const chipsW = chips.reduce((sum, c) => sum + c.w + chipGap, 0);
  const oneRow = totalW + chipGap + chipsW <= textW;
  y = Math.max(y, pad + headH - (oneRow ? chipH : chipH * 2 + chipGap));
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#0a4a36";
  ctx.fillRect(x, y, totalW, chipH);
  ctx.font = `800 ${ts}px ${MONO}`;
  ctx.fillStyle = "#fff";
  ctx.fillText(total, x + ts * 0.6, y + chipH / 2);
  let cx = oneRow ? x + totalW + chipGap : x;
  const cy = oneRow ? y : y + chipH + chipGap;
  ctx.font = `700 ${Math.round(ts * 0.75)}px ${MONO}`;
  for (const c of chips) {
    if (cx + c.w > W - pad) break;
    ctx.fillStyle = SLOT_COLOR[c.sl][1];
    ctx.fillRect(cx, cy, c.w, chipH);
    ctx.fillStyle = SLOT_COLOR[c.sl][0];
    ctx.fillRect(cx, cy, ts * 0.35, chipH);
    ctx.fillStyle = "#fff";
    ctx.fillText(c.label, cx + ts * 0.8, cy + chipH / 2);
    cx += c.w + chipGap;
  }
  const headBottom = Math.max(pad + headH, cy + chipH);

  // Ability order along the bottom.
  const abilities = hero?.abilities?.length && b.ab.length ? [...hero.abilities].sort((p, q) => p.slot - q.slot) : null;
  const abH = abilities ? Math.round(H * (wide ? 0.11 : 0.1)) : 0;
  const footH = Math.round(H * 0.035);
  const top = headBottom + pad * 0.4, bottom = H - pad - footH - (abH ? abH + pad * 0.4 : 0);

  const layout = shotLayout(cats, W, top, bottom, pad, Math.round(pad * 0.6));
  if (layout) {
    const icons = new Map(await Promise.all(cats.flatMap((c) => c.items).map(async (e) => {
      const it = state.byKey.get(e.k);
      return [e.k, it ? await tryImage(iconUrl(it)) : null];
    })));
    const { size, tg, head, colW } = layout;
    for (const p of layout.place) {
      const px = pad + p.col * (colW + Math.round(pad * 0.6)), py = top + p.y;
      // Category label and its cost.
      const hs = Math.round(head * 0.5);
      ctx.font = `800 ${hs}px ${MONO}`;
      ctx.textBaseline = "middle";
      const name = fitLines(ctx, p.c.name.toUpperCase(), colW * 0.7, 1)[0];
      ctx.fillStyle = "#ecece8";
      ctx.fillRect(px, py, ctx.measureText(name).width + hs, head * 0.8);
      ctx.fillStyle = "#111";
      ctx.fillText(name, px + hs / 2, py + head * 0.4);
      ctx.fillStyle = "#e6fbf7";
      ctx.textAlign = "right";
      ctx.fillText(`${souls(catCost(p.c))}`, px + colW, py + head * 0.4);
      ctx.textAlign = "left";
      let iy = py + head;
      if (p.c.desc) {
        ctx.font = `400 ${Math.round(size * 0.12)}px ${SANS}`;
        ctx.fillStyle = "#a9ada6";
        ctx.fillText(fitLines(ctx, p.c.desc.replace(/\s+/g, " "), colW, 1)[0], px, iy + size * 0.06);
        iy += size * 0.2;
      }
      p.c.items.forEach((e, i) => {
        const tx = px + (i % p.perRow) * (size + tg), ty2 = iy + Math.floor(i / p.perRow) * (p.tileH + tg);
        const it = state.byKey.get(e.k);
        const [light, deep] = SLOT_COLOR[it?.slot] || ["#666", "#333"];
        const nameH = size * 0.42;
        ctx.fillStyle = deep;
        ctx.fillRect(tx, ty2, size, size + nameH);
        const img = icons.get(e.k);
        if (img) ctx.drawImage(img, tx + 2, ty2 + 2, size - 4, size - 4);
        ctx.strokeStyle = light;
        ctx.lineWidth = Math.max(2, size * 0.02);
        ctx.strokeRect(tx + ctx.lineWidth / 2, ty2 + ctx.lineWidth / 2, size - ctx.lineWidth, size + nameH - ctx.lineWidth);
        if (it?.active) {
          const as = Math.round(size * 0.1);
          ctx.font = `800 ${as}px ${MONO}`;
          ctx.fillStyle = "#f3efe6";
          ctx.fillRect(tx + 4, ty2 + 4, ctx.measureText("АКТ").width + as * 0.6, as * 1.4);
          ctx.fillStyle = "#15120e";
          ctx.fillText(it.active === "toggle" ? "ВКЛ" : "АКТ", tx + 4 + as * 0.3, ty2 + 4 + as * 0.72);
        }
        const ns = Math.max(10, Math.round(size * 0.115));
        ctx.font = `600 ${ns}px ${SANS}`;
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        const lines = fitLines(ctx, it ? nameOf(it) : e.k, size - 8, 2);
        lines.forEach((l, li) => ctx.fillText(l, tx + size / 2, ty2 + size + nameH * (lines.length === 1 ? 0.5 : 0.3 + li * 0.38)));
        if (e.note) {
          ctx.font = `400 ${Math.max(10, Math.round(size * 0.1))}px ${SANS}`;
          ctx.fillStyle = "#f3c74c";
          fitLines(ctx, e.note, size, 2).forEach((l, li) => ctx.fillText(l, tx + size / 2, ty2 + size + nameH + size * (0.09 + li * 0.12)));
        }
        ctx.textAlign = "left";
      });
    }
  } else if (!cats.length) {
    ctx.font = `600 ${Math.round(H * 0.03)}px ${SANS}`;
    ctx.fillStyle = "#8a8f88";
    ctx.fillText("В билде пока нет предметов", pad, top + H * 0.05);
  }

  if (abilities) {
    const glyphs = new Map(await Promise.all(abilities.map(async (a) => [a.slot, await tryImage(`heroes/abilities/${a.icon}`)])));
    const ay = H - pad - footH - abH, ls = Math.round(abH * 0.16);
    ctx.font = `700 ${ls}px ${MONO}`;
    ctx.fillStyle = "#8a8f88";
    ctx.textBaseline = "top";
    ctx.fillText("ПОРЯДОК ПРОКАЧКИ", pad, ay);
    const boxH = abH - ls * 1.6, n = b.ab.length;
    const boxW = Math.min(boxH * 0.85, (W - pad * 2 - (n - 1) * 4) / n);
    const levels = { 1: 0, 2: 0, 3: 0, 4: 0 };
    b.ab.forEach((slot, i) => {
      levels[slot] = Math.min(4, levels[slot] + 1);
      const bx = pad + i * (boxW + 4), by = ay + ls * 1.6;
      ctx.fillStyle = "#221f1b";
      ctx.fillRect(bx, by, boxW, boxH);
      const g = glyphs.get(slot), gs = Math.min(boxW, boxH) * 0.5;
      if (g) ctx.drawImage(g, bx + (boxW - gs) / 2, by + boxH * 0.22, gs, gs);
      ctx.textAlign = "center";
      ctx.font = `700 ${Math.round(boxH * 0.14)}px ${MONO}`;
      ctx.fillStyle = "rgba(255,255,255,.55)";
      ctx.fillText(String(i + 1), bx + boxW / 2, by + boxH * 0.04);
      ctx.fillStyle = "#7ee0c3";
      ctx.fillText(LEVEL_MARK[levels[slot]].toUpperCase(), bx + boxW / 2, by + boxH * 0.78);
      ctx.textAlign = "left";
    });
  }

  // Signature.
  ctx.textBaseline = "bottom";
  ctx.font = `700 ${Math.round(footH * 0.5)}px ${MONO}`;
  ctx.fillStyle = "rgba(255,255,255,.45)";
  const site = `${location.host}${location.pathname.replace(/\/[^/]*$/, "")}`;
  ctx.fillText(`ZIPLINE · ${site} · билд игры ${state.index?.build ?? ""}`, pad, H - pad * 0.6);
  return canvas;
}

async function downloadShot(b, W, H) {
  try {
    toast(`Рисуем ${W}×${H}…`);
    const canvas = await drawBuildShot(b, W, H);
    const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    const slug = b.name.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-").replace(/^-|-$/g, "") || "build";
    saveBlob(blob, `${slug}-${W}x${H}.png`);
    toast(`Картинка ${W}×${H} готова`);
  } catch (err) {
    console.error(err);
    toast("Не получилось нарисовать картинку");
  }
}

// ── Drag and drop ──────────────────────────────────────
let dragging = null; // {key} from the shop or {cat, index} from the build

function dropIndex(list, x, y) {
  const items = $$(".be-item", list);
  for (let i = 0; i < items.length; i++) {
    const r = items[i].getBoundingClientRect();
    if (y < r.top || (y <= r.bottom && x < r.left + r.width / 2)) return i;
  }
  return items.length;
}

function wireDrag() {
  document.addEventListener("dragstart", (e) => {
    const card = e.target.closest?.(".item[draggable='true']");
    const be = e.target.closest?.(".be-item");
    if (card) dragging = { key: card.dataset.key };
    else if (be) dragging = { cat: be.dataset.cat, index: Number(be.dataset.index) };
    else return;
    hideTip();
    e.dataTransfer.effectAllowed = card ? "copy" : "move";
    e.dataTransfer.setData("text/plain", card ? card.dataset.key : "");
  });
  document.addEventListener("dragend", () => {
    dragging = null;
    $$(".drop-on").forEach((el) => el.classList.remove("drop-on"));
  });
  const editor = $("#build-editor");
  editor.addEventListener("dragover", (e) => {
    const cat = e.target.closest(".be-cat");
    if (!dragging || !cat) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = dragging.key ? "copy" : "move";
    $$(".drop-on").forEach((el) => el !== cat && el.classList.remove("drop-on"));
    cat.classList.add("drop-on");
  });
  editor.addEventListener("drop", (e) => {
    const cat = e.target.closest(".be-cat");
    if (!dragging || !cat) return;
    e.preventDefault();
    const index = dropIndex($(".be-items", cat), e.clientX, e.clientY);
    const from = dragging;
    dragging = null;
    if (from.key) addToBuild(from.key, cat.dataset.cat, index);
    else moveEntry(from.cat, from.index, cat.dataset.cat, index);
  });
}

// ── Events ─────────────────────────────────────────────
function updateFab() {
  const fab = $("#build-fab"), b = currentBuild();
  if (!b || innerWidth >= 1100) {
    fab.hidden = true;
    return;
  }
  const r = $("#build-panel").getBoundingClientRect();
  fab.hidden = r.top < innerHeight && r.bottom > 0;
  fab.textContent = `Билд · ${buildCost(b).count}`;
}

function wireEvents() {
  $("#shop-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tab]");
    if (!btn) return;
    state.tab = btn.dataset.tab;
    for (const b of $$("#shop-tabs [data-tab]")) b.setAttribute("aria-selected", b === btn);
    try { localStorage.setItem("zipline.shop.tab", state.tab); } catch {}
    renderBoard();
  });
  $("#q").addEventListener("input", (e) => {
    state.q = e.target.value;
    renderBoard();
  });
  $("#sort").addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderBoard();
  });
  $("#lang").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-lang]");
    if (!btn) return;
    state.lang = btn.dataset.lang;
    for (const b of $$("#lang button")) b.setAttribute("aria-pressed", b === btn);
    try { localStorage.setItem("zipline.shop.lang", state.lang); } catch {}
    refreshEditor();
  });
  $("#filters-toggle").addEventListener("click", () => {
    const panel = $("#filters");
    panel.hidden = !panel.hidden;
    $("#filters-toggle").setAttribute("aria-expanded", !panel.hidden);
  });
  $("#filter-groups").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-filter]");
    if (!btn) return;
    const f = btn.dataset.filter;
    if (state.filters.has(f)) state.filters.delete(f);
    else state.filters.add(f);
    renderFilters();
    renderBoard();
  });
  $("#active-filters").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-unfilter]");
    if (!btn) return;
    state.filters.delete(btn.dataset.unfilter);
    renderFilters();
    renderBoard();
  });
  $("#filter-mode").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mode]");
    if (!btn) return;
    state.mode = btn.dataset.mode;
    renderFilters();
    renderBoard();
  });
  $("#filters-clear").addEventListener("click", () => {
    state.filters.clear();
    renderFilters();
    renderBoard();
  });
  $("#quick").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-quick]");
    if (!btn) return;
    const q = btn.dataset.quick;
    if (state.quick.has(q)) state.quick.delete(q);
    else {
      state.quick.add(q);
      if (q === "active") state.quick.delete("passive");
      if (q === "passive") state.quick.delete("active");
    }
    for (const b of $$("#quick [data-quick]")) b.setAttribute("aria-pressed", state.quick.has(b.dataset.quick));
    renderBoard();
  });
  $("#legendary").addEventListener("change", (e) => {
    state.legendary = e.target.checked;
    renderBoard();
  });

  const board = $("#board");
  board.addEventListener("click", (e) => {
    const card = e.target.closest(".item");
    if (!card) return;
    if (state.editing && !e.shiftKey) addToBuild(card.dataset.key);
    else openItem(card.dataset.key);
  });
  board.addEventListener("contextmenu", (e) => {
    const card = e.target.closest(".item");
    if (!card || !state.editing) return;
    e.preventDefault();
    openItem(card.dataset.key);
  });
  board.addEventListener("pointerover", (e) => {
    const card = e.target.closest(".item");
    if (card && e.pointerType === "mouse" && !dragging) showTip(card);
  });
  board.addEventListener("pointerout", (e) => {
    const card = e.target.closest(".item");
    if (card && !card.contains(e.relatedTarget)) hideTip();
  });
  board.addEventListener("focusin", (e) => {
    const card = e.target.closest(".item");
    if (card && card.matches(":focus-visible")) showTip(card);
  });
  board.addEventListener("focusout", hideTip);
  addEventListener("scroll", hideTip, { passive: true });

  $("#zip-size").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-size]");
    if (!btn) return;
    state.zipSize = Number(btn.dataset.size);
    for (const b of $$("#zip-size button")) b.setAttribute("aria-pressed", b === btn);
  });
  $("#zip-all").addEventListener("click", () => downloadZip(state.items, "all"));
  $("#zip-shown").addEventListener("click", () => downloadZip(visibleItems(), state.tab === "all" ? "shown" : state.tab));

  const dialog = $("#item-dialog");
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog || e.target.closest("[data-close]")) return closeItem();
    const dl = e.target.closest("[data-dl]");
    if (dl) return downloadIcon(dl.dataset.dl, Number(dl.dataset.size));
    const open = e.target.closest("[data-open]");
    if (open) return openItem(open.dataset.open);
    const add = e.target.closest("[data-add]");
    if (add) {
      addToBuild(add.dataset.add);
      return closeItem();
    }
    const copy = e.target.closest("[data-copy-link]");
    if (copy) {
      navigator.clipboard.writeText(`${location.origin}${location.pathname}#item=${copy.dataset.copyLink}`)
        .then(() => toast("Ссылка на предмет скопирована"), () => toast("Не удалось скопировать"));
    }
  });
  dialog.addEventListener("close", () => {
    if (location.hash.startsWith("#item=")) history.replaceState(null, "", location.pathname);
  });
  $("#tip").addEventListener("click", (e) => e.stopPropagation());
  // Mini item buttons inside the hover tip are not reachable; inside the dialog they open the item.

  $("#build-new").addEventListener("click", () => {
    const b = newBuild();
    saveBuilds();
    openEditor(b.id);
    setTimeout(() => $(".be-name")?.select(), 50);
  });
  $("#build-close").addEventListener("click", closeEditor);
  $("#build-full").addEventListener("click", () => currentBuild() && openView(currentBuild(), false));
  const view = $("#build-view");
  view.addEventListener("click", (e) => {
    const act = e.target.closest("[data-view]");
    if (act) return viewAction(act.dataset.view);
    const shot = e.target.closest("[data-shot]");
    if (shot) return viewAction(`shot:${shot.dataset.shot}`);
    const item = e.target.closest(".vi");
    if (item) openItem(item.dataset.key, false);
  });
  view.addEventListener("pointerover", (e) => {
    const item = e.target.closest(".vi");
    if (item && e.pointerType === "mouse") showTip(item);
  });
  view.addEventListener("pointerout", (e) => {
    const item = e.target.closest(".vi");
    if (item && !item.contains(e.relatedTarget)) hideTip();
  });
  $("#build-share").addEventListener("click", () => currentBuild() && shareBuild(currentBuild()));
  $("#build-export").addEventListener("click", () => currentBuild() && exportBuild(currentBuild()));
  $("#build-fab").addEventListener("click", () => $("#build-panel").scrollIntoView({ behavior: "smooth", block: "start" }));
  addEventListener("scroll", updateFab, { passive: true });
  addEventListener("resize", updateFab);

  const editor = $("#build-editor");
  editor.addEventListener("input", (e) => {
    const b = currentBuild();
    if (!b) return;
    if (e.target.dataset.field) b[e.target.dataset.field] = e.target.value;
    const catEl = e.target.closest(".be-cat");
    if (catEl && e.target.dataset.catField) {
      const cat = b.cats.find((c) => c.id === catEl.dataset.cat);
      if (cat) cat[e.target.dataset.catField] = e.target.value;
    }
    clearTimeout(editor.timer);
    editor.timer = setTimeout(() => {
      touch(b);
      renderBuildCards();
    }, 400);
  });
  editor.addEventListener("click", (e) => {
    const b = currentBuild();
    if (!b) return;
    const heroBtn = e.target.closest("[data-hero]");
    if (heroBtn) {
      const hero = heroBtn.dataset.hero || null;
      if (hero !== b.hero) b.ab = []; // another hero, other abilities
      b.hero = hero;
      touch(b);
      refreshEditor();
      return;
    }
    const ab = e.target.closest("[data-ab]");
    if (ab) {
      b.ab.push(Number(ab.dataset.ab));
      touch(b);
      renderEditor();
      return;
    }
    const act = e.target.closest("[data-act]");
    if (act) return editorAction(act.dataset.act, act.closest(".be-cat")?.dataset.cat);
    const be = e.target.closest(".be-item");
    if (be) return openItemMenu(be);
    // A click on a category makes it the one shop clicks go to.
    const cat = e.target.closest(".be-cat");
    if (cat && state.target !== cat.dataset.cat) {
      state.target = cat.dataset.cat;
      for (const el of $$(".be-cat", editor)) el.classList.toggle("target", el === cat);
    }
  });
  editor.addEventListener("keydown", (e) => {
    const be = e.target.closest(".be-item");
    if (be && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openItemMenu(be);
    }
    if (be && (e.key === "Delete" || e.key === "Backspace")) {
      const b = currentBuild();
      const cat = b.cats.find((c) => c.id === be.dataset.cat);
      cat.items.splice(Number(be.dataset.index), 1);
      touch(b);
      refreshEditor();
    }
  });
  editor.addEventListener("pointerover", (e) => {
    const be = e.target.closest(".be-item[data-key]");
    if (be && e.pointerType === "mouse" && !dragging && $("#item-menu").hidden) showTip(be);
  });
  editor.addEventListener("pointerout", (e) => {
    const be = e.target.closest(".be-item");
    if (be && !be.contains(e.relatedTarget)) hideTip();
  });

  const menu = $("#item-menu");
  menu.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-menu]");
    if (btn) itemMenuAction(btn);
  });
  document.addEventListener("pointerdown", (e) => {
    if (!(e.target instanceof Element)) return;
    if (!menu.hidden && !menu.contains(e.target) && !e.target.closest(".be-item")) closeItemMenu();
    const grid = $("#hero-grid");
    if (grid && !grid.hidden && !e.target.closest("#hero-grid, [data-act='hero']")) grid.hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeItemMenu();
      hideTip();
    }
    if (e.key === "/" && !e.target.matches("input, textarea, select")) {
      e.preventDefault();
      $("#q").focus();
    }
  });

  $("#build-cards").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-build-act]");
    const card = e.target.closest("[data-build]");
    if (!card) return;
    const b = state.builds.find((x) => x.id === card.dataset.build);
    if (!b) return;
    if (btn?.dataset.buildAct === "share") return shareBuild(b);
    if (btn?.dataset.buildAct === "view") return openView(b, false);
    openEditor(b.id);
    $("#shop-layout").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("#build-import").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      importBuild(JSON.parse(await file.text()));
      toast("Билд импортирован");
    } catch (err) {
      console.error(err);
      toast("Это не файл билда zipline");
    }
  });
  addEventListener("hashchange", async () => {
    if (await importFromHash()) return;
    if (!location.hash.startsWith("#item=")) closeView();
    const m = location.hash.match(/^#item=(\w+)$/);
    if (m) openItem(m[1], false);
  });
  addEventListener("storage", (e) => {
    // Another tab changed the builds.
    if (e.key !== STORE) return;
    loadBuilds();
    if (!currentBuild()) state.editing = null;
    refreshEditor();
  });
}

// ── Theme ──────────────────────────────────────────────
// Same switch as on the other pages; the inline script in <head> already set data-theme.
function applyTheme(theme, animate) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add("theme-anim");
    setTimeout(() => root.classList.remove("theme-anim"), 350);
  }
  root.dataset.theme = theme;
  $("#theme-toggle").setAttribute("aria-checked", theme === "dark");
  document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#0c0d0c" : "#ebebe9";
}

function initTheme() {
  applyTheme(document.documentElement.dataset.theme || "light", false);
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", next); } catch {}
    applyTheme(next, true);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    let saved = null;
    try { saved = localStorage.getItem("theme"); } catch {}
    if (!saved) applyTheme(e.matches ? "dark" : "light", true);
  });
}

// ── Boot ───────────────────────────────────────────────
async function init() {
  initTheme();
  try {
    state.tab = localStorage.getItem("zipline.shop.tab") || "all";
    state.lang = localStorage.getItem("zipline.shop.lang") || "ru";
  } catch {}
  if (!SLOTS[state.tab]) state.tab = "all";
  for (const b of $$("#shop-tabs [data-tab]")) b.setAttribute("aria-selected", b.dataset.tab === state.tab);
  wireEvents();
  wireDrag();

  const [items, heroes] = await Promise.all([
    fetch("items/index.json", { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw new Error(`items/index.json: ${r.status}`);
      return r.json();
    }),
    fetch("heroes/index.json", { cache: "no-cache" }).then((r) => r.ok ? r.json() : null).catch(() => null),
  ]);
  state.index = items;
  prepare(items);
  state.heroes = (heroes?.heroes || []).filter((h) => h.abilities?.length).sort((a, b) => a.name.localeCompare(b.name));
  for (const h of state.heroes) state.heroByKey.set(h.key, h);
  if (!items.russian) {
    // The Russian text arrives with the next data update; until then everything is in English.
    state.lang = "en";
    $("#lang").hidden = true;
  }
  for (const b of $$("#lang button")) b.setAttribute("aria-pressed", b.dataset.lang === state.lang);

  loadBuilds();
  try {
    const open = localStorage.getItem(OPEN_BUILD);
    if (open && state.builds.some((b) => b.id === open)) state.editing = open;
  } catch {}
  renderFilters();
  renderEditor();
  renderBoard();
  renderBuildCards();

  const withIcons = state.items.filter((i) => i.icon).length;
  $("#source-status").textContent = `Файлы игры: билд ${items.build} · ${plural(state.items.length, "предмет", "предмета", "предметов")}` +
    (withIcons < state.items.length ? ` · иконок ${withIcons}` : "") + (items.russian ? "" : " · текст на английском");
  $("#source-status").classList.add("ok");

  if (!(await importFromHash())) {
    const m = location.hash.match(/^#item=(\w+)$/);
    if (m) openItem(m[1], false);
  }
}

init().catch((err) => {
  console.error(err);
  $("#board").innerHTML = `<p class="board-empty">Не удалось загрузить предметы. Обновите страницу чуть позже.</p>`;
});
