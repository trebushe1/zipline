const $ = (sel) => document.querySelector(sel);
const SOURCE = "https://github.com/SteamDatabase/GameTracking-Deadlock";
const STRINGS_STEP = 60;
const MODEL_VIEWER = "https://cdn.jsdelivr.net/npm/@google/model-viewer@3.5.0/dist/model-viewer.min.js";

const state = { index: null, build: null, file: "", q: "", showEmpty: false };

// ── Formatting ─────────────────────────────────────────
const fullDate = new Intl.DateTimeFormat("ru", { dateStyle: "long", timeStyle: "short" });
const shortDate = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const num = new Intl.NumberFormat("ru");

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function plural(n, one, few, many) {
  const n10 = n % 10, n100 = n % 100;
  const word = n10 === 1 && n100 !== 11 ? one : n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14) ? few : many;
  return `${num.format(n)} ${word}`;
}

function bytes(n) {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} КБ`;
  return `${(n / 1024 ** 2).toFixed(1)} МБ`;
}

const SIZE = {
  major: "Крупный патч",
  update: "Обновление",
  hotfix: "Небольшая правка",
  empty: "Без изменений файлов",
};
const STATUS = { a: ["+", "новый"], c: ["~", "изменён"], r: ["−", "удалён"] };

// Game strings carry <br> and simple markup; show line breaks, escape the rest.
const gameText = (s) => escapeHtml(s).replace(/&lt;br\s*\/?&gt;/gi, "<br>");

// Word-level diff for changed strings: deleted words struck, inserted words marked.
function wordDiff(a, b) {
  const split = (s) => s.split(/(\s+|<br\s*\/?>)/i).filter(Boolean);
  const x = split(a), y = split(b);
  if (x.length * y.length > 90_000) return { old: gameText(a), new: gameText(b) };
  const dp = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, o = "", n = "";
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) {
      o += gameText(x[i]); n += gameText(y[j]); i++; j++;
    } else if (j < y.length && (i >= x.length || dp[i][j + 1] >= dp[i + 1][j])) {
      n += `<ins>${gameText(y[j++])}</ins>`;
    } else {
      o += `<del>${gameText(x[i++])}</del>`;
    }
  }
  return { old: o, new: n };
}

// ── Data ───────────────────────────────────────────────
async function getJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

const matches = (...parts) => !state.q || parts.some((p) => p && String(p).toLowerCase().includes(state.q));

// ── Build list ─────────────────────────────────────────
function renderBuildList() {
  const list = $("#build-list");
  const builds = state.index.builds.filter((b) => state.showEmpty || b.size !== "empty" || b.file === state.file);
  list.innerHTML = builds.map((b) => `
    <li>
      <button class="build-btn size-${b.size}" data-file="${b.file}" aria-pressed="${b.file === state.file}">
        <span class="build-row">
          <span class="build-no">${b.build}</span>
          <span class="build-size">${SIZE[b.size]}</span>
          ${b.media ? `<span class="build-media" title="Есть модели и текстуры до/после">3D</span>` : ""}
          <span class="build-when">${shortDate.format(b.time)}</span>
        </span>
        <span class="build-line">${escapeHtml(b.headline[0] || "")}</span>
      </button>
    </li>`).join("");
}

// ── Build page ─────────────────────────────────────────
function renderBuild() {
  const b = state.build;
  const c = b.counts;
  const visual = b.subjects.filter((s) => s.visual);
  const rest = b.subjects.filter((s) => !s.visual);
  $("#build").innerHTML = `
    <div class="panel build-head">
      <div class="build-title">
        <span class="tag">Билд ${b.build}</span>
        <span class="size-badge size-${b.size}">${SIZE[b.size]}</span>
        ${b.newHeroes.map((h) => `<span class="size-badge hero-badge">Новый герой: ${escapeHtml(h)}</span>`).join("")}
      </div>
      <p class="build-meta">
        ${fullDate.format(b.time)}${b.versionDate ? ` · сборка Valve от ${escapeHtml(b.versionDate)}` : ""}
        · <a href="${SOURCE}/commit/${b.sha}" target="_blank" rel="noopener">полный diff на GitHub</a>
        · <a href="https://steamdb.info/app/1422450/patchnotes/" target="_blank" rel="noopener">SteamDB</a>
      </p>
      <ul class="headline">${b.headline.map((h) => `<li>${escapeHtml(h)}</li>`).join("")}</ul>
    </div>

    <section class="stats build-stats" aria-label="Сводка билда">
      <div class="stat"><span class="stat-label">Новых файлов</span><span class="stat-value">${num.format(c.added)}</span></div>
      <div class="stat"><span class="stat-label">Изменено</span><span class="stat-value">${num.format(c.changed)}</span></div>
      <div class="stat"><span class="stat-label">Удалено</span><span class="stat-value">${num.format(c.removed)}</span></div>
      <div class="stat"><span class="stat-label">Строк текста</span><span class="stat-value">${num.format(c.strings)}</span></div>
      <div class="stat"><span class="stat-label">Параметров</span><span class="stat-value">${num.format(c.vdata)}</span></div>
    </section>

    <form class="panel build-tools" id="build-tools" role="search">
      <label class="search">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="2"/><path d="M16.5 16.5 L21 21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        <input type="search" id="q" placeholder="Поиск: герой, файл, строка, параметр…" autocomplete="off" value="${escapeHtml(state.q)}">
      </label>
      <nav class="jump" aria-label="Разделы билда">
        ${b.media ? `<a href="#sec-media">Было → стало <b>${Object.keys(b.media).length}</b></a>` : ""}
        ${visual.length ? `<a href="#sec-visual">Внешний вид <b>${visual.length}</b></a>` : ""}
        ${b.stringsTotal ? `<a href="#sec-strings">Тексты <b>${num.format(b.stringsTotal)}</b></a>` : ""}
        ${b.vdataTotal ? `<a href="#sec-params">Параметры <b>${num.format(b.vdataTotal)}</b></a>` : ""}
        ${rest.length ? `<a href="#sec-other">Остальное <b>${rest.length}</b></a>` : ""}
      </nav>
    </form>

    <div id="build-body"></div>`;

  $("#build-tools").addEventListener("submit", (e) => e.preventDefault());
  let debounce;
  $("#q").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.q = e.target.value.trim().toLowerCase();
      renderBody();
    }, 200);
  });
  renderBody();
}

function renderBody() {
  const b = state.build;
  const subjects = b.subjects.filter((s) => matches(s.title, s.id, s.desc) || s.groups.some((g) => g.files.some((f) => matches(f.p))));
  const visual = subjects.filter((s) => s.visual);
  const rest = subjects.filter((s) => !s.visual);
  const strings = b.strings.filter((r) => matches(r.key, r.old, r.new, r.file));
  const params = b.vdata.filter((e) => matches(e.key, e.name, e.hero) || e.rows.some((r) => matches(r.label, r.ctx, r.old, r.new)));

  const media = Object.entries(b.media || {}).filter(([p, m]) => matches(p, m.subject));

  const sections = [];
  if (media.length) sections.push(section("sec-media", "Было → стало", "Изменённые модели и текстуры прямо из файлов игры. Нажмите на карточку: 3D-модели можно крутить мышью, «до» и «после» поворачиваются вместе.",
    `<div class="gallery">${media.map(([p, m]) => mediaTile(p, m)).join("")}</div>`));
  if (visual.length) sections.push(section("sec-visual", "Внешний вид", "Модели, текстуры, материалы и эффекты — что можно увидеть в игре. Нажмите на путь файла, чтобы скопировать его для Source 2 Viewer.",
    `<div class="subjects">${visual.map((s) => subjectCard(s)).join("")}</div>`));
  if (strings.length) sections.push(section("sec-strings", "Тексты игры", "Новые и изменённые строки английской локализации: названия, описания способностей, подсказки. Часто здесь первыми появляются будущие герои и предметы.",
    stringsBlock(strings, b.stringsTotal)));
  if (params.length) sections.push(section("sec-params", "Параметры", "Значения из игровых скриптов: урон, перезарядки, здоровье и прочие цифры — было → стало.",
    paramsBlock(params, b.vdataTotal)));
  if (rest.length) sections.push(section("sec-other", "Остальное", "Звуки, интерфейс, скрипты и служебные файлы.",
    `<div class="subjects compact">${rest.map((s) => subjectCard(s)).join("")}</div>`));

  $("#build-body").innerHTML = sections.join("") || `<div class="panel"><p class="empty pad">${
    state.q ? "Ничего не найдено. Попробуйте другой запрос." : b.counts.code
      ? "В этом билде изменился только код игры (бинарники) — внешних изменений в файлах нет."
      : "В этом билде нет изменений в файлах игры: обычно это перезаливка или правка на стороне сервера."}</p></div>`;
}

function section(id, title, lede, body) {
  return `<section class="build-section" id="${id}">
    <header class="section-head"><h2>${title}</h2><p>${lede}</p></header>
    ${body}
  </section>`;
}

// ── Subjects (heroes and areas) ───────────────────────
function subjectCard(s) {
  const kinds = s.groups.map((g) => {
    const parts = [["+", g.counts.added, "a"], ["~", g.counts.changed, "c"], ["−", g.counts.removed, "r"]]
      .filter(([, n]) => n).map(([sign, n, cls]) => `<i class="st-${cls}">${sign}${num.format(n)}</i>`).join(" ");
    return `<span class="kind kind-${g.kind}">${g.label} ${parts}</span>`;
  }).join("");
  return `<article class="panel subject subject-${s.type}" data-subject="${escapeHtml(s.type + ":" + s.id)}">
    <header class="subject-head">
      <h3 class="subject-title ${s.type === "hero" ? "is-hero" : ""}">${escapeHtml(s.title)}</h3>
      <span class="subject-desc">${escapeHtml(s.desc)}</span>
      ${s.tags.map((t) => `<span class="size-badge hero-badge">${escapeHtml(t)}</span>`).join("")}
    </header>
    ${s.notes.length ? `<ul class="notes">${s.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
    <div class="kinds">${kinds}</div>
    <details class="files" data-lazy>
      <summary>Файлы · ${plural(s.total, "файл", "файла", "файлов")}</summary>
      <div class="files-body"></div>
    </details>
  </article>`;
}

