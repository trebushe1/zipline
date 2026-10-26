const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const REFRESH_MS = 90_000;
const HLS = "https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js";
const HOUR = 3_600_000;

const state = { data: null, posts: [], tab: "pulse", topic: "", q: "", shown: PAGE, pending: null };

// ── Formatting ─────────────────────────────────────────
const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const fullDate = new Intl.DateTimeFormat("ru", { dateStyle: "long", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const num = new Intl.NumberFormat("ru");

function ago(ms) {
  if (!ms) return "—";
  const s = (ms - Date.now()) / 1000;
  const abs = Math.abs(s);
  if (abs < 60) return "только что";
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return shortDate.format(ms);
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  return `${n} ${n10 === 1 && n100 !== 11 ? one : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? few : many}`;
}

// Share of same-age posts this one beats: the normal CDF of its strength z.
function beats(z) {
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z >= 0 ? 1 - p : p;
}

const ageH = (p) => (Date.now() - p.created) / HOUR;
const link = (p) => `https://www.reddit.com/r/${state.data?.subreddit || "DeadlockTheGame"}/comments/${p.id}/`;

const TOPICS = { news: "Новость", clip: "Клип", art: "Арт", talk: "Обсуждение", other: "" };
const BADGES = { rising: ["Взлетает", "b-rising"], hit: ["Хит", "b-hit"], new: ["Новый", "b-new"] };

const icons = {
  open: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  view: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  kind: {
    text: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 11h14M5 16h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    video: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>',
  },
};

// ── Data ───────────────────────────────────────────────
// reddit.json is the snapshot taken at deploy (or served by the local server); the live
// collector refreshes /live/reddit in Firebase every minute and the page reads that.
async function getJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function fetchData() {
  if (!state.realtimeUrl) {
    const base = await getJson(`reddit.json?t=${Date.now()}`);
    state.realtimeUrl = base.realtimeUrl || "";
    if (!state.realtimeUrl) return base;
    const live = await getJson(`${state.realtimeUrl}/live/reddit.json`).catch(() => null);
    return live && (live.updatedAt || 0) > (base.updatedAt || 0) ? live : base;
  }
  return getJson(`${state.realtimeUrl}/live/reddit.json`);
}

// Firebase drops empty arrays and nulls, so restore what the page expects.
const normalize = (p) => ({ badges: [], comments: 0, z: 0, mom: 0, pulse: 0, topic: "other", ...p });

function apply(data, { quiet = false } = {}) {
  const posts = (Array.isArray(data.posts) ? data.posts : Object.values(data.posts || {})).map(normalize);
  const known = new Set(state.posts.map((p) => p.id));
  const fresh = posts.filter((p) => !known.has(p.id)).length;
  // A reader in the middle of the feed keeps their place; the bar offers the new order.
  if (quiet && state.posts.length && window.scrollY > 300) {
    state.pending = { data, posts };
    const bar = $("#new-bar");
    bar.hidden = false;
    bar.textContent = fresh ? `Новых постов: ${fresh} — обновить ленту` : "Лента обновилась — показать";
    return renderChrome(data, posts);
  }
  state.data = data;
  state.posts = posts;
  state.pending = null;
  $("#new-bar").hidden = true;
  renderChrome(data, posts);
  renderPosts();
}

// ── Ordering ───────────────────────────────────────────
function ordered() {
  const q = state.q.trim().toLowerCase();
  let rows = state.posts.filter((p) =>
    (!state.topic || p.topic === state.topic) &&
    (!q || [p.title, p.excerpt, p.author].some((s) => s && s.toLowerCase().includes(q))));
  switch (state.tab) {
    case "rising":
      return rising(rows);
    case "new":
      return rows.sort((a, b) => b.created - a.created);
    case "top":
      rows = rows.filter((p) => ageH(p) <= 24);
      return state.data?.source === "api"
        ? rows.sort((a, b) => (b.score || 0) - (a.score || 0))
        : rows.sort((a, b) => (a.day_rank || 1e3) - (b.day_rank || 1e3) || (a.hot_rank || 1e3) - (b.hot_rank || 1e3) || b.z - a.z);
    default:
      return rows.sort((a, b) => b.pulse - a.pulse);
  }
}

// Young posts well ahead of their peers, the ones still climbing first. The «Взлетает»
// badge marks the strongest of them.
const rising = (posts) => posts.filter((p) => ageH(p) <= 4 && p.z + p.mom / 2 > 0.6)
  .sort((a, b) => b.z + b.mom / 2 - (a.z + a.mom / 2));

// ── Rendering ──────────────────────────────────────────
function renderChrome(data, posts) {
  const climbing = rising(posts);
  const top = [...posts].sort((a, b) => b.pulse - a.pulse)[0];
  $("#stat-day").textContent = data.stats?.day ?? posts.filter((p) => ageH(p) < 24).length;
  $("#stat-rising").textContent = climbing.length;
  $("#rising-count").textContent = climbing.length || "";
  $("#stat-top").textContent = top ? top.title : "—";
  $("#stat-top").title = top ? top.title : "";
  $("#stat-updated").textContent = ago(data.updatedAt);
  const hourly = data.stats?.hourly || [];
  const max = Math.max(1, ...hourly);
  $("#spark").innerHTML = hourly
    .map((n, i) => `<i class="${n ? "on" : ""}" style="height:${Math.max(6, (n / max) * 100)}%" title="${23 - i === 0 ? "этот час" : `${23 - i} ч назад`}: ${n}"></i>`)
    .join("");

  const el = $("#source-status");
  const source = data.source === "api" ? "Reddit API" : "Reddit RSS";
  el.className = "collector-status";
  if (data.status?.ok === false) {
    el.classList.add("err");
    el.textContent = `${source}: ${data.status.error} · данные от ${ago(data.updatedAt)}`;
  } else if (data.updatedAt) {
    el.classList.add("ok");
    el.textContent = `${source}: r/${data.subreddit} · обновлено ${ago(data.updatedAt)}`;
  } else {
    el.textContent = "Reddit: сборщик ещё не запускался";
  }
}

function renderPosts() {
  if (!state.data) return;
  const rows = ordered();
  const list = $("#posts");
  list.innerHTML = "";
  rows.slice(0, state.shown).forEach((p, i) => list.append(renderPost(p, i + 1)));
  $("#more").hidden = rows.length <= state.shown;
  $("#empty").hidden = rows.length > 0;
  $("#empty").textContent = state.posts.length
    ? state.tab === "rising" && !state.q && !state.topic
      ? "Сейчас ничего не взлетает: свежие посты набирают голоса как обычно."
      : "Ничего не найдено. Попробуйте другую вкладку или тему."
    : "Постов пока нет — сборщик Reddit ещё не прислал данные.";
}

function meter(z) {
  const share = beats(z);
  const on = Math.round(share * 10);
  return `<span class="meter" title="Сильнее ${Math.round(share * 100)}% постов того же возраста">${
    Array.from({ length: 10 }, (_, i) => `<i class="${i < on ? "on" : ""}"></i>`).join("")}</span>`;
}

function stats(p) {
  const out = [];
  out.push(`<span class="rp-strength">${meter(p.z)}сильнее ${Math.round(beats(p.z) * 100)}% ровесников</span>`);
  if (p.mom >= 0.2) out.push(`<span class="rp-up" title="Сила за последние полчаса">▲ разгон +${p.mom.toFixed(1)}</span>`);
  else if (p.mom <= -0.2) out.push(`<span class="rp-down" title="Сила за последние полчаса">▼ сдаёт ${p.mom.toFixed(1)}</span>`);
  if (p.score != null) out.push(`<span title="Рейтинг">▲ ${num.format(p.score)}${p.ratio ? ` · ${Math.round(p.ratio * 100)}% за` : ""}</span>`);
  out.push(`<span title="Комментарии">💬 ${num.format(p.comments || 0)}</span>`);
  const age = ageH(p);
  if (age < 1 && p.hour_rank) out.push(`<span title="Место среди постов последнего часа">#${p.hour_rank} за час</span>`);
  else if (p.day_rank && age < 24) out.push(`<span title="Место в топе Reddit за сутки">#${p.day_rank} за сутки</span>`);
  else if (p.hot_rank) out.push(`<span title="Место во вкладке Hot">#${p.hot_rank} в Hot</span>`);
  return out.join("");
}

function renderPost(p, place) {
  const li = document.createElement("li");
  li.className = "rpost";
  const blur = p.nsfw || p.spoiler;
  const thumb = p.thumb
    ? `<img src="${escapeHtml(p.thumb)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer"${blur ? ' class="blur"' : ""}>`
    : `<span class="rp-kind">${icons.kind[p.kind] || icons.kind.text}</span>`;
  const badges = [
    ...p.badges.filter((b) => BADGES[b]).map((b) => `<span class="badge ${BADGES[b][1]}">${BADGES[b][0]}</span>`),
    // The subreddit's own flair when the API gives it, else our guess from the title and link.
    p.flair || TOPICS[p.topic]
      ? `<span class="badge b-topic${p.topic === "news" ? " b-news" : ""}">${escapeHtml(p.flair || TOPICS[p.topic])}</span>` : "",
    p.nsfw ? '<span class="badge b-warn">18+</span>' : p.spoiler ? '<span class="badge b-warn">Спойлер</span>' : "",
  ].join("");
  const play = p.kind === "video" && p.thumb ? `<span class="rp-play">${icons.kind.video}</span>` : "";
  li.innerHTML = `
    <span class="rp-place">${String(place).padStart(2, "0")}</span>
    ${VIEW[p.kind]
      ? `<button class="rp-thumb" type="button" data-view aria-label="${VIEW[p.kind]}">${thumb}${play}</button>`
      : `<a class="rp-thumb" href="${link(p)}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">${thumb}${play}</a>`}
    <div class="rp-body">
      <div class="rp-head">
        <span class="rp-author">u/${escapeHtml(p.author || "deleted")}</span>
        <time class="rp-time" datetime="${new Date(p.created).toISOString()}" title="${fullDate.format(p.created)}">${ago(p.created)}</time>
        <span class="rp-badges">${badges}</span>
      </div>
      <h3 class="rp-title"><a href="${link(p)}" target="_blank" rel="noopener"></a></h3>
      <p class="rp-excerpt" hidden></p>
      <div class="rp-stats">${stats(p)}</div>
      <div class="msg-actions">
        ${VIEW[p.kind] ? `<button class="act act-view" type="button" data-view>${icons.view}${VIEW[p.kind]}</button>` : ""}
        <a class="act" href="${link(p)}" target="_blank" rel="noopener">${icons.open}Открыть на Reddit</a>
        <button class="act" type="button" data-copy="post">${icons.copy}Текст для поста</button>
        <button class="act" type="button" data-copy="link">${icons.link}Ссылка</button>
      </div>
    </div>`;
  li.querySelector(".rp-title a").textContent = p.title;
  const ex = li.querySelector(".rp-excerpt");
  ex.hidden = !p.excerpt;
  ex.textContent = p.excerpt || "";
  li.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-view]")) return openViewer(p);
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    if (btn.dataset.copy === "link") copy(link(p), "Ссылка скопирована");
    else copy(postText(p), "Текст для поста скопирован");
  });
  return li;
}

