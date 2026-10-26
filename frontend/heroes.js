const $ = (sel) => document.querySelector(sel);
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const MAX_EXPORT = 8192; // longest side of a downloaded PNG, before trimming

const state = { index: null, heroes: [], hero: null, q: "" };

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

// ── Roster ─────────────────────────────────────────────
function renderRoster() {
  const q = state.q.trim().toLowerCase();
  const heroes = state.heroes.filter((h) => !q || h.name.toLowerCase().includes(q) || h.key.includes(q));
  $("#roster").innerHTML = heroes.length ? heroes.map((h) => `
    <li>
      <button class="hero-card" type="button" data-key="${h.key}" ${h.file ? "" : 'aria-disabled="true"'}>
        <span class="hero-portrait">${h.icon ? `<img src="heroes/icons/${h.icon}" alt="" loading="lazy" decoding="async">` : `<i>${escapeHtml(h.name[0])}</i>`}</span>
        <span class="hero-name">${escapeHtml(h.name)}</span>
        ${h.file ? "" : '<span class="hero-soon">модель готовится</span>'}
      </button>
    </li>`).join("") : `<li class="empty pad">Никого не нашлось.</li>`;
}

// ── Animation names ────────────────────────────────────
// Clip names are the file names from the game: ability_charge_inair → «ability charge inair».
const GROUPS = [
  ["Способности", /^(ability|ult|abil|cast|spell)/],
  ["Стойка и выбор героя", /^(ui_|idle|hero_?select|pick|intro|lobby|pose|showcase)|_idle$|stand/],
  ["Эмоции", /(emote|taunt|gesture|dance|celebrat|wave|laugh|cheer|victory|gloat)/],
  ["Ближний бой", /(melee|punch|parry|heavy|light_|slash|bash)/],
  ["Прыжки и полёт", /(jump|land|fall|air|fly|glide|hover|double)/],
  ["Движение", /(run|walk|sprint|jog|move|loco|strafe|turn|crouch|slide|dash|mantle|zipline|step)/],
  ["Стрельба и оружие", /(shoot|fire|attack|reload|weapon|gun|primary|secondary|alt_fire)/],
  ["Урон и смерть", /(death|die|dead|flinch|hit|stun|knock|ragdoll|pain|ko)/],
];

const prettyAnim = (name) => name.replace(/_/g, " ").replace(/\s+/g, " ").trim();
const groupOf = (name) => (GROUPS.find(([, re]) => re.test(name.toLowerCase())) || ["Прочее"])[0];

// The first pose you see: the hero select stance if there is one, else a plain idle.
function defaultClip(clips) {
  const names = clips.map((c) => c.name.toLowerCase());
  const prefer = [/hero_?select|pickscreen|pick_?idle|showcase/, /^ui_pose/, /^ui_/, /stand_idle$/, /^idle$/, /^idle/, /idle/, /stand/];
  for (const re of prefer) {
    const i = names.findIndex((n) => re.test(n) && !/aim|additive|lower|upper/.test(n));
    if (i >= 0) return clips[i];
  }
  return clips[0];
}

// ── Viewer ─────────────────────────────────────────────
const viewer = {
  THREE: null, renderer: null, scene: null, camera: null, controls: null, loader: null,
  model: null, anchor: null, mixer: null, action: null, clips: [], rootBone: null, rootStart: null,
  playing: true, speed: 1, bg: "checker", scale: 2, frameBox: null, token: 0, cache: new Map(),
};

