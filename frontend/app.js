const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const REFRESH_MS = 30_000;

const state = {
  q: "",
  authors: new Set(),
  channel: "",
  messages: [],
  hasMore: false,
  newest: 0,
  pending: [],
  devs: new Map(),
};

// ── Formatting ─────────────────────────────────────────
const rtf = new Intl.RelativeTimeFormat("ru", { numeric: "auto" });
const fullDate = new Intl.DateTimeFormat("ru", { dateStyle: "long", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function ago(ms) {
  if (!ms) return "нет сообщений";
  const s = (ms - Date.now()) / 1000;
  const abs = Math.abs(s);
  if (abs < 60) return "только что";
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(s / 86400), "day");
  return shortDate.format(ms);
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Minimal Discord markdown: escape first, then format.
function renderMarkdown(text) {
  let html = escapeHtml(text);
  const codes = [];
  html = html.replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  html = html
    .replace(/^&gt; (.*)$/gm, "<blockquote>$1</blockquote>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<u>$1</u>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/~~(.+?)~~/g, "<s>$1</s>")
    .replace(/\|\|(.+?)\|\|/g, '<span class="spoiler">$1</span>')
    .replace(/&lt;a?:(\w+):\d+&gt;/g, ":$1:")
    .replace(/&lt;@!?(\d+)&gt;/g, (_, id) => `<span class="mention">@${escapeHtml(state.devs.get(id)?.name || "user")}</span>`)
    .replace(/&lt;#(\d+)&gt;/g, '<span class="mention">#канал</span>')
    .replace(/\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]'"]/g, (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`)
    .replace(/<\/blockquote>\n/g, "</blockquote>");
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

function hue(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return 100 + (h % 90); // greens to teals
}

function avatar(dev, lg = false) {
  const name = dev?.name || "?";
  if (dev?.avatar) {
    const img = document.createElement("img");
    img.className = "avatar" + (lg ? " lg" : "");
    img.src = dev.avatar;
    img.alt = "";
    img.loading = "lazy";
    return img;
  }
  const span = document.createElement("span");
  span.className = "avatar" + (lg ? " lg" : "");
  span.textContent = name[0].toUpperCase();
  span.style.background = `hsl(${hue(dev?.user_id || name)} 45% 55%)`;
  span.setAttribute("aria-hidden", "true");
  return span;
}

const icons = {
  discord: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20 5.3A17 17 0 0 0 15.8 4l-.5 1a15.7 15.7 0 0 0-6.6 0l-.5-1A17 17 0 0 0 4 5.3 18 18 0 0 0 .9 17.4 17 17 0 0 0 6 20l1.1-1.8a11 11 0 0 1-1.7-.8l.4-.3a12.2 12.2 0 0 0 12.4 0l.4.3-1.7.8L18 20a17 17 0 0 0 5.1-2.6A18 18 0 0 0 20 5.3ZM8.5 14.9c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2Zm7 0c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2Z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

// ── Data ───────────────────────────────────────────────
// Two backends: the Python server (/api/*), or a prebuilt data.json on static
// hosting (GitHub Pages) where the same queries are answered in the browser.
let staticData = null;

async function loadStatic() {
  const res = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) return false;
  staticData = await res.json();
  return true;
}

async function api(path, params = {}) {
  if (staticData) return staticApi(path, params);
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  const res = await fetch(`api/${path}${qs.size ? "?" + qs : ""}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const DAY = 86_400_000;

function staticApi(path, p) {
  const { messages, developers, status } = staticData;
  const now = Date.now();
  switch (path) {
    case "dev-messages": {
      const q = (p.q || "").trim().toLowerCase();
      const authors = new Set((p.authors || "").split(",").filter(Boolean));
      const limit = p.limit || PAGE;
      const rows = messages.filter((m) =>
        (!q || m.text.toLowerCase().includes(q) || (m.thread_name || "").toLowerCase().includes(q)) &&
        (!authors.size || authors.has(m.author_id)) &&
        (!p.channel || m.channel_name === p.channel) &&
        (!p.before || m.time < p.before) &&
        (!p.after || m.time > p.after));
      return { messages: rows.slice(0, limit), hasMore: rows.length > limit };
    }
    case "developers": {
      const devs = developers.map((d) => {
        const own = messages.filter((m) => m.author_id === d.user_id);
        return { ...d, total: own.length, week: own.filter((m) => m.time > now - 7 * DAY).length, last_time: own[0]?.time || null };
      });
      return devs.sort((a, b) => (b.last_time || 0) - (a.last_time || 0) || a.name.localeCompare(b.name));
    }
    case "channels": {
      const counts = {};
      messages.forEach((m) => m.channel_name && (counts[m.channel_name] = (counts[m.channel_name] || 0) + 1));
      return Object.entries(counts).map(([name, total]) => ({ name, total })).sort((a, b) => b.total - a.total);
    }
    case "dev-stats": {
      const week = messages.filter((m) => m.time > now - 7 * DAY);
      const byDev = {};
      week.forEach((m) => (byDev[m.name] = (byDev[m.name] || 0) + 1));
      const [topName, topN] = Object.entries(byDev).sort((a, b) => b[1] - a[1])[0] || [];
      const daily = Array(30).fill(0);
      messages.forEach((m) => {
        const d = Math.floor((now - m.time) / DAY);
        if (d >= 0 && d < 30) daily[29 - d]++;
      });
      return {
        total: messages.length,
        day: messages.filter((m) => m.time > now - DAY).length,
        week: week.length,
        lastTime: messages[0]?.time || null,
        topWeek: topName ? { name: topName, n: topN } : null,
        daily,
      };
    }
    case "status":
      return { ...status, now };
  }
  throw new Error(`unknown path ${path}`);
}

const filterParams = () => ({ q: state.q, authors: [...state.authors].join(","), channel: state.channel });

async function loadFeed() {
  const list = $("#messages");
  list.innerHTML = '<li class="skeleton"></li><li class="skeleton"></li><li class="skeleton"></li>';
  const data = await api("dev-messages", { ...filterParams(), limit: PAGE });
  state.messages = data.messages;
  state.hasMore = data.hasMore;
  state.newest = data.messages[0]?.time || state.newest;
  state.pending = [];
  list.innerHTML = "";
  data.messages.forEach((m) => list.append(renderMessage(m)));
  updateFeedChrome();
}

async function loadMore() {
  const last = state.messages.at(-1);
  if (!last) return;
  const btn = $("#more");
  btn.disabled = true;
  const data = await api("dev-messages", { ...filterParams(), before: last.time, limit: PAGE });
  state.messages.push(...data.messages);
  state.hasMore = data.hasMore;
  data.messages.forEach((m) => $("#messages").append(renderMessage(m)));
  btn.disabled = false;
  updateFeedChrome();
}

async function checkNew() {
  if (!state.newest) return loadFeed();
  const data = await api("dev-messages", { ...filterParams(), after: state.newest, limit: 100 });
  const known = new Set([...state.pending, ...state.messages].map((m) => m.id));
  state.pending = [...data.messages.filter((m) => !known.has(m.id)), ...state.pending];
  const bar = $("#new-bar");
  bar.hidden = !state.pending.length;
  bar.textContent = `Новых сообщений: ${state.pending.length} — показать`;
  document.title = (state.pending.length ? `(${state.pending.length}) ` : "") + "zipline — трекер разработчиков Deadlock";
}

function showPending() {
  const list = $("#messages");
  [...state.pending].reverse().forEach((m) => {
    const el = renderMessage(m);
    el.classList.add("fresh");
    list.prepend(el);
  });
  state.messages.unshift(...state.pending);
  state.newest = state.messages[0]?.time || state.newest;
  state.pending = [];
  $("#new-bar").hidden = true;
  document.title = "zipline — трекер разработчиков Deadlock";
}

// ── Rendering ──────────────────────────────────────────
function renderMessage(m) {
  const dev = state.devs.get(m.author_id) || { user_id: m.author_id, name: m.name, avatar: m.avatar };
  const li = document.createElement("li");
  li.className = "msg";
  li.append(avatar(dev, true));

  const body = document.createElement("div");
  const where = m.channel_name
    ? `#${escapeHtml(m.channel_name)}${m.thread_name ? ` <span class="thread">› ${escapeHtml(m.thread_name)}</span>` : ""}`
    : "";
  body.innerHTML = `
    <div class="msg-head">
      <span class="msg-author">${escapeHtml(dev.name || "Неизвестно")}</span>
      <span class="msg-where">${where}</span>
      ${m.edited_time ? '<span class="msg-edited">(изменено)</span>' : ""}
      <time class="msg-time" datetime="${new Date(m.time).toISOString()}" title="${fullDate.format(m.time)}">${ago(m.time)}</time>
    </div>
    <div class="msg-text">${renderMarkdown(m.text || "")}</div>`;

  for (const a of m.attachments) {
    if (a.type?.startsWith("image/")) {
      const img = document.createElement("img");
      img.className = "msg-media";
      img.src = a.url;
      img.alt = a.name || "вложение";
      img.loading = "lazy";
      body.append(img);
    } else if (a.type?.startsWith("video/")) {
      const v = document.createElement("video");
      v.className = "msg-media";
      v.src = a.url;
      v.controls = true;
      v.preload = "metadata";
      body.append(v);
    } else {
      const f = document.createElement("a");
      f.className = "msg-file";
      f.href = a.url;
      f.target = "_blank";
      f.rel = "noopener";
      f.innerHTML = `${icons.link}<span></span>`;
      f.querySelector("span").textContent = a.name || "файл";
      body.append(f);
    }
  }
  for (const e of m.embeds) {
    const box = document.createElement("div");
    box.className = "msg-embed";
    const title = e.title ? (e.url ? `<a href="${escapeHtml(e.url)}" target="_blank" rel="noopener"><strong>${escapeHtml(e.title)}</strong></a>` : `<strong>${escapeHtml(e.title)}</strong>`) : "";
    box.innerHTML = `${title}${e.description ? `<p>${renderMarkdown(e.description)}</p>` : ""}`;
    if (e.image) {
      const img = document.createElement("img");
      img.className = "msg-media";
      img.src = e.image;
      img.alt = "";
      img.loading = "lazy";
      box.append(img);
    }
    body.append(box);
  }

  const actions = document.createElement("div");
  actions.className = "msg-actions";
  actions.innerHTML = `
    <a class="act" ${m.url ? `href="${escapeHtml(m.url)}" target="_blank" rel="noopener"` : 'aria-disabled="true"'}>${icons.discord}Открыть в Discord</a>
    <button class="act" data-copy="post">${icons.copy}Текст для поста</button>
    <button class="act" data-copy="link" ${m.url ? "" : "disabled"}>${icons.link}Ссылка</button>`;
  actions.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    if (btn.dataset.copy === "link") copy(m.url, "Ссылка скопирована");
    else copy(postText(m, dev), "Текст для поста скопирован");
  });
  body.append(actions);
  li.append(body);
  return li;
}

function postText(m, dev) {
  const where = m.thread_name ? `#${m.channel_name} › ${m.thread_name}` : `#${m.channel_name}`;
  const lines = [`💬 ${dev.name} в Discord Deadlock (${where}):`, "", m.text.trim()];
  if (m.url) lines.push("", `🔗 ${m.url}`);
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

let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 1800);
}

function updateFeedChrome() {
  $("#more").hidden = !state.hasMore;
  $("#empty").hidden = state.messages.length > 0;
  const chips = $("#active-filters");
  chips.innerHTML = "";
  for (const id of state.authors) {
    chips.append(chip(state.devs.get(id)?.name || id, () => toggleAuthor(id)));
  }
  if (state.channel) chips.append(chip(`#${state.channel}`, () => setChannel("")));
  $("#clear-devs").hidden = !state.authors.size;
}

function chip(label, onRemove) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "chip";
  b.textContent = label;
  b.title = "Убрать фильтр";
  b.addEventListener("click", onRemove);
  return b;
}

function renderDevs(devs) {
  const list = $("#dev-list");
  list.innerHTML = "";
  for (const d of devs) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.className = "dev-btn" + (d.total ? "" : " quiet");
    b.setAttribute("aria-pressed", state.authors.has(d.user_id));
    b.append(avatar(d));
    b.insertAdjacentHTML("beforeend", `
      <span class="dev-meta"><span class="dev-name"></span><span class="dev-when">${ago(d.last_time)}</span></span>
      <span class="count" title="Сообщений за 7 дней">${d.week || ""}</span>`);
    b.querySelector(".dev-name").textContent = d.name;
    b.addEventListener("click", () => toggleAuthor(d.user_id));
    li.append(b);
    list.append(li);
  }
}

function renderChannels(channels) {
  const list = $("#channel-list");
  list.innerHTML = "";
  for (const c of channels) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.className = "channel-btn";
    b.setAttribute("aria-pressed", state.channel === c.name);
    b.innerHTML = `<span class="hash">#</span><span class="name"></span><span class="count">${c.total}</span>`;
    b.querySelector(".name").textContent = c.name;
    b.addEventListener("click", () => setChannel(state.channel === c.name ? "" : c.name));
    li.append(b);
    list.append(li);
  }
}