// ── Viewer ─────────────────────────────────────────────
// Pictures and Reddit's own videos open right here; galleries and videos from other sites
// through Reddit's official embed. ← / → step through the posts of the current list.
const VIEW = { image: "Картинка", video: "Смотреть видео", gallery: "Галерея" };
const viewer = { list: [], index: 0, hls: null };
let hlsLoaded;

function loadHls() {
  hlsLoaded ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = HLS;
    s.onload = resolve;
    s.onerror = reject;
    document.head.append(s);
  });
  return hlsLoaded;
}

function openViewer(p) {
  viewer.list = ordered().slice(0, state.shown).filter((q) => VIEW[q.kind]);
  viewer.index = Math.max(0, viewer.list.findIndex((q) => q.id === p.id));
  showInViewer();
  $("#viewer").showModal();
}

function stepViewer(step) {
  const n = viewer.list.length;
  if (n < 2) return;
  viewer.index = (viewer.index + step + n) % n;
  showInViewer();
}

const isRedditVideo = (p) => p.kind === "video" && p.domain === "v.redd.it";

function embed(p) {
  const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  return `<iframe class="rview-embed" title="Пост на Reddit" allow="autoplay; fullscreen; encrypted-media" allowfullscreen
    src="https://embed.reddit.com/r/${state.data?.subreddit || "DeadlockTheGame"}/comments/${p.id}/?embed=true&showmedia=true&showmore=false&theme=${theme}"></iframe>`;
}