async function initThree() {
  if (viewer.THREE) return;
  const [THREE, { OrbitControls }, { GLTFLoader }, { RoomEnvironment }, { MeshoptDecoder }] = await Promise.all([
    import("three"),
    import("three/addons/controls/OrbitControls.js"),
    import("three/addons/loaders/GLTFLoader.js"),
    import("three/addons/environments/RoomEnvironment.js"),
    import("three/addons/libs/meshopt_decoder.module.js"),
  ]);
  viewer.THREE = THREE;
  const canvas = $("#stage-canvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = 1.05;
  viewer.renderer = renderer;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.75;
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(2, 4, 3);
  const rim = new THREE.DirectionalLight(0xcfe8ff, 1.4);
  rim.position.set(-3, 2.5, -3);
  scene.add(key, rim, new THREE.HemisphereLight(0xffffff, 0x404040, 0.6));
  viewer.scene = scene;

  viewer.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 1000);
  viewer.controls = new OrbitControls(viewer.camera, canvas);
  viewer.controls.enableDamping = true;
  viewer.controls.dampingFactor = 0.08;
  viewer.controls.screenSpacePanning = true;
  viewer.controls.autoRotateSpeed = 1.6;
  viewer.loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  viewer.clock = new THREE.Clock();

  new ResizeObserver(resizeStage).observe($("#stage"));
  resizeStage();
  renderer.setAnimationLoop(tick);
}

function resizeStage() {
  const { renderer, camera } = viewer;
  if (!renderer) return;
  const { clientWidth: w, clientHeight: h } = $("#stage");
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function tick() {
  const dt = Math.min(viewer.clock.getDelta(), 0.1);
  if (viewer.mixer && viewer.playing) viewer.mixer.update(dt * viewer.speed);
  keepInPlace();
  updateProps();
  viewer.controls.update();
  viewer.renderer.render(viewer.scene, viewer.camera);
  if (viewer.action && viewer.playing) syncTimeline();
}

// Movement clips carry root motion: the hero would run off the screen. Pin the root bone over the start point.
function keepInPlace() {
  const { anchor, rootBone, rootStart, THREE } = viewer;
  if (!anchor || !rootBone) return;
  anchor.position.set(0, 0, 0);
  if (!$("#in-place").checked) return;
  anchor.updateMatrixWorld(true);
  const p = rootBone.getWorldPosition(new THREE.Vector3());
  anchor.position.set(rootStart.x - p.x, 0, rootStart.z - p.z);
}

// Weapons and props hang off their own branch of the skeleton (root_motion → weaponPivot → weapon), not
// off the hand. In the game a hand target pins them to the hand; the clips alone often leave them lying at
// the hero's feet. So each prop bone (a child of such a branch) is shown only while it is next to a hand,
// and otherwise shrunk to nothing, together with the vertices that follow it.
function markBranches(model, root) {
  viewer.propBones = [];
  viewer.hands = [];
  if (!root) return;
  const branchOf = (bone) => {
    let b = bone;
    while (b.parent && b.parent !== root && b.parent.isBone) b = b.parent;
    return b.parent === root ? b : root;
  };
  const totals = new Map();
  model.traverse((o) => {
    if (!o.isSkinnedMesh) return;
    const index = o.geometry.attributes.skinIndex, weight = o.geometry.attributes.skinWeight;
    for (let i = 0; i < index.count; i++) {
      let best = 0;
      for (let c = 1; c < 4; c++) if (weight.getComponent(i, c) > weight.getComponent(i, best)) best = c;
      const branch = branchOf(o.skeleton.bones[index.getComponent(i, best)]);
      totals.set(branch, (totals.get(branch) || 0) + 1);
    }
  });
  const body = [...totals].sort((x, y) => y[1] - x[1])[0]?.[0];
  if (!body) return;
  body.traverse((b) => { if (b.isBone && /hand/i.test(b.name) && !/weapon|target|ik/i.test(b.name)) viewer.hands.push(b); });
  if (!viewer.hands.length) return;
  for (const branch of totals.keys()) {
    if (branch !== body && branch !== root) viewer.propBones.push(...branch.children.filter((b) => b.isBone));
  }
}

const isHidden = (bone) => {
  for (let b = bone; b?.isBone; b = b.parent) if (b.userData.hidden) return true;
  return false;
};

function updateProps() {
  const { propBones, hands, THREE } = viewer;
  if (!propBones?.length || !viewer.reach) return;
  viewer.model.updateMatrixWorld(true);
  const p = new THREE.Vector3(), h = new THREE.Vector3();
  for (const bone of propBones) {
    bone.getWorldPosition(p);
    const near = hands.some((hand) => hand.getWorldPosition(h).distanceTo(p) < viewer.reach);
    if (!near && !bone.userData.hidden) {
      bone.userData.hidden = bone.scale.clone();
      bone.scale.setScalar(1e-5);
    } else if (near && bone.userData.hidden) {
      bone.scale.copy(bone.userData.hidden);
      bone.userData.hidden = null;
    }
  }
  viewer.model.updateMatrixWorld(true);
}

// Anything else lying far from the hero in the current pose (Baba's pigeon parks six metres under her):
// the bones whose vertices are all well outside the bulk of the model are shrunk the same way. Checked
// whenever a clip starts, so a prop that the clip brings in stays visible.
function hideStrays() {
  const { THREE, model } = viewer;
  for (const b of viewer.strays || []) {
    if (b.userData.hidden) { b.scale.copy(b.userData.hidden); b.userData.hidden = null; }
  }
  viewer.strays = [];
  model.updateMatrixWorld(true);
  const points = [], v = new THREE.Vector3();
  model.traverse((o) => {
    if (!o.isSkinnedMesh || !o.visible) return;
    o.skeleton.update();
    const index = o.geometry.attributes.skinIndex, weight = o.geometry.attributes.skinWeight;
    for (let i = 0; i < index.count; i += 2) {
      let best = 0;
      for (let c = 1; c < 4; c++) if (weight.getComponent(i, c) > weight.getComponent(i, best)) best = c;
      const bone = o.skeleton.bones[index.getComponent(i, best)];
      if (isHidden(bone)) continue;
      o.getVertexPosition(i, v).applyMatrix4(o.matrixWorld);
      points.push([v.x, v.y, v.z, bone]);
    }
  });
  if (points.length < 100) return;
  // The bulk: 10th to 90th percentile on each axis, with 60% of its size as margin.
  const lo = [], hi = [];
  for (let a = 0; a < 3; a++) {
    const sorted = points.map((p) => p[a]).sort((x, y) => x - y);
    lo.push(sorted[Math.floor(sorted.length * 0.1)]);
    hi.push(sorted[Math.floor(sorted.length * 0.9)]);
  }
  const margin = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) * 0.6;
  const inside = (p) => [0, 1, 2].every((a) => p[a] > lo[a] - margin && p[a] < hi[a] + margin);
  // Per bone subtree: does any of its vertices sit in the bulk?
  const near = new Map();
  for (const p of points) {
    const ok = inside(p);
    for (let b = p[3]; b?.isBone; b = b.parent) {
      const seen = near.get(b);
      if (seen === true) break;
      near.set(b, ok || seen === true);
      if (!ok && seen === false) break;
    }
  }
  for (const [bone, ok] of near) {
    if (ok || near.get(bone.parent) === false) continue; // collapse only the top of a stray subtree
    bone.userData.hidden = bone.scale.clone();
    bone.scale.setScalar(1e-5);
    viewer.strays.push(bone);
  }
  model.updateMatrixWorld(true);
}