function renderStats(s) {
  $("#stat-day").textContent = s.day;
  $("#stat-week").textContent = s.week;
  $("#stat-top").textContent = s.topWeek ? `${s.topWeek.name} · ${s.topWeek.n}` : "—";
  $("#stat-last").textContent = s.lastTime ? ago(s.lastTime) : "—";
  const max = Math.max(1, ...s.daily);
  $("#spark").innerHTML = s.daily
    .map((n, i) => `<i class="${n ? "on" : ""}" style="height:${Math.max(6, (n / max) * 100)}%" title="${29 - i === 0 ? "сегодня" : `${29 - i} дн. назад`}: ${n}"></i>`)
    .join("");
}

function renderStatus(st) {
  $("#demo-banner").hidden = !st.demo;
  const el = $("#collector-status");
  el.className = "collector-status";
  if (!st.collector) {
    el.textContent = st.ingest ? "Сборщик: ожидаю данные через /api/ingest" : "Сборщик Discord не подключён";
  } else if (st.lastPoll?.ok) {
    el.classList.add("ok");
    el.textContent = st.pollSeconds
      ? `Discord: последний опрос ${ago(st.lastPoll.at)}, интервал ${st.pollSeconds} с`
      : `Discord: последний опрос ${ago(st.lastPoll.at)}`;
  } else if (st.lastPoll) {
    el.classList.add("err");
    el.textContent = `Discord: ошибка опроса — ${st.lastPoll.error}`;
  } else {
    el.textContent = "Discord: первый опрос…";
  }
}