function fileList(s) {
  return s.groups.map((g) => {
    const total = g.counts.added + g.counts.changed + g.counts.removed;
    const files = g.files.filter((f) => matches(f.p) || !state.q);
    return `<div class="file-group">
      <h4>${g.label}</h4>
      <ul>${files.map((f) => {
        const cut = f.p.lastIndexOf("/") + 1;
        return `<li class="file st-${f.s}">
          <span class="file-mark" title="${STATUS[f.s][1]}">${STATUS[f.s][0]}</span>
          <button class="file-path" data-copy="${escapeHtml(f.p)}" title="Скопировать путь"><span class="dir">${escapeHtml(f.p.slice(0, cut))}</span>${escapeHtml(f.p.slice(cut))}</button>
          ${state.build.media?.[f.p] ? `<button class="file-view" data-media="${escapeHtml(f.p)}">смотреть</button>` : ""}
          <span class="file-size">${bytes(f.size)}</span>
        </li>`;
      }).join("")}</ul>
      ${g.files.length < total ? `<p class="more-note">Показаны первые ${g.files.length} из ${num.format(total)} — остальные в <a href="${SOURCE}/commit/${state.build.sha}" target="_blank" rel="noopener">полном diff</a>.</p>` : ""}
    </div>`;
  }).join("");
}

