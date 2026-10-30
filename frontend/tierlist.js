const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const STORE = "zipline.tierlist";
const DEFAULT_TITLE = "Тир-лист героев";
// The classic tier-list colours, a little muted to sit on the dark board.
const TIERS = [
  { id: "S", color: "#ff7b6e" },
  { id: "A", color: "#ffb162" },
  { id: "B", color: "#ffd966" },
  { id: "C", color: "#b6e070" },
  { id: "D", color: "#74c4f2" },
];
// By physical key, so the shortcuts work with the Russian layout too.
const TIER_KEYS = { KeyS: "S", KeyA: "A", KeyB: "B", KeyC: "C", KeyD: "D" };

const state = { index: null, heroes: [], byKey: new Map(), order: new Map(), tiers: {}, title: "", q: "", picked: null };

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

// ── Storage ────────────────────────────────────────────
function load() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORE)); } catch {}
  state.title = typeof saved?.title === "string" ? saved.title : "";
  // Heroes that left the game drop out; new ones simply wait in the list below.
  const seen = new Set();
  for (const t of TIERS) {
    const keys = Array.isArray(saved?.tiers?.[t.id]) ? saved.tiers[t.id] : [];
    state.tiers[t.id] = keys.filter((k) => state.byKey.has(k) && !seen.has(k) && seen.add(k));
  }
}

function save() {
  try { localStorage.setItem(STORE, JSON.stringify({ title: state.title, tiers: state.tiers })); } catch {}
}

const tierOf = (key) => TIERS.find((t) => state.tiers[t.id].includes(key))?.id || null;
const placedCount = () => TIERS.reduce((n, t) => n + state.tiers[t.id].length, 0);

// ── Drawing ────────────────────────────────────────────
function heroHtml(h) {
  const tier = tierOf(h.key);
  return `<li class="tl-hero${state.picked === h.key ? " picked" : ""}" data-key="${h.key}" tabindex="0" title="${escapeHtml(h.name)}" aria-label="${escapeHtml(h.name)}${tier ? `, тир ${tier}` : ""}">` +
    (h.icon ? `<img src="heroes/icons/${h.icon}" alt="" draggable="false" decoding="async">` : `<i>${escapeHtml(h.name[0])}</i>`) +
    `<span>${escapeHtml(h.name)}</span></li>`;
}

function render() {
  $("#tl-board").innerHTML = TIERS.map((t) => `
    <div class="tl-row" data-tier="${t.id}" style="--t: ${t.color}">
      <div class="tl-label">${t.id}</div>
      <ul class="tl-zone" data-zone="${t.id}" aria-label="Тир ${t.id}">${state.tiers[t.id].map((k) => heroHtml(state.byKey.get(k))).join("")}</ul>
    </div>`).join("");
  const pool = state.heroes.filter((h) => !tierOf(h.key));
  $("#tl-pool").innerHTML = pool.map(heroHtml).join("") + '<li class="tl-empty">Все герои расставлены</li>';
  $("#tl-left").textContent = pool.length ? `· ${pool.length}` : "";
  $(".tl-panel").classList.toggle("picking", !!state.picked);
  filterPool();
}

function filterPool() {
  const q = state.q.trim().toLowerCase();
  for (const li of $$("#tl-pool .tl-hero")) li.hidden = !!q && !state.byKey.get(li.dataset.key).name.toLowerCase().includes(q) && !li.dataset.key.includes(q);
}

function renderPicked() {
  for (const li of $$(".tl-panel .tl-hero")) li.classList.toggle("picked", li.dataset.key === state.picked);
  $(".tl-panel").classList.toggle("picking", !!state.picked);
}

// Puts a hero into a tier at the given place (null: at the end), or back into the list when tier is null.
function place(key, tier, index = null) {
  for (const t of TIERS) state.tiers[t.id] = state.tiers[t.id].filter((k) => k !== key);
  if (tier) state.tiers[tier].splice(index ?? state.tiers[tier].length, 0, key);
  state.picked = null;
  save();
  render();
}