function showInViewer() {
  stopVideo();
  const p = viewer.list[viewer.index];
  const many = viewer.list.length > 1;
  const media = p.kind === "image"
    ? `<img class="rview-media" src="${escapeHtml(p.url)}" alt="" referrerpolicy="no-referrer">`
    : isRedditVideo(p)
      ? `<video class="rview-media" controls playsinline${p.thumb ? ` poster="${escapeHtml(p.thumb)}"` : ""}></video>`
      : embed(p);
  const dlg = $("#viewer");
  dlg.innerHTML = `<div class="cmp rview">
    <header class="cmp-head">
      <span class="rp-author">u/${escapeHtml(p.author || "deleted")}</span>
      <span class="rp-time">${ago(p.created)}</span>
      <button class="cmp-close" type="button" aria-label="Закрыть">×</button>
      <strong class="rview-title"></strong>
    </header>
    <div class="rview-stage">
      ${media}
      ${many ? `<button class="rview-nav prev" type="button" data-step="-1" aria-label="Предыдущий пост">‹</button>
      <button class="rview-nav next" type="button" data-step="1" aria-label="Следующий пост">›</button>` : ""}
    </div>
    <footer class="rview-foot">
      ${many ? `<span class="rview-count">${viewer.index + 1} / ${viewer.list.length}</span>` : ""}
      <a class="act" href="${link(p)}" target="_blank" rel="noopener">${icons.open}Открыть на Reddit</a>
      ${p.kind === "image" ? `<a class="act" href="${escapeHtml(p.url)}" target="_blank" rel="noopener">${icons.view}Оригинал</a>` : ""}
    </footer>
  </div>`;
  dlg.querySelector(".rview-title").textContent = p.title;
  const img = dlg.querySelector("img.rview-media");
  if (img && p.thumb) img.addEventListener("error", () => (img.src = p.thumb), { once: true });
  if (isRedditVideo(p)) playVideo(dlg.querySelector("video"), p);
}