// ── Before / after media ───────────────────────────────
const mediaUrl = (name) => `media/${state.build.mediaTag}/${name}`;

function mediaTile(path, m) {
  const name = path.slice(path.lastIndexOf("/") + 1).replace(/\.\w+_c$/, "").replaceAll("_", " ");
  const label = m.status === "a" ? "новое" : m.before ? "до / после" : "после";
  const thumb = m.kind === "texture"
    ? `<img src="${mediaUrl(m.after || m.before)}" alt="" loading="lazy">`
    : `<span class="tile-3d" aria-hidden="true">3D</span>`;
  return `<button class="tile tile-${m.kind}" data-media="${escapeHtml(path)}" title="${escapeHtml(path)}">
    <span class="tile-thumb">${thumb}</span>
    <span class="tile-name">${escapeHtml(name)}</span>
    <span class="tile-meta"><span class="str-badge ${m.status === "a" ? "new" : ""}">${label}</span> ${escapeHtml(m.subject)}</span>
  </button>`;
}

let viewerLoaded;
function loadModelViewer() {
  viewerLoaded ||= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.type = "module";
    s.src = MODEL_VIEWER;
    s.onload = resolve;
    s.onerror = reject;
    document.head.append(s);
  });
  return viewerLoaded;
}

function openMedia(path) {
  const m = state.build.media[path];
  const sides = [["before", "До"], ["after", m.status === "a" ? "Новое" : "После"]].filter(([k]) => m[k]);
  const pane = ([key, title]) => `<figure class="cmp-pane">
    <figcaption>${title}</figcaption>
    ${m.kind === "model"
      ? `<model-viewer src="${mediaUrl(m[key])}" camera-controls auto-rotate shadow-intensity="1" exposure="1.1" interaction-prompt="none"></model-viewer>`
      : `<img src="${mediaUrl(m[key])}" alt="${title}">`}
  </figure>`;
  const dlg = $("#media-dialog");
  dlg.innerHTML = `<div class="cmp">
    <header class="cmp-head">
      <strong>${escapeHtml(path.slice(path.lastIndexOf("/") + 1))}</strong>
      <button class="link-btn" data-copy="${escapeHtml(path)}">${escapeHtml(path)}</button>
      <button class="cmp-close" aria-label="Закрыть">×</button>
    </header>
    <div class="cmp-panes cmp-${sides.length}">${sides.map(pane).join("")}</div>
    ${m.kind === "model" ? `<p class="more-note">Мышь — вращать, колесо — приблизить. Материалы упрощены: без шейдеров игры.</p>` : ""}
  </div>`;
  dlg.showModal();
  if (m.kind === "model") loadModelViewer().then(() => syncCameras(dlg)).catch(() => toast("Не удалось загрузить 3D-просмотрщик"));
}