// ── Drag and drop ──────────────────────────────────────
// Pointer events rather than HTML drag and drop, so the same code serves a mouse, a finger and a pen.
// A finger has to rest on a hero for a moment first: a swipe over the list still scrolls the page.
const HOLD_MS = 200;
let drag = null; // {key, el, id, x0, y0, x, y, touch, active, ghost, dx, dy, home, timer, raf}
let dragEndedAt = 0;

function zoneAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const row = el?.closest(".tl-row");
  return row ? $(".tl-zone", row) : el?.closest(".tl-pool") || null;
}

// The hero the dragged one lands in front of; null means at the end.
function landingBefore(zone, x, y) {
  const others = $$(".tl-hero", zone).filter((li) => li !== drag.el);
  if (zone.dataset.zone === "pool") {
    // The list keeps its alphabetical order.
    const at = state.order.get(drag.key);
    return others.find((li) => state.order.get(li.dataset.key) > at) || null;
  }
  return others.find((li) => {
    const r = li.getBoundingClientRect();
    return y < r.top || (y <= r.bottom && x < r.left + r.width / 2);
  }) || null;
}

function startDrag() {
  const { el } = drag;
  const r = el.getBoundingClientRect();
  drag.active = true;
  drag.dx = drag.x0 - r.left;
  drag.dy = drag.y0 - r.top;
  drag.home = { parent: el.parentNode, next: el.nextSibling };
  const ghost = el.cloneNode(true);
  ghost.classList.remove("picked");
  ghost.classList.add("tl-ghost");
  ghost.removeAttribute("tabindex");
  ghost.setAttribute("aria-hidden", "true");
  ghost.style.width = `${r.width}px`;
  ghost.style.height = `${r.height}px`;
  document.body.append(ghost);
  drag.ghost = ghost;
  el.classList.add("tl-placeholder");
  document.body.classList.add("tl-dragging");
  if (drag.touch) navigator.vibrate?.(10);
  moveDrag();
  drag.raf = requestAnimationFrame(autoScroll);
}

// The hero itself moves to where it would land, so the rows make room for it as it goes.
function moveDrag() {
  const { el, x, y } = drag;
  drag.ghost.style.transform = `translate(${x - drag.dx}px, ${y - drag.dy}px) rotate(-3deg)`;
  const zone = zoneAt(x, y);
  for (const z of $$(".drop-on")) if (z !== zone) z.classList.remove("drop-on");
  if (!zone) {
    // Off the board: dropping here changes nothing, so the hero is shown back at its place.
    const { parent, next } = drag.home;
    if (el.parentNode !== parent || el.nextSibling !== next) parent.insertBefore(el, next);
    return;
  }
  zone.classList.add("drop-on");
  const before = landingBefore(zone, x, y);
  if (el.parentNode !== zone || el.nextSibling !== before) zone.insertBefore(el, before);
}

// Near the top or bottom edge the page scrolls, faster the closer the pointer gets.
function autoScroll() {
  if (!drag?.active) return;
  const edge = Math.min(90, innerHeight * 0.15), { y } = drag;
  const v = y < edge ? y - edge : y > innerHeight - edge ? y - (innerHeight - edge) : 0;
  if (v) {
    scrollBy(0, Math.sign(v) * Math.ceil(Math.min(Math.abs(v), edge) / 4));
    moveDrag();
  }
  drag.raf = requestAnimationFrame(autoScroll);
}

function endDrag(commit) {
  const d = drag;
  drag = null;
  clearTimeout(d.timer);
  if (!d.active) return;
  cancelAnimationFrame(d.raf);
  d.ghost.remove();
  document.body.classList.remove("tl-dragging");
  for (const z of $$(".drop-on")) z.classList.remove("drop-on");
  dragEndedAt = performance.now();
  if (!commit) {
    render();
    return;
  }
  const zone = d.el.parentNode;
  const tier = zone.dataset.zone === "pool" ? null : zone.dataset.zone;
  place(d.key, tier, tier ? $$(".tl-hero", zone).indexOf(d.el) : null);
}