// Bounds of the posed model without the hidden props.
function poseBox(root) {
  const { THREE } = viewer;
  const box = new THREE.Box3(), v = new THREE.Vector3();
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    if (!o.isMesh || !o.visible) return;
    if (!o.isSkinnedMesh) { box.union(new THREE.Box3().setFromObject(o, true)); return; }
    o.skeleton.update();
    const index = o.geometry.attributes.skinIndex, weight = o.geometry.attributes.skinWeight;
    for (let i = 0; i < index.count; i++) {
      let best = 0;
      for (let c = 1; c < 4; c++) if (weight.getComponent(i, c) > weight.getComponent(i, best)) best = c;
      if (isHidden(o.skeleton.bones[index.getComponent(i, best)])) continue;
      box.expandByPoint(o.getVertexPosition(i, v).applyMatrix4(o.matrixWorld));
    }
  });
  return box.isEmpty() ? new THREE.Box3().setFromObject(root, true) : box;
}

function frameModel() {
  const { THREE, camera, controls, frameBox } = viewer;
  if (!frameBox) return;
  const size = frameBox.getSize(new THREE.Vector3());
  const center = frameBox.getCenter(new THREE.Vector3());
  const fit = Math.max(size.y, size.x / Math.max(camera.aspect, 0.5)) / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
  const dist = fit * 1.04 + size.z / 2;
  camera.near = dist / 100;
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  // Slightly from the left and above, like a character select screen.
  camera.position.copy(center).add(new THREE.Vector3(Math.sin(0.35) * dist, size.y * 0.06, Math.cos(0.35) * dist));
  controls.target.copy(center);
  controls.minDistance = dist * 0.05;
  controls.maxDistance = dist * 6;
  controls.update();
}