// Turning one model turns the other, so both stay comparable.
function syncCameras(root) {
  const views = [...root.querySelectorAll("model-viewer")];
  for (const v of views) {
    v.addEventListener("camera-change", (e) => {
      if (e.detail.source !== "user-interaction") return;
      for (const other of views) {
        if (other === v) continue;
        other.autoRotate = false;
        other.cameraOrbit = v.getCameraOrbit().toString();
        other.fieldOfView = v.getFieldOfView() + "deg";
      }
      v.autoRotate = false;
    });
  }
}

// ── Game strings ───────────────────────────────────────
const LOC_FILES = {
  citadel_main: "Интерфейс", citadel_heroes: "Герои и способности", citadel_mods: "Предметы",
  citadel_gc: "Меню и магазин", citadel_gc_hero_names: "Имена героев", citadel_gc_mod_names: "Названия предметов",
  citadel_attributes: "Характеристики", citadel_patch_notes: "Патчноуты в игре", citadel_generated_vo: "Реплики героев (субтитры)",
};

function stringsBlock(rows, total) {
  const byFile = new Map();
  for (const r of rows) {
    if (!byFile.has(r.file)) byFile.set(r.file, []);
    byFile.get(r.file).push(r);
  }
  const groups = [...byFile].map(([file, list]) => {
    const vo = file === "citadel_generated_vo";
    return `<details class="panel strings-group" ${vo && !state.q ? "" : "open"} data-file="${file}">
      <summary><span class="group-title">${LOC_FILES[file] || file}</span><span class="group-count">${plural(list.length, "строка", "строки", "строк")}</span></summary>
      <ol class="strings" data-shown="${STRINGS_STEP}">${list.slice(0, STRINGS_STEP).map(stringRow).join("")}</ol>
      ${list.length > STRINGS_STEP ? `<div class="feed-foot"><button class="btn more-strings">Показать ещё</button></div>` : ""}
    </details>`;
  }).join("");
  const cut = total > state.build.strings.length
    ? `<p class="more-note">В билде ${plural(total, "строка", "строки", "строк")}; здесь первые ${state.build.strings.length}. Все — в <a href="${SOURCE}/commit/${state.build.sha}" target="_blank" rel="noopener">полном diff</a>.</p>` : "";
  return groups + cut;
}

