const $ = (sel) => document.querySelector(sel);
const REFRESH_MS = 10 * 60_000;

const state = { data: null, rank: "all", sort: "wr", items: "top" };

const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const num = new Intl.NumberFormat("ru");
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => `${x.toFixed(1).replace(".", ",")}%`;
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
    </header>
    <p class="lede small">${p.hours} ч после патча · ${num.format(p.games)} матчей. Винрейт по всем рангам в сравнении с неделей до патча.</p>
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

// ── Interactions ───────────────────────────────────────
function select(attr, value, render) {
  document.querySelectorAll(`[data-${attr}]`).forEach((b) => b.setAttribute("aria-selected", b.dataset[attr] === value));
  render();
}

function wireEvents() {
  document.addEventListener("click", (e) => {
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
