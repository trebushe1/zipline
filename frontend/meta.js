const $ = (sel) => document.querySelector(sel);
const REFRESH_MS = 10 * 60_000;

const state = { data: null, rank: "all", sort: "wr", items: "top" };

const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const num = new Intl.NumberFormat("ru");
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => `${x.toFixed(1).replace(".", ",")}%`;
const plural = (n, one, few, many) => {
  const n10 = n % 10, n100 = n % 100;
  return n10 === 1 && n100 !== 11 ? one : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? few : many;
};
const games = (n) => `${num.format(n)} ${plural(n, "матч", "матча", "матчей")}`;
const signed = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(1).replace(".", ",")}`;

function ago(ms) {
  const s = (ms - Date.now()) / 1000;
  if (Math.abs(s) < 60) return "только что";
  if (Math.abs(s) < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (Math.abs(s) < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

// ── Data ───────────────────────────────────────────────
// meta.json is the snapshot taken at deploy; the live collector refreshes Firebase every 3 hours.
async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function fetchData() {
  const base = await getJson(`meta.json?t=${Date.now()}`).catch(() => ({}));
  if (!base.realtimeUrl) return base;
  const live = await getJson(`${base.realtimeUrl}/live/meta.json`).catch(() => null);
  return live?.updatedAt > (base.updatedAt || 0) ? { ...live, realtimeUrl: base.realtimeUrl } : base;
}

// ── Rendering ──────────────────────────────────────────
function deltaBadge(d, title = "за неделю") {
  if (d == null) return `<span class="meta-delta" title="нет данных за прошлую неделю">—</span>`;
  const cls = d >= 0.3 ? "up" : d <= -0.3 ? "down" : "";
  return `<span class="meta-delta ${cls}" title="изменение винрейта ${title}">${signed(d)}</span>`;
}

function bar(wr) {
  // 40–60% fills the bar; 50% sits in the middle.
  const fill = Math.max(0, Math.min(100, (wr - 40) * 5));
  return `<span class="wr-bar" aria-hidden="true"><i style="width:${fill}%"></i><b></b></span>`;
}

function heroRow(r, i) {
  return `<li class="meta-row">
    <span class="meta-rank">${i + 1}</span>
    ${r.icon ? `<img class="meta-icon" src="heroes/icons/${escapeHtml(r.icon)}" alt="" loading="lazy">` : `<i class="meta-icon"></i>`}
    <span class="meta-name">${escapeHtml(r.name)}<small>${num.format(r.matches)} матчей</small></span>
    <span class="meta-wr">${pct(r.wr)}${bar(r.wr)}</span>
    ${deltaBadge(r.wrDelta)}
    <span class="meta-pick" title="в какой доле матчей встречается герой">${pct(r.pick)}<small>пик</small></span>
  </li>`;
}

function itemRow(r, i) {
  return `<li class="meta-row">
    <span class="meta-rank">${i + 1}</span>
    <img class="meta-icon item" src="items/thumbs/${escapeHtml(r.key)}.webp" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
    <span class="meta-name">${escapeHtml(r.name)}<small>${r.tier ? `тир ${r.tier} · ` : ""}${num.format(r.matches)} покупок</small></span>
    <span class="meta-wr">${pct(r.wr)}${bar(r.wr)}</span>
    ${deltaBadge(r.wrDelta)}
    <span class="meta-pick" title="изменение числа покупок за неделю">${r.usageDelta == null ? "—" : `${signed(r.usageDelta)}%`}<small>покупок</small></span>
  </li>`;
}

function renderStats(d) {
  const heroes = d.heroes_all || [];
  $("#stat-games").textContent = d.games_all ? num.format(d.games_all) : "—";
  $("#stat-best").textContent = heroes[0] ? `${heroes[0].name} · ${pct(heroes[0].wr)}` : "—";
  const popular = [...heroes].sort((a, b) => b.pick - a.pick)[0];
  $("#stat-popular").textContent = popular ? `${popular.name} · ${pct(popular.pick)}` : "—";
  $("#stat-updated").textContent = d.updatedAt ? ago(d.updatedAt) : "—";
}

function renderPatch(d) {
  const p = d.patch;
  const box = $("#patch");
  if (!p) { box.hidden = true; return; }
  const byKey = new Map(p.rows.map((r) => [r.key, r]));
  const card = (key, kind) => {
    const r = byKey.get(key);
    return r ? `<li class="patch-hero ${kind}">
      ${r.icon ? `<img src="heroes/icons/${escapeHtml(r.icon)}" alt="" loading="lazy">` : ""}
      <span class="meta-name">${escapeHtml(r.name)}<small>${pct(r.wr)}</small></span>
      ${deltaBadge(r.wrDelta, "после патча")}
    </li>` : "";
  };
  box.hidden = false;
  box.innerHTML = `
    <header class="meta-head">
      <h2>Кто выиграл от патча ${p.build}</h2>
      <a class="link-btn" href="changes.html?build=${encodeURIComponent(p.file.replace(/\.json$/, ""))}">что изменили →</a>
      <button class="btn export-btn" id="patch-shot" type="button">Скачать 1080×1080</button>
    </header>
    <p class="lede small">${p.hours} ч после патча · ${games(p.games)}. Винрейт по всем рангам в сравнении с неделей до патча.</p>
    <div class="patch-cols">
      <div><h3 class="param-hero-title">Усилились</h3><ol class="patch-list">${p.winners.map((k) => card(k, "up")).join("") || "<li class=\"empty\">заметных нет</li>"}</ol></div>
      <div><h3 class="param-hero-title">Просели</h3><ol class="patch-list">${p.losers.map((k) => card(k, "down")).join("") || "<li class=\"empty\">заметных нет</li>"}</ol></div>
    </div>`;
}

function renderHeroes() {
  const rows = [...(state.data?.[`heroes_${state.rank}`] || [])];
  const key = state.sort;
  rows.sort((a, b) => (b[key] ?? -1e9) - (a[key] ?? -1e9));
  $("#heroes").innerHTML = rows.map(heroRow).join("") || `<li class="empty pad">Нет данных.</li>`;
}

function renderItems() {
  let rows = [...(state.data?.items || [])];
  if (state.items === "up") rows = rows.filter((r) => r.wrDelta > 0).sort((a, b) => b.wrDelta - a.wrDelta);
  else if (state.items === "down") rows = rows.filter((r) => r.wrDelta < 0).sort((a, b) => a.wrDelta - b.wrDelta);
  $("#items").innerHTML = rows.slice(0, 20).map(itemRow).join("") || `<li class="empty pad">Нет данных.</li>`;
}

function apply(d) {
  state.data = d;
  renderStats(d);
  renderPatch(d);
  renderHeroes();
  renderItems();
}

// ── The picture «who won and lost from the patch» ─────
// Same look as the tier list picture: dark paper, green eyebrow, the title on a white label, our logo.
const MONO = '"JetBrains Mono", ui-monospace, monospace';
const GREEN = "#3cc493", RED = "#e2694a", PAPER = "#f6f6f4";

const images = new Map();
function loadImage(src) {
  if (!images.has(src)) {
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

async function drawPatchShot(p, W = 1080, H = 1080) {
  await Promise.all([`800 20px ${MONO}`, `700 20px ${MONO}`, `500 20px ${MONO}`].map((f) => document.fonts.load(f).catch(() => null)));
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  const pad = Math.round(W * 0.05);

  const bg = ctx.createRadialGradient(W / 2, 0, 0, W / 2, 0, Math.max(W, H));
  bg.addColorStop(0, "#2a241c");
  bg.addColorStop(0.7, "#15120e");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // Header: eyebrow, the title on a white label, our logo top right.
  let titleW = W - pad * 2;
  const logo = await tryImage("assets/logo-white.png");
  if (logo) {
    const lh = Math.round(H * 0.06), lw = lh * logo.naturalWidth / logo.naturalHeight;
    ctx.drawImage(logo, W - pad - lw, pad, lw, lh);
    titleW -= lw + pad * 0.6;
  }
  ctx.textBaseline = "top";
  const eyebrow = Math.round(H * 0.019);
  ctx.font = `700 ${eyebrow}px ${MONO}`;
  ctx.fillStyle = GREEN;
  ctx.fillText("КТО ВЫИГРАЛ И ПРОИГРАЛ ОТ ПАТЧА", pad, pad);
  const titleSize = Math.round(H * 0.06);
  ctx.font = `800 ${titleSize}px ${MONO}`;
  const title = ellipsize(ctx, `ПАТЧ ${p.build}`, titleW - titleSize * 0.6);
  const ty = pad + eyebrow * 1.9;
  ctx.fillStyle = PAPER;
  ctx.fillRect(pad, ty, ctx.measureText(title).width + titleSize * 0.6, titleSize * 1.3);
  ctx.fillStyle = "#111";
  ctx.fillText(title, pad + titleSize * 0.3, ty + titleSize * 0.18);
  const sub = Math.round(H * 0.02);
  ctx.font = `500 ${sub}px ${MONO}`;
  ctx.fillStyle = "rgba(246, 246, 244, .7)";
  ctx.fillText(`${p.hours} ч после патча · ${games(p.games)} · винрейт, все ранги`, pad, ty + titleSize * 1.3 + sub * 0.9);

  // Two columns of hero cards: winners left, losers right.
  const byKey = new Map(p.rows.map((r) => [r.key, r]));
  const top = Math.round(ty + titleSize * 1.3 + sub * 3.2), bottom = H - pad - sub * 2.2;
  const colGap = Math.round(W * 0.03), colW = (W - pad * 2 - colGap) / 2;
  // Five rows per column: portrait on the left, name and win rate in the middle, the change large on the right.
  const ROWS = 5;
  const headH = Math.round(H * 0.05), cardGap = Math.round(H * 0.012);
  const cardH = (bottom - top - headH - cardGap * ROWS) / ROWS;
  const columns = [["УСИЛИЛИСЬ", p.winners, GREEN], ["ПРОСЕЛИ", p.losers, RED]];
  const portraits = new Map(await Promise.all([...p.winners, ...p.losers].map(async (k) => {
    const r = byKey.get(k);
    return [k, r?.icon ? await tryImage(`heroes/icons/${r.icon}`) : null];
  })));

  columns.forEach(([label, keys, color], c) => {
    const x = pad + c * (colW + colGap);
    ctx.fillStyle = color;
    ctx.fillRect(x, top, colW, headH);
    ctx.fillStyle = "#15120e";
    ctx.font = `800 ${Math.round(headH * 0.5)}px ${MONO}`;
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + headH * 0.35, top + headH / 2);

    if (!keys.length) {
      ctx.fillStyle = "rgba(246, 246, 244, .5)";
      ctx.font = `500 ${sub}px ${MONO}`;
      ctx.fillText("заметных изменений нет", x + headH * 0.35, top + headH + cardGap + cardH / 2);
    }
    keys.slice(0, ROWS).forEach((k, i) => {
      const r = byKey.get(k);
      if (!r) return;
      const y = top + headH + cardGap + i * (cardH + cardGap);
      ctx.fillStyle = "#211d17";
      ctx.fillRect(x, y, colW, cardH);
      // Portrait: a square on the left, covering it from the top (the face).
      const img = portraits.get(k), ph = cardH;
      ctx.fillStyle = "#2a2622";
      ctx.fillRect(x, y, ph, ph);
      if (img) {
        const scale = Math.max(ph / img.naturalWidth, ph / img.naturalHeight);
        const side = ph / scale;
        ctx.drawImage(img, (img.naturalWidth - side) / 2, 0, side, side, x, y, ph, ph);
      }
      ctx.fillStyle = color;
      ctx.fillRect(x, y, Math.max(4, Math.round(W * 0.005)), cardH);

      // The change, right-aligned and large.
      const inner = cardH * 0.16;
      ctx.textBaseline = "middle";
      ctx.textAlign = "right";
      ctx.font = `800 ${Math.round(cardH * 0.4)}px ${MONO}`;
      ctx.fillStyle = color;
      const delta = signed(r.wrDelta);
      ctx.fillText(delta, x + colW - inner, y + cardH / 2);
      const dw = ctx.measureText(delta).width;
      ctx.textAlign = "left";

      // Name and win rate between the portrait and the change; the name shrinks to fit before it is cut.
      const tx = x + ph + inner, tw = colW - ph - inner * 3 - dw;
      ctx.textBaseline = "alphabetic";
      let nameSize = Math.round(cardH * 0.22);
      ctx.font = `800 ${nameSize}px ${MONO}`;
      while (ctx.measureText(r.name.toUpperCase()).width > tw && nameSize > cardH * 0.12) ctx.font = `800 ${--nameSize}px ${MONO}`;
      ctx.fillStyle = PAPER;
      ctx.fillText(ellipsize(ctx, r.name.toUpperCase(), tw), tx, y + cardH * 0.47);
      ctx.font = `500 ${Math.round(cardH * 0.16)}px ${MONO}`;
      ctx.fillStyle = "rgba(246, 246, 244, .75)";
      ctx.fillText(ellipsize(ctx, `винрейт ${pct(r.wr)}`, tw), tx, y + cardH * 0.78);
    });
  });

  ctx.textBaseline = "alphabetic";
  ctx.font = `500 ${Math.round(sub * 0.85)}px ${MONO}`;
  ctx.fillStyle = "rgba(246, 246, 244, .45)";
  ctx.fillText("Сравнение с неделей до патча · данные deadlock-api.com", pad, H - pad + sub * 0.2);
  return canvas;
}

function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2200);
}

async function downloadPatchShot(btn) {
  const p = state.data?.patch;
  if (!p) return;
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  try {
    toast("Рисуем 1080×1080…");
    const canvas = await drawPatchShot(p);
    const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    saveBlob(blob, `patch-${p.build}-winners-1080x1080.png`);
    toast("Картинка 1080×1080 готова");
  } catch (err) {
    console.error(err);
    toast("Не получилось нарисовать картинку");
  } finally {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
}

// ── Interactions ───────────────────────────────────────
function select(attr, value, render) {
  document.querySelectorAll(`[data-${attr}]`).forEach((b) => b.setAttribute("aria-selected", b.dataset[attr] === value));
  render();
}

function wireEvents() {
  document.addEventListener("click", (e) => {
    const shot = e.target.closest("#patch-shot");
    if (shot) return downloadPatchShot(shot);
    const b = e.target.closest("[data-rank],[data-sort],[data-items]");
    if (!b) return;
    if (b.dataset.rank) { state.rank = b.dataset.rank; select("rank", state.rank, renderHeroes); }
    if (b.dataset.sort) { state.sort = b.dataset.sort; select("sort", state.sort, renderHeroes); }
    if (b.dataset.items) { state.items = b.dataset.items; select("items", state.items, renderItems); }
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
  wireEvents();
  try {
    apply(await fetchData());
  } catch (err) {
    console.error(err);
    $("#heroes").innerHTML = `<li class="empty pad">Не удалось загрузить статистику. Попробуйте обновить страницу.</li>`;
  }
  setInterval(() => fetchData().then(apply).catch(console.error), REFRESH_MS);
}

init();