function stringRow(r) {
  let body;
  if (r.s === "changed") {
    const d = wordDiff(r.old, r.new);
    body = `<div class="str-old">${d.old}</div><div class="str-new">${d.new}</div>`;
  } else if (r.s === "added") {
    body = `<div class="str-new">${gameText(r.new)}</div>`;
  } else {
    body = `<div class="str-old"><del>${gameText(r.old)}</del></div>`;
  }
  const label = { added: "новая", changed: "изменена", removed: "удалена" }[r.s];
  return `<li class="str str-${r.s}">
    <div class="str-key"><span class="str-badge">${label}</span><code>${escapeHtml(r.key)}</code></div>
    ${body}
  </li>`;
}

// ── Script values ──────────────────────────────────────
function paramsBlock(entities, total) {
  const byHero = new Map();
  for (const e of entities) {
    const k = e.hero || "";
    if (!byHero.has(k)) byHero.set(k, []);
    byHero.get(k).push(e);
  }
  const keys = [...byHero.keys()].sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
  const out = keys.map((hero) => `
    <div class="param-hero">
      <h3 class="param-hero-title">${hero ? `<span class="is-hero">${escapeHtml(hero)}</span>` : "Общее: предметы, юниты, мир"}</h3>
      <div class="params">${byHero.get(hero).map(paramCard).join("")}</div>
    </div>`).join("");
  const cut = total > state.build.vdata.length
    ? `<p class="more-note">Показаны ${state.build.vdata.length} записей из ${num.format(total)}.</p>` : "";
  return out + cut;
}

function paramCard(e) {
  const badge = e.added ? `<span class="str-badge new">новое</span>` : e.removed ? `<span class="str-badge gone">удалено</span>` : "";
  const rows = `<table class="param-rows"><tbody>${e.rows.map(paramRow).join("")}</tbody></table>
    ${e.total > e.rows.length ? `<p class="more-note">…и ещё ${e.total - e.rows.length}</p>` : ""}`;
  return `<article class="panel param">
    <header class="param-head">
      <strong>${escapeHtml(e.name || e.key)}</strong> ${badge}
      <code>${escapeHtml(e.file)} › ${escapeHtml(e.key)}</code>
    </header>
    ${e.added || e.removed ? `<details><summary>${plural(e.total, "параметр", "параметра", "параметров")}</summary>${rows}</details>` : rows}
  </article>`;
}

function paramRow(r) {
  const o = r.old, n = r.new;
  let change;
  if (o != null && n != null) {
    const a = parseFloat(o), b = parseFloat(n);
    const numeric = !isNaN(a) && !isNaN(b) && /^-?[\d.]+$/.test(o) && /^-?[\d.]+$/.test(n);
    const dir = numeric ? (b > a ? `<span class="delta up">▲</span>` : b < a ? `<span class="delta down">▼</span>` : "") : "";
    change = `<span class="v-old">${escapeHtml(o) || "∅"}</span><span class="arrow">→</span><span class="v-new">${escapeHtml(n) || "∅"}</span>${dir}`;
  } else if (n != null) {
    change = `<span class="v-new">${escapeHtml(n) || "∅"}</span>`;
  } else {
    change = `<span class="v-old"><del>${escapeHtml(o) || "∅"}</del></span>`;
  }
  return `<tr><th>${escapeHtml(r.label)}${r.ctx ? `<small>${escapeHtml(r.ctx)}</small>` : ""}</th><td>${change}</td></tr>`;
}