async function loadModel(hero) {
  const token = ++viewer.token;
  const { THREE, scene } = viewer;
  setStatus("Загрузка модели…");
  if (viewer.model) {
    scene.remove(viewer.anchor);
    viewer.mixer?.stopAllAction();
    viewer.model = viewer.anchor = viewer.mixer = viewer.action = viewer.rootBone = viewer.current = viewer.reach = null;
  }
  fillAnimations([]);

  let cached = viewer.cache.get(hero.file);
  if (!cached) {
    const [gltf, pack] = await Promise.all([
      viewer.loader.loadAsync(`heroes/models/${hero.file}`, (e) => {
        if (token === viewer.token && e.total) setStatus(`Загрузка модели… ${Math.round((e.loaded / e.total) * 100)}%`);
      }),
      hero.anims ? openPack(`heroes/models/${hero.anims}`) : null,
    ]);
    cached = { gltf, pack };
    viewer.cache.clear(); // one hero in memory at a time
    viewer.cache.set(hero.file, cached);
  }
  if (token !== viewer.token) return;
  const { gltf, pack } = cached;
  viewer.pack = pack;

  const model = gltf.scene;
  model.traverse((o) => {
    if (o.isMesh) {
      o.frustumCulled = false; // skinned meshes move away from their bind-pose bounds
      if (o.material) o.material.side = THREE.FrontSide;
    }
  });
  const anchor = new THREE.Group();
  anchor.add(model);
  scene.add(anchor);
  viewer.model = model;
  viewer.anchor = anchor;
  viewer.mixer = new THREE.AnimationMixer(model);

  let skeletonRoot = null;
  model.traverse((o) => {
    if (!skeletonRoot && o.isBone && !o.parent?.isBone) skeletonRoot = o;
  });
  viewer.rootBone = skeletonRoot;
  markBranches(model, skeletonRoot);
  model.updateMatrixWorld(true);
  viewer.rootStart = skeletonRoot ? skeletonRoot.getWorldPosition(new THREE.Vector3()) : new THREE.Vector3();

  viewer.clips = pack ? [...pack.clips].sort((a, b) => a.name.localeCompare(b.name)) : [];
  fillAnimations(viewer.clips);
  const first = defaultClip(viewer.clips);
  if (first) await playClip(first.name);
  if (token !== viewer.token) return;

  // Frame on the starting pose, measured with skinning applied.
  viewer.mixer.update(0);
  keepInPlace();
  anchor.updateMatrixWorld(true);
  // Hands count as «near» within a third of the hero's height; the props at the feet then drop out.
  viewer.reach = new THREE.Box3().setFromObject(anchor, true).getSize(new THREE.Vector3()).y * 0.3;
  updateProps();
  hideStrays();
  viewer.frameBox = poseBox(anchor);
  frameModel();
  setStatus("");
  $("#viewer-meta").textContent = viewer.clips.length ? plural(viewer.clips.length, "анимация", "анимации", "анимаций") : "без анимаций";
}