function wireDrag() {
  const panel = $(".tl-panel");
  panel.addEventListener("pointerdown", (e) => {
    const el = e.target.closest(".tl-hero");
    if (!el || drag || (e.pointerType === "mouse" && e.button !== 0)) return;
    drag = { key: el.dataset.key, el, id: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, touch: e.pointerType !== "mouse", active: false };
    if (drag.touch) drag.timer = setTimeout(() => drag && !drag.active && startDrag(), HOLD_MS);
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag.x = e.clientX;
    drag.y = e.clientY;
    if (drag.active) return moveDrag();
    const moved = Math.hypot(drag.x - drag.x0, drag.y - drag.y0);
    if (drag.touch) {
      // Moved before the hold: the finger is scrolling the page.
      if (moved > 10) endDrag(false);
    } else if (moved > 5) {
      startDrag();
    }
  });
  document.addEventListener("pointerup", (e) => drag && e.pointerId === drag.id && endDrag(true));
  document.addEventListener("pointercancel", (e) => drag && e.pointerId === drag.id && endDrag(false));
  // While a finger carries a hero the page must not scroll under it.
  document.addEventListener("touchmove", (e) => drag?.active && e.cancelable && e.preventDefault(), { passive: false });
  // A long press would open the image menu instead.
  panel.addEventListener("contextmenu", (e) => e.target.closest(".tl-hero") && e.preventDefault());
}

// ── The picture ────────────────────────────────────────
// Drawn on a canvas of the exact size, so it holds only the tier list: no buttons, no page around it.
// The portraits get the largest size at which every tier fits.
const MONO = '"Anonymous Pro", ui-monospace, monospace';

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

const tryImage = (src) => src ? Promise.race([loadImage(src).catch(() => null), new Promise((r) => setTimeout(() => r(null), 10_000))]) : Promise.resolve(null);

function ellipsize(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  while (text.length > 1 && ctx.measureText(`${text}…`).width > width) text = text.slice(0, -1);
  return `${text.trimEnd()}…`;
}

function shotLayout(counts, width, room, rowGap) {
  let fit = null;
  for (let size = 170; size >= 24; size -= 2) {
    const tileH = size * 1.25, gap = Math.max(4, Math.round(size * 0.06));
    const perRow = Math.max(1, Math.floor((width - gap * 2 + gap) / (size + gap)));
    const heights = counts.map((n) => {
      const lines = Math.max(1, Math.ceil(n / perRow));
      return gap * 2 + lines * tileH + (lines - 1) * gap;
    });
    fit = { size, tileH, gap, perRow, heights, total: heights.reduce((a, b) => a + b, 0) + rowGap * (counts.length - 1) };
    if (fit.total <= room) break;
  }
  return fit;
}