// v.redd.it streams HLS with sound (audio is a separate track). hls.js plays it wherever
// MediaSource exists; built-in HLS is only for the iPhone — Chrome claims it too, but fails
// on the separate audio. If the stream fails, Reddit's own player takes over.
async function playVideo(video, p) {
  const src = `${p.url.replace(/\/$/, "")}/HLSPlaylist.m3u8`;
  const fallback = () => {
    if (!video.isConnected) return;
    video.insertAdjacentHTML("afterend", embed(p));
    video.remove();
  };
  const native = () => {
    if (!video.canPlayType("application/vnd.apple.mpegurl")) return fallback();
    video.addEventListener("error", fallback, { once: true });
    video.src = src;
    video.play().catch(() => {});
  };
  if (!window.MediaSource && !window.ManagedMediaSource) return native();
  try {
    await loadHls();
  } catch {
    return native();
  }
  if (!video.isConnected) return; // closed or moved on while hls.js was loading
  if (!window.Hls.isSupported()) return native();
  const hls = (viewer.hls = new window.Hls());
  hls.on(window.Hls.Events.ERROR, (_, data) => {
    if (!data.fatal) return;
    stopVideo();
    fallback();
  });
  hls.on(window.Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
  hls.loadSource(src);
  hls.attachMedia(video);
}

function stopVideo() {
  viewer.hls?.destroy();
  viewer.hls = null;
  $("#viewer video")?.pause();
}

function postText(p) {
  const lines = [`🔥 Reddit r/${state.data?.subreddit || "DeadlockTheGame"} — u/${p.author || "deleted"}:`, "", p.title];
  if (p.excerpt) lines.push("", p.excerpt);
  if (p.url && p.kind !== "text") lines.push("", `📎 ${p.url}`);
  lines.push("", `🔗 ${link(p)}`);
  return lines.join("\n");
}

async function copy(text, note) {
  try {
    await navigator.clipboard.writeText(text);
    toast(note);
  } catch {
    toast("Не удалось скопировать");
  }
}

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 1800);
}