function fillAnimations(clips) {
  const select = $("#anim-select");
  const q = $("#anim-filter").value.trim().toLowerCase().replace(/\s+/g, "_");
  const shown = clips.filter((c) => !q || c.name.toLowerCase().includes(q));
  const groups = new Map();
  for (const c of shown) {
    const g = groupOf(c.name);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }
  const order = [...GROUPS.map(([g]) => g), "Прочее"];
  select.innerHTML = order.filter((g) => groups.has(g)).map((g) => `
    <optgroup label="${g} · ${groups.get(g).length}">
      ${groups.get(g).map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(prettyAnim(c.name))}</option>`).join("")}
    </optgroup>`).join("");
  if (viewer.current) select.value = viewer.current;
  $("#anim-count").textContent = clips.length ? (shown.length === clips.length ? clips.length : `${shown.length} из ${clips.length}`) : "";
}

async function playClip(name) {
  const entry = viewer.clips.find((c) => c.name === name);
  if (!entry || !viewer.mixer) return;
  const mixer = viewer.mixer;
  viewer.current = name;
  $("#anim-select").value = name;
  let clip;
  try {
    clip = await loadClip(viewer.pack, entry);
  } catch (err) {
    console.error(err);
    toast("Не удалось загрузить анимацию");
    return;
  }
  if (mixer !== viewer.mixer || viewer.current !== name) return; // another hero or clip was picked meanwhile
  const prev = viewer.action;
  const action = mixer.clipAction(clip);
  action.reset().setLoop(viewer.THREE.LoopRepeat, Infinity).play();
  if (prev && prev !== action) prev.crossFadeTo(action, 0.18, false);
  viewer.action = action;
  $("#anim-time").max = clip.duration;
  if (viewer.reach) {
    mixer.update(0);
    hideStrays();
  }
  setPlaying(true);
}

// ── Animation pack ─────────────────────────────────────
// One file per hero (written by backend/heroes.py): b"ZAN1", u32 index length, JSON index, then one
// zlib blob per clip. The page reads the index, then fetches each clip by HTTP range when it is picked.
async function fetchRange(url, start, end) {
  const res = await fetch(url, { headers: { Range: `bytes=${start}-${end - 1}` } });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  const buf = new Uint8Array(await res.arrayBuffer());
  // A server without range support sends the whole file: keep it and cut from it.
  return res.status === 206 ? { buf, whole: null } : { buf: buf.subarray(start, end), whole: buf };
}

async function openPack(url) {
  let { buf, whole } = await fetchRange(url, 0, 65536);
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  if (String.fromCharCode(...buf.subarray(0, 4)) !== "ZAN1") throw new Error("bad animation pack");
  const len = view.getUint32(4, true);
  if (8 + len > buf.length && !whole) buf = (await fetchRange(url, 0, 8 + len)).buf;
  else if (whole) buf = whole;
  const index = JSON.parse(new TextDecoder().decode(buf.subarray(8, 8 + len)));
  return { url, base: 8 + len, whole, nodes: index.nodes, clips: index.clips, decoded: new Map() };
}

async function inflate(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function loadClip(pack, entry) {
  if (pack.decoded.has(entry.name)) return pack.decoded.get(entry.name);
  const start = pack.base + entry.o, end = start + entry.n;
  const raw = pack.whole ? pack.whole.subarray(start, end) : (await fetchRange(pack.url, start, end)).buf;
  const clip = decodeClip(await inflate(raw), pack.nodes, entry);
  pack.decoded.set(entry.name, clip);
  return clip;
}

function decodeClip(bytes, nodes, entry) {
  const THREE = viewer.THREE;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let p = 0;
  const u16 = () => { const v = dv.getUint16(p, true); p += 2; return v; };
  const times = [];
  for (let n = u16(); n--;) {
    const count = u16(), t = new Float32Array(count);
    for (let i = 0, ms = 0; i < count; i++) { ms = (ms + u16()) & 0xffff; t[i] = ms / 1000; }
    times.push(t);
  }
  const tracks = [];
  for (let n = u16(); n--;) {
    const node = u16(), path = dv.getUint8(p++), t = times[u16()], count = t.length;
    const name = THREE.PropertyBinding.sanitizeNodeName(nodes[node]);
    if (path === 0) {
      const v = new Float32Array(count * 4), q = [0, 0, 0, 0];
      for (let i = 0; i < count; i++) {
        for (let c = 0; c < 4; c++) { q[c] = ((q[c] + dv.getInt16(p, true)) << 16) >> 16; p += 2; }
        const len = Math.hypot(...q) || 1;
        for (let c = 0; c < 4; c++) v[i * 4 + c] = q[c] / len;
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${name}.quaternion`, t, v));
    } else {
      const lo = [0, 0, 0].map((_, c) => dv.getFloat32(p + c * 4, true));
      const step = [0, 0, 0].map((_, c) => dv.getFloat32(p + 12 + c * 4, true));
      p += 24;
      const v = new Float32Array(count * 3), q = [0, 0, 0];
      for (let i = 0; i < count; i++) {
        for (let c = 0; c < 3; c++) { q[c] = (q[c] + u16()) & 0xffff; v[i * 3 + c] = lo[c] + step[c] * q[c]; }
      }
      tracks.push(new THREE.VectorKeyframeTrack(`${name}.${path === 1 ? "position" : "scale"}`, t, v));
    }
  }
  return new THREE.AnimationClip(entry.name, Math.max(entry.duration, 1 / 30), tracks);
}