async function drawTierShot(W, H) {
  await Promise.all([`700 20px ${MONO}`, `400 20px ${MONO}`].map((f) => document.fonts.load(f).catch(() => null)));
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  const pad = Math.round(W * 0.04);

  // Paper-dark background like the shop board.
  const bg = ctx.createRadialGradient(W / 2, 0, 0, W / 2, 0, Math.max(W, H));
  bg.addColorStop(0, "#2a241c");
  bg.addColorStop(0.7, "#15120e");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // Header: eyebrow, the title on a white label; our logo in the top right corner, the title stops short of it.
  let titleW = W - pad * 2;
  const logo = await tryImage("assets/logo-white.png");
  if (logo) {
    const lh = Math.round(H * 0.06), lw = lh * logo.naturalWidth / logo.naturalHeight;
    ctx.drawImage(logo, W - pad - lw, pad, lw, lh);
    titleW -= lw + pad * 0.6;
  }
  const eyebrow = Math.round(H * 0.017);
  ctx.textBaseline = "top";
  ctx.font = `700 ${eyebrow}px ${MONO}`;
  ctx.fillStyle = "#3cc493";
  ctx.fillText(`ТИР-ЛИСТ DEADLOCK · ${new Date().toLocaleDateString("ru-RU")}`, pad, pad);
  const name = (state.title.trim() || DEFAULT_TITLE).toUpperCase();
  let titleSize = Math.round(H * 0.045);
  ctx.font = `700 ${titleSize}px ${MONO}`;
  while (ctx.measureText(name).width + titleSize * 0.6 > titleW && titleSize > 30) ctx.font = `700 ${--titleSize}px ${MONO}`;
  const title = ellipsize(ctx, name, titleW - titleSize * 0.6);
  const ty = pad + eyebrow * 1.9;
  ctx.fillStyle = "#f6f6f4";
  ctx.fillRect(pad, ty, ctx.measureText(title).width + titleSize * 0.6, titleSize * 1.3);
  ctx.fillStyle = "#111";
  ctx.fillText(title, pad + titleSize * 0.3, ty + titleSize * 0.18);

  // The tiers fill the rest; spare height is shared between the rows.
  const top = Math.round(ty + titleSize * 1.3 + pad * 0.6), room = H - pad - top;
  const labelW = Math.round(W * 0.085), rowGap = Math.max(4, Math.round(H * 0.006)), zoneW = W - pad * 2 - labelW;
  const { size, tileH, gap, perRow, heights, total } = shotLayout(TIERS.map((t) => state.tiers[t.id].length), zoneW, room, rowGap);
  const spare = Math.max(0, room - total) / TIERS.length;
  const portraits = new Map(await Promise.all(TIERS.flatMap((t) => state.tiers[t.id]).map(async (k) => {
    const h = state.byKey.get(k);
    return [k, h.icon ? await tryImage(`heroes/icons/${h.icon}`) : null];
  })));

  let y = top;
  TIERS.forEach((t, i) => {
    const rowH = heights[i] + spare, keys = state.tiers[t.id];
    ctx.fillStyle = "#211d17";
    ctx.fillRect(pad + labelW, y, zoneW, rowH);
    ctx.fillStyle = t.color;
    ctx.fillRect(pad, y, labelW, rowH);
    ctx.fillStyle = "#15120e";
    ctx.font = `700 ${Math.round(Math.min(labelW * 0.5, rowH * 0.5))}px ${MONO}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(t.id, pad + labelW / 2, y + rowH / 2);

    // Portraits: centred in the row's height, the name over the lower edge as on the page.
    const lines = Math.max(1, Math.ceil(keys.length / perRow));
    const y0 = y + (rowH - (lines * tileH + (lines - 1) * gap)) / 2;
    const ns = Math.max(9, Math.round(size * 0.12));
    keys.forEach((k, j) => {
      const tx = pad + labelW + gap + (j % perRow) * (size + gap), ty2 = y0 + Math.floor(j / perRow) * (tileH + gap);
      const hero = state.byKey.get(k), img = portraits.get(k);
      ctx.fillStyle = "#2a2622";
      ctx.fillRect(tx, ty2, size, tileH);
      if (img) {
        // Cover the tile, keeping the top of the portrait (the face).
        const scale = Math.max(size / img.naturalWidth, tileH / img.naturalHeight);
        const sw = size / scale, sh = tileH / scale;
        ctx.drawImage(img, (img.naturalWidth - sw) / 2, 0, sw, sh, tx, ty2, size, tileH);
      }
      const shade = ctx.createLinearGradient(0, ty2 + tileH - ns * 3, 0, ty2 + tileH);
      shade.addColorStop(0, "rgba(0, 0, 0, 0)");
      shade.addColorStop(0.6, "rgba(0, 0, 0, .85)");
      shade.addColorStop(1, "rgba(0, 0, 0, .85)");
      ctx.fillStyle = shade;
      ctx.fillRect(tx, ty2 + tileH - ns * 3, size, ns * 3);
      ctx.font = `700 ${ns}px ${MONO}`;
      ctx.fillStyle = "#fff";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(ellipsize(ctx, hero.name.toUpperCase(), size - 6), tx + size / 2, ty2 + tileH - ns * 0.55);
      ctx.strokeStyle = "rgba(255, 255, 255, .14)";
      ctx.lineWidth = 1;
      ctx.strokeRect(tx + 0.5, ty2 + 0.5, size - 1, tileH - 1);
    });
    ctx.textAlign = "left";
    y += rowH + rowGap;
  });
  return canvas;
}

function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

async function downloadShot(W, H) {
  if (!placedCount()) {
    toast("Сначала поставьте в тир хотя бы одного героя");
    return;
  }
  const btn = $("#tl-shot");
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  try {
    toast(`Рисуем ${W}×${H}…`);
    const canvas = await drawTierShot(W, H);
    const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    const slug = (state.title.trim() || "tier-list").toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "-").replace(/^-|-$/g, "") || "tier-list";
    saveBlob(blob, `${slug}-${W}x${H}.png`);
    toast(`Картинка ${W}×${H} готова`);
  } catch (err) {
    console.error(err);
    toast("Не получилось нарисовать картинку");
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
}

// ── Events ─────────────────────────────────────────────
// Without dragging: pick a hero, then a row (or the list below); or the S A B C D keys.
function wireEvents() {
  const panel = $(".tl-panel");
  panel.addEventListener("click", (e) => {
    if (performance.now() - dragEndedAt < 300) return; // the click that ends a drag
    const hero = e.target.closest(".tl-hero");
    if (hero) {
      state.picked = state.picked === hero.dataset.key ? null : hero.dataset.key;
      renderPicked();
      return;
    }
    if (!state.picked) return;
    const row = e.target.closest(".tl-row");
    if (row) place(state.picked, row.dataset.tier);
    else if (e.target.closest(".tl-pool")) place(state.picked, null);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (drag?.active) endDrag(false);
      else if (state.picked) {
        state.picked = null;
        renderPicked();
      }
      return;
    }
    if (e.target.closest?.("input, textarea, select") || e.ctrlKey || e.metaKey || e.altKey) return;
    const focused = e.target.closest?.(".tl-hero");
    const key = focused?.dataset.key || state.picked;
    if (!key || drag) return;
    let tier;
    if (TIER_KEYS[e.code]) tier = TIER_KEYS[e.code];
    else if (e.key === "Delete" || e.key === "Backspace" || e.key === "0") tier = null;
    else if (focused && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      focused.click();
      return;
    } else return;
    e.preventDefault();
    place(key, tier);
    $(`.tl-hero[data-key="${key}"]`)?.focus();
  });
  $("#tl-title").addEventListener("input", (e) => {
    state.title = e.target.value;
    save();
  });
  $("#tl-q").addEventListener("input", (e) => {
    state.q = e.target.value;
    filterPool();
  });
  $("#tl-reset").addEventListener("click", () => {
    if (!placedCount() || !confirm("Вернуть всех героев в общий список?")) return;
    for (const t of TIERS) state.tiers[t.id] = [];
    state.picked = null;
    save();
    render();
  });
  $("#tl-shot").addEventListener("click", () => downloadShot(1080, 1080));
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
  wireEvents();
  wireDrag();
  const res = await fetch("heroes/index.json", { cache: "no-cache" });
  if (!res.ok) throw new Error(`heroes/index.json: ${res.status}`);
  state.index = await res.json();
  state.heroes = [...state.index.heroes].sort((a, b) => a.name.localeCompare(b.name));
  state.heroes.forEach((h, i) => {
    state.byKey.set(h.key, h);
    state.order.set(h.key, i);
  });
  load();
  $("#tl-title").value = state.title;
  render();
  $("#source-status").textContent = `Файлы игры: билд ${state.index.build} · ${plural(state.heroes.length, "герой", "героя", "героев")}`;
  $("#source-status").classList.add("ok");
}

init().catch((err) => {
  console.error(err);
  $("#tl-pool").innerHTML = `<li class="board-loading">Не удалось загрузить список героев. Обновите страницу чуть позже.</li>`;
});