function renderHelp() {
  const api = state.data?.source === "api";
  $("#pulse-help").innerHTML = `
    <p><b>Каждый пост сравнивается только с ровесниками</b> — постами примерно того же возраста (от 0,6 до 1,6 его возраста).
    Поэтому пост, которому 20 минут и у которого 30 голосов, может оказаться выше десятичасового с тремя сотнями: для 20 минут это очень много.</p>
    <ul>
      <li><b>Сила</b> — какую долю ровесников пост обгоняет ${api
        ? "по голосам и комментариям (данные Reddit API)."
        : "по голосам и комментариям. Голоса берутся из места поста в «топе часа», «горячем» и «топе дня» Reddit, комментарии считаются по общей ленте комментариев сабреддита."}</li>
      <li><b>Разгон</b> — насколько сила выросла за последние полчаса.</li>
      <li><b>Новости</b> — патчи, обновления, утечки, ссылки на сайты Valve — получают надбавку.</li>
      <li><b>Свежесть</b>: вес поста падает вдвое каждые 8 часов.</li>
    </ul>
    <p>Пульс = (сила + разгон / 2 + надбавка + 0,6) × свежесть. Во вкладке «Взлетают» — посты моложе 4 часов, заметно обогнавшие ровесников;
    значок «Взлетает» получают сильнейшие из них — те, что обгоняют больше 86% ровесников и продолжают расти.</p>`;
}

// ── Controls & URL state ───────────────────────────────
function syncUrl() {
  const p = new URLSearchParams();
  if (state.tab !== "pulse") p.set("tab", state.tab);
  if (state.topic) p.set("topic", state.topic);
  if (state.q) p.set("q", state.q);
  history.replaceState(null, "", p.size ? `?${p}` : location.pathname);
}

function setTab(tab) {
  state.tab = tab;
  state.shown = PAGE;
  document.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === tab));
  syncUrl();
  renderPosts();
}

function setTopic(topic) {
  state.topic = topic;
  state.shown = PAGE;
  document.querySelectorAll("[data-topic]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.topic === topic));
  syncUrl();
  renderPosts();
}

function wireEvents() {
  const dlg = $("#viewer");
  dlg.addEventListener("click", (e) => {
    if (e.target === dlg || e.target.closest(".cmp-close")) return dlg.close();
    const nav = e.target.closest("[data-step]");
    if (nav) stepViewer(Number(nav.dataset.step));
  });
  dlg.addEventListener("keydown", (e) => {
    if (e.target.closest("video")) return; // arrows seek inside the player
    if (e.key === "ArrowLeft") stepViewer(-1);
    if (e.key === "ArrowRight") stepViewer(1);
  });
  dlg.addEventListener("close", () => {
    stopVideo();
    dlg.innerHTML = "";
  });
  document.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b) setTab(b.dataset.tab);
  });
  $("#topics").addEventListener("click", (e) => {
    const b = e.target.closest("[data-topic]");
    if (b) setTopic(b.dataset.topic);
  });
  let debounce;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.q = e.target.value;
      state.shown = PAGE;
      syncUrl();
      renderPosts();
    }, 200);
  });
  $("#filters").addEventListener("submit", (e) => e.preventDefault());
  $("#more").addEventListener("click", () => {
    state.shown += PAGE;
    renderPosts();
  });
  $("#new-bar").addEventListener("click", () => {
    if (state.pending) apply(state.pending.data);
    window.scrollTo({ top: $(".reddit-feed").offsetTop - 16, behavior: "smooth" });
  });
  $("#help-toggle").addEventListener("click", (e) => {
    const help = $("#pulse-help");
    help.hidden = !help.hidden;
    e.currentTarget.setAttribute("aria-expanded", !help.hidden);
    if (!help.hidden) renderHelp();
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
function showError(err) {
  console.error(err);
  $("#posts").innerHTML = "";
  $("#empty").hidden = false;
  $("#empty").textContent = "Не удалось загрузить посты. Попробуйте обновить страницу.";
}

async function init() {
  initTheme();
  wireEvents();
  const p = new URLSearchParams(location.search);
  if (["rising", "new", "top"].includes(p.get("tab"))) setTab(p.get("tab"));
  if (p.get("topic") in TOPICS) setTopic(p.get("topic"));
  state.q = p.get("q") || "";
  $("#q").value = state.q;
  apply(await fetchData());
  // Refresh while the tab is visible; a hidden tab catches up as soon as it is shown again.
  let last = Date.now();
  const refresh = () => {
    last = Date.now();
    fetchData().then((d) => apply(d, { quiet: true })).catch(console.error);
  };
  setInterval(() => document.visibilityState === "visible" && refresh(), REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - last > REFRESH_MS) refresh();
  });
  // Relative times and the «updated» stamp stay current between refreshes.
  setInterval(() => state.data && renderChrome(state.data, state.posts), 30_000);
}

init().catch(showError);