function setPlaying(on) {
  viewer.playing = on;
  $("#anim-play").textContent = on ? "❚❚" : "▶";
  $("#anim-play").setAttribute("aria-label", on ? "Пауза" : "Играть");
}

function syncTimeline() {
  const { action } = viewer;
  const d = action.getClip().duration;
  const t = action.time % d;
  $("#anim-time").value = t;
  $("#anim-clock").textContent = `${t.toFixed(2)} / ${d.toFixed(2)} с`;
}

function scrub(t) {
  const { action, mixer } = viewer;
  if (!action) return;
  setPlaying(false);
  // Finish any cross-fade so the frame shows just this clip.
  for (const a of mixer._actions) if (a !== action) a.stop();
  action.setEffectiveWeight(1).play();
  action.time = t;
  mixer.update(0);
  syncTimeline();
}

function setStatus(text) {
  const el = $("#stage-status");
  el.textContent = text;
  el.hidden = !text;
}

function setBackground(bg) {
  viewer.bg = bg;
  $("#stage").dataset.bg = bg;
  for (const b of document.querySelectorAll("[data-bg]")) b.setAttribute("aria-pressed", b.dataset.bg === bg);
}

// ── PNG export ─────────────────────────────────────────
// Renders the current pose again at a higher resolution with a transparent background,
// then trims the empty margins.
async function exportPng() {
  const { renderer, camera, scene, THREE } = viewer;
  if (!viewer.model) return;
  const btn = $("#export-png");
  btn.disabled = true;
  btn.textContent = "Рендер…";
  try {
    // Render the same view into the canvas at a bigger size (tone mapping and colours stay the same as
    // on screen), copy it out, then put the canvas back.
    const size = renderer.getSize(new THREE.Vector2());
    const ratio = renderer.getPixelRatio();
    const gl = renderer.getContext();
    const limit = Math.min(MAX_EXPORT, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), ...gl.getParameter(gl.MAX_VIEWPORT_DIMS));
    const scale = Math.min(viewer.scale * Math.min(devicePixelRatio, 2), limit / Math.max(size.x, size.y));
    const w = Math.round(size.x * scale), h = Math.round(size.y * scale);
    renderer.setPixelRatio(1);
    renderer.setSize(w, h, false);
    renderer.render(scene, camera);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(renderer.domElement, 0, 0);
    renderer.setPixelRatio(ratio);
    renderer.setSize(size.x, size.y, false);

    let out = canvas;
    if ($("#export-trim").checked) out = trim(canvas, ctx.getImageData(0, 0, w, h).data) || canvas;
    const blob = await new Promise((r) => out.toBlob(r, "image/png"));
    const anim = viewer.current || "pose";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${state.hero.key}-${anim}-${out.width}x${out.height}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast(`Сохранено: ${out.width}×${out.height}`);
  } catch (err) {
    console.error(err);
    toast("Не получилось отрендерить картинку");
  } finally {
    btn.disabled = false;
    btn.textContent = "Скачать PNG";
  }
}