// ── Interactions ───────────────────────────────────────
let toastTimer;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2200);
}

async function selectBuild(file, push = true) {
  state.file = file;
  renderBuildList();
  $("#build").innerHTML = `<div class="panel"><div class="skeleton" style="margin:18px"></div></div>`;
  state.build = await getJson(`patches/${file}`);
  if (push) history.replaceState(null, "", `?build=${file.replace(/\.json$/, "")}`);
  renderBuild();
}

function wireEvents() {
  $("#build-list").addEventListener("click", (e) => {
    const btn = e.target.closest(".build-btn");
    if (!btn) return;
    selectBuild(btn.dataset.file).catch(showError);
    if (matchMedia("(max-width: 960px)").matches) $("#build").scrollIntoView({ behavior: "smooth" });
  });
  const dlg = $("#media-dialog");
  dlg.addEventListener("click", async (e) => {
    if (e.target === dlg || e.target.closest(".cmp-close")) return dlg.close();
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        toast("Путь скопирован");
      } catch {
        toast("Не удалось скопировать");
      }
    }
  });
  dlg.addEventListener("close", () => (dlg.innerHTML = ""));
  $("#show-empty").addEventListener("change", (e) => {
    state.showEmpty = e.target.checked;
    renderBuildList();
  });
  // File lists are built on first open: big patches have thousands of paths.
  $("#build").addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d.matches?.("details.files") || !d.open) return;
    const [type, ...id] = d.closest(".subject").dataset.subject.split(":");
    const s = state.build.subjects.find((x) => x.type === type && x.id === id.join(":"));
    d.querySelector(".files-body").innerHTML = fileList(s);
  }, true);
  $("#build").addEventListener("click", async (e) => {
    const view = e.target.closest("[data-media]");
    if (view) {
      openMedia(view.dataset.media);
      return;
    }
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        toast("Путь скопирован");
      } catch {
        toast("Не удалось скопировать");
      }
      return;
    }
    const more = e.target.closest(".more-strings");
    if (more) {
      const group = more.closest(".strings-group");
      const list = group.querySelector(".strings");
      const rows = state.build.strings.filter((r) => r.file === group.dataset.file && matches(r.key, r.old, r.new, r.file));
      const shown = Number(list.dataset.shown);
      list.insertAdjacentHTML("beforeend", rows.slice(shown, shown + STRINGS_STEP).map(stringRow).join(""));
      list.dataset.shown = shown + STRINGS_STEP;
      if (shown + STRINGS_STEP >= rows.length) more.parentElement.remove();
    }
  });
}

function showError(err) {
  console.error(err);
  $("#build").innerHTML = `<div class="panel"><p class="empty pad">Не удалось загрузить данные о патчах. Обновите страницу чуть позже.</p></div>`;
}

// ── Theme ──────────────────────────────────────────────
// Same switch as on the main page; the inline script in <head> already set data-theme.
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
  state.index = await getJson("patches/index.json");
  const builds = state.index.builds;
  $("#source-status").textContent = `Данные: SteamDB GameTracking · проверено ${shortDate.format(state.index.updatedAt)}`;
  $("#source-status").classList.add("ok");
  if (!builds.length) throw new Error("no builds");
  const wanted = new URLSearchParams(location.search).get("build");
  // By default open the newest build that actually changed game files.
  const first = builds.find((b) => wanted && b.file === `${wanted}.json`)
    || builds.find((b) => b.size !== "empty") || builds[0];
  await selectBuild(first.file, Boolean(wanted));
}

init().catch(showError);