// ── Filters & URL state ────────────────────────────────
function syncUrl() {
  const p = new URLSearchParams(filterParams());
  for (const [k, v] of [...p]) if (!v) p.delete(k);
  history.replaceState(null, "", p.size ? `?${p}` : location.pathname);
}

function refreshSidebarPressed() {
  document.querySelectorAll(".dev-btn").forEach((b, i) => b.setAttribute("aria-pressed", state.authors.has(devOrder[i])));
  document.querySelectorAll(".channel-btn").forEach((b) => b.setAttribute("aria-pressed", b.querySelector(".name").textContent === state.channel));
}

function applyFilters() {
  syncUrl();
  refreshSidebarPressed();
  $("#new-bar").hidden = true;
  loadFeed().catch(showError);
}

function toggleAuthor(id) {
  state.authors.has(id) ? state.authors.delete(id) : state.authors.add(id);
  applyFilters();
}

function setChannel(name) {
  state.channel = name;
  applyFilters();
}

function showError(err) {
  console.error(err);
  $("#messages").innerHTML = "";
  $("#empty").hidden = false;
  $("#empty").textContent = "Не удалось загрузить сообщения. Сервер доступен?";
}

let devOrder = [];
async function refreshSide() {
  if (staticData) await loadStatic();
  const [devs, channels, stats, status] = await Promise.all([api("developers"), api("channels"), api("dev-stats"), api("status")]);
  state.devs = new Map(devs.map((d) => [d.user_id, d]));
  devOrder = devs.map((d) => d.user_id);
  renderDevs(devs);
  renderChannels(channels);
  renderStats(stats);
  renderStatus(status);
}

// ── Boot ───────────────────────────────────────────────
function init() {
  const p = new URLSearchParams(location.search);
  state.q = p.get("q") || "";
  state.channel = p.get("channel") || "";
  (p.get("authors") || "").split(",").filter(Boolean).forEach((a) => state.authors.add(a));
  $("#q").value = state.q;

  let debounce;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.q = e.target.value;
      applyFilters();
    }, 250);
  });
  $("#filters").addEventListener("submit", (e) => e.preventDefault());
  $("#more").addEventListener("click", () => loadMore().catch(showError));
  $("#new-bar").addEventListener("click", showPending);
  $("#clear-devs").addEventListener("click", () => {
    state.authors.clear();
    applyFilters();
  });

  loadStatic()
    .catch(() => false)
    .then(refreshSide)
    .then(() => loadFeed())
    .catch(showError);

  setInterval(() => {
    refreshSide().then(checkNew).catch(console.error);
  }, REFRESH_MS);
}

init();