function trim(canvas, data) {
  const { width: w, height: h } = canvas;
  let top = h, left = w, right = -1, bottom = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (data[(y * w + x) * 4 + 3] > 2) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        bottom = y;
      }
    }
  }
  if (right < 0) return null;
  const pad = Math.round(Math.max(w, h) * 0.01);
  left = Math.max(0, left - pad); top = Math.max(0, top - pad);
  right = Math.min(w - 1, right + pad); bottom = Math.min(h - 1, bottom + pad);
  const out = document.createElement("canvas");
  out.width = right - left + 1;
  out.height = bottom - top + 1;
  out.getContext("2d").drawImage(canvas, left, top, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

// ── Opening and closing ────────────────────────────────
async function openHero(key, push = true) {
  const hero = state.heroes.find((h) => h.key === key);
  if (!hero?.file) return;
  state.hero = hero;
  $("#viewer-name").textContent = hero.name;
  $("#viewer-meta").textContent = "";
  $("#viewer-icon").src = hero.icon ? `heroes/icons/${hero.icon}` : "";
  $("#anim-filter").value = "";
  const dialog = $("#viewer");
  if (!dialog.open) dialog.showModal();
  if (push) history.replaceState(null, "", `#${key}`);
  try {
    await initThree();
    resizeStage();
    await loadModel(hero);
  } catch (err) {
    console.error(err);
    setStatus("Не удалось загрузить модель. Попробуйте обновить страницу.");
  }
}

function closeViewer() {
  if ($("#viewer").open) return; // «close» arrives a task later: the viewer may already show another hero
  viewer.token++;
  viewer.mixer?.stopAllAction();
  if (viewer.anchor) viewer.scene.remove(viewer.anchor);
  viewer.model = viewer.anchor = viewer.mixer = viewer.action = viewer.current = null;
  $("#viewer-frame").classList.remove("max");
  history.replaceState(null, "", location.pathname + location.search);
}

function wireEvents() {
  $("#hero-search").addEventListener("input", (e) => {
    state.q = e.target.value;
    renderRoster();
  });
  $("#roster").addEventListener("click", (e) => {
    const btn = e.target.closest(".hero-card");
    if (btn) openHero(btn.dataset.key);
  });
  const dialog = $("#viewer");
  $("#viewer-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", closeViewer);
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  $("#viewer-max").addEventListener("click", () => {
    const frame = $("#viewer-frame");
    frame.classList.toggle("max");
    frame.style.width = frame.style.height = "";
  });

  $("#anim-select").addEventListener("change", (e) => playClip(e.target.value));
  $("#anim-filter").addEventListener("input", () => fillAnimations(viewer.clips));
  $("#anim-play").addEventListener("click", () => {
    if (!viewer.action) return;
    if (!viewer.playing) viewer.clock.getDelta();
    setPlaying(!viewer.playing);
  });
  $("#anim-time").addEventListener("input", (e) => scrub(Number(e.target.value)));
  $("#anim-speed").addEventListener("change", (e) => (viewer.speed = Number(e.target.value)));
  $("#turntable").addEventListener("change", (e) => (viewer.controls.autoRotate = e.target.checked));
  $("#reset-view").addEventListener("click", frameModel);
  for (const b of document.querySelectorAll("[data-bg]")) b.addEventListener("click", () => setBackground(b.dataset.bg));
  for (const b of document.querySelectorAll("[data-scale]")) {
    b.addEventListener("click", () => {
      viewer.scale = Number(b.dataset.scale);
      for (const o of document.querySelectorAll("[data-scale]")) o.setAttribute("aria-pressed", o === b);
    });
  }
  $("#export-png").addEventListener("click", exportPng);
  // Space toggles playback while the stage is focused, without scrolling the dialog.
  dialog.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !e.target.matches("input, select, button")) {
      e.preventDefault();
      $("#anim-play").click();
    }
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
  setBackground("checker");
  const res = await fetch("heroes/index.json", { cache: "no-cache" });
  if (!res.ok) throw new Error(`heroes/index.json: ${res.status}`);
  state.index = await res.json();
  state.heroes = state.index.heroes;
  renderRoster();
  const ready = state.heroes.filter((h) => h.file).length;
  $("#source-status").textContent = `Файлы игры: билд ${state.index.build} · ${plural(state.heroes.length, "герой", "героя", "героев")}` +
    (ready < state.heroes.length ? ` · моделей готово ${ready}` : "");
  $("#source-status").classList.add("ok");
  const wanted = decodeURIComponent(location.hash.slice(1));
  if (wanted) openHero(wanted, false);
}

init().catch((err) => {
  console.error(err);
  $("#roster").innerHTML = `<li class="empty pad">Не удалось загрузить список героев. Обновите страницу чуть позже.</li>`;
});
