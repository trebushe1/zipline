const $ = (sel) => document.querySelector(sel);
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const MAX_EXPORT = 8192; // longest side of a downloaded PNG, before trimming
const MAX_VIDEO = 4096; // the same for a WebM
const FPS = 30;
const VP9 = "vp09.00.10.08"; // profile 0, 8 bits

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
// Only running and melee clips are converted (backend/heroes.py). Their names are file names from the
// game, sometimes with the hero's code name in front (vampirebat_melee_hit); shown in plain Russian.
const GROUPS = [
  ["Бег", /(^|_)(run|sprint)(_|$)/],
  ["Ближний бой", /(^|_)(melee|parry)(_|$)/],
];
const STANCE = { weapon: "с оружием", out_of_combat: "вне боя", outofcombat: "вне боя", primary: "основной" };
const MELEE = [
  [/^melee_quick_(\d)$/, (m) => `Быстрый удар ${m[1]}`],
  [/^melee_start$/, () => "Тяжёлый удар: замах"],
  [/^melee_dash$/, () => "Тяжёлый удар: рывок"],
  [/^melee_hit$/, () => "Тяжёлый удар: удар"],
  [/^melee_(?:in_air_)?(start|dash|hit)(?:_in_air)?$/, (m) => `Тяжёлый удар в воздухе: ${{ start: "замах", dash: "рывок", hit: "удар" }[m[1]]}`],
  [/^melee_slide_kick_(start|dash|hit)$/, (m) => `Удар в подкате: ${{ start: "начало", dash: "рывок", hit: "удар" }[m[1]]}`],
  [/^melee_combo_hit_(one|two|three)_(start|recover)(_x)?$/, (m) =>
    `Комбо, удар ${{ one: 1, two: 2, three: 3 }[m[1]]}: ${m[2] === "start" ? "удар" : "возврат"}${m[3] ? " (вариант)" : ""}`],
  [/^melee_combo_generic_recover$/, () => "Комбо: возврат"],
  [/^parry$/, () => "Парирование"],
  [/^parry_idle$/, () => "Парирование: стойка"],
  [/^parry_success$/, () => "Парирование: успех"],
  [/^parry_to_idle$/, () => "Парирование: выход"],
  [/^parry_to_success$/, () => "Парирование: успех"],
];

// «vampirebat_melee_hit» → «melee_hit»: drop a leading word that is not part of the move itself.
const bareName = (name) => {
  const n = name.toLowerCase();
  return /^(melee|parry|run|sprint|weapon|primary|out_of_combat|outofcombat)(_|$)/.test(n) ? n : n.replace(/^[a-z0-9]+_/, "");
};

function prettyAnim(name) {
  const n = bareName(name);
  for (const [re, label] of MELEE) {
    const m = n.match(re);
    if (m) return label(m);
  }
  const run = n.match(/^(?:(weapon|out_of_combat|outofcombat|primary)_)?(run|sprint)(_fast)?(?:_n)?$/);
  if (run) return `${run[2] === "sprint" ? "Спринт" : run[3] ? "Быстрый бег" : "Бег"}${run[1] ? ` ${STANCE[run[1]]}` : ""}`;
  return n.replace(/_/g, " ");
}

const groupOf = (name) => (GROUPS.find(([, re]) => re.test(bareName(name))) || ["Прочее"])[0];

// ── Viewer ─────────────────────────────────────────────
const viewer = {
  THREE: null, renderer: null, scene: null, camera: null, controls: null, loader: null,
  model: null, anchor: null, mixer: null, action: null, clips: [], rootBone: null, rootStart: null,
  playing: true, speed: 1, bg: "checker", scale: 2, frameBox: null, token: 0, cache: new Map(), exporting: false,
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
  if (!renderer || viewer.exporting) return; // a video export owns the canvas size until it is done
  const { clientWidth: w, clientHeight: h } = $("#stage");
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

function tick() {
  // An error thrown here would end three.js's animation loop for good and freeze the picture on the
  // last hero: report it and keep drawing.
  try {
    const dt = Math.min(viewer.clock.getDelta(), 0.1);
    if (viewer.mixer && viewer.playing) viewer.mixer.update(dt * viewer.speed);
    keepInPlace();
    updateProps();
    if (viewer.action && viewer.playing) syncTimeline();
  } catch (err) {
    console.error(err);
  }
  viewer.controls.update();
  viewer.renderer.render(viewer.scene, viewer.camera);
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
  // The hands themselves (hand_L, baba_hand_R, darm_hand_R), not attachment or helper bones named after them.
  body.traverse((b) => { if (b.isBone && /(^|_)hand_[LR]$/i.test(b.name) && !/weapon|object|attach|target|ik/i.test(b.name)) viewer.hands.push(b); });
  if (!viewer.hands.length) return;
  for (const branch of totals.keys()) {
    if (branch !== body && branch !== root) viewer.propBones.push(...branch.children.filter((b) => b.isBone));
  }
  // Some guns hang inside the body's own branch (weapon_hand_R under the spine): the topmost weapon bone.
  const weaponish = (b) => /(^|_)(weapon|gun)(_|$)/i.test(b.name);
  body.traverse((b) => { if (b.isBone && weaponish(b) && !(b.parent && weaponish(b.parent))) viewer.propBones.push(b); });
}

const isHidden = (bone) => {
  for (let b = bone; b?.isBone; b = b.parent) if (b.userData.hidden) return true;
  return false;
};

function updateProps() {
  const { propBones, hands, THREE } = viewer;
  if (!viewer.model || !propBones?.length || !viewer.reach) return;
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
  if (!model) return;
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
    forgetModel();
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

  // Opens still, in the pose the model was made in: an animation starts only when picked.
  viewer.clips = pack ? [...pack.clips].sort((a, b) => prettyAnim(a.name).localeCompare(prettyAnim(b.name), "ru")) : [];
  fillAnimations(viewer.clips);
  setPlaying(false);

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
  const shown = clips.filter((c) => !q || c.name.toLowerCase().includes(q) || prettyAnim(c.name).toLowerCase().replace(/\s+/g, "_").includes(q));
  const groups = new Map();
  for (const c of shown) {
    const g = groupOf(c.name);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }
  const order = [...GROUPS.map(([g]) => g), "Прочее"];
  select.innerHTML = `<option value="">Без анимации</option>` + order.filter((g) => groups.has(g)).map((g) => `
    <optgroup label="${g} · ${groups.get(g).length}">
      ${groups.get(g).map((c) => `<option value="${escapeHtml(c.name)}" title="${escapeHtml(c.name)}">${escapeHtml(prettyAnim(c.name))}</option>`).join("")}
    </optgroup>`).join("");
  select.value = viewer.current || "";
  $("#anim-count").textContent = clips.length ? (shown.length === clips.length ? clips.length : `${shown.length} из ${clips.length}`) : "";
}

// Back to the still model.
function stopClip() {
  const { mixer } = viewer;
  viewer.current = viewer.action = null;
  $("#anim-select").value = "";
  if (!mixer) return;
  mixer.stopAllAction();
  mixer.update(0); // stopped clips hand the bones back to their original pose
  hideStrays();
  setPlaying(false);
  $("#anim-time").value = 0;
  $("#anim-clock").textContent = "—";
}

async function playClip(name) {
  if (!name) return stopClip();
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
  $("#anim-play").disabled = $("#anim-time").disabled = !viewer.action;
  $("#export-webm").disabled = !viewer.action || viewer.exporting;
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
    const [w, h] = exportSize(MAX_EXPORT);
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
    save(blob, `${state.hero.key}-${viewer.current || "pose"}-${out.width}x${out.height}.png`);
    toast(`Сохранено: ${out.width}×${out.height}`);
  } catch (err) {
    console.error(err);
    toast("Не получилось отрендерить картинку");
  } finally {
    btn.disabled = false;
    btn.textContent = "Скачать PNG";
  }
}

// The stage times the chosen scale, within `max` and what the GPU can draw.
function exportSize(max) {
  const { renderer, THREE } = viewer;
  const size = renderer.getSize(new THREE.Vector2());
  const gl = renderer.getContext();
  const limit = Math.min(max, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), ...gl.getParameter(gl.MAX_VIEWPORT_DIMS));
  const scale = Math.min(viewer.scale * Math.min(devicePixelRatio, 2), limit / Math.max(size.x, size.y));
  return [Math.round(size.x * scale), Math.round(size.y * scale)];
}

function save(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

// The visible pixels plus a 1% margin; null for an empty frame.
function alphaBounds(data, w, h) {
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
  return { left: Math.max(0, left - pad), top: Math.max(0, top - pad), right: Math.min(w - 1, right + pad), bottom: Math.min(h - 1, bottom + pad) };
}

function trim(canvas, data) {
  const box = alphaBounds(data, canvas.width, canvas.height);
  if (!box) return null;
  const out = document.createElement("canvas");
  out.width = box.right - box.left + 1;
  out.height = box.bottom - box.top + 1;
  out.getContext("2d").drawImage(canvas, box.left, box.top, out.width, out.height, 0, 0, out.width, out.height);
  return out;
}

// ── WebM export ────────────────────────────────────────
// One loop of the current clip at 30 fps on a transparent background. Frames are posed and rendered one
// by one rather than recorded live, so none is dropped at any size. VideoEncoder has no alpha for VP9 in
// the browsers, so it goes the way ffmpeg does it: a second VP9 stream carries the alpha as brightness and
// is stored with each frame as its BlockAdditional (the format of Telegram's video stickers too).
async function exportWebm() {
  const { renderer, camera, scene, THREE, mixer, action } = viewer;
  if (!action || viewer.exporting) return;
  const btn = $("#export-webm"), pngBtn = $("#export-png");
  const [w, h] = exportSize(MAX_VIDEO);
  const probe = { codec: VP9, width: w & ~1, height: h & ~1, framerate: FPS };
  const supports = async (config) => !!window.VideoEncoder && !!(await VideoEncoder.isConfigSupported(config).catch(() => ({}))).supported;
  if (!(await supports(probe))) {
    toast("Этот браузер не умеет записывать WebM — откройте сайт в Chrome");
    return;
  }
  // Constant quality where the browser has it. Under bitrate control the first frame of the alpha comes out
  // coarse, and a faint haze (alpha 5 instead of 0) spreads over the whole background.
  const quantizer = await supports({ ...probe, bitrateMode: "quantizer" });
  const token = viewer.token, key = state.hero.key, name = viewer.current;
  const stillHere = () => token === viewer.token && action === viewer.action;
  const wasPlaying = viewer.playing, wasTime = action.time;
  const size = renderer.getSize(new THREE.Vector2()), ratio = renderer.getPixelRatio();
  viewer.exporting = true;
  btn.disabled = pngBtn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  renderer.setAnimationLoop(null); // the frames are posed by hand below
  const encoders = [];
  try {
    renderer.setPixelRatio(1);
    renderer.setSize(w, h, false);
    // Just this clip, at full weight, even in the middle of a cross-fade.
    for (const a of mixer._actions) if (a !== action) a.stop();
    action.stopFading().setEffectiveWeight(1).play();
    const duration = action.getClip().duration;
    const count = Math.max(1, Math.round((duration / viewer.speed) * FPS));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const trimmed = $("#export-trim").checked, passes = trimmed ? 2 : 1;
    // Frame i of the loop: the straight RGBA pixels inside box.
    const draw = async (i, pass, box) => {
      btn.textContent = `Запись… ${Math.round(((pass * count + i) / (passes * count)) * 100)}%`;
      await nextTask(); // let the label paint
      if (!stillHere()) throw new Error("aborted");
      action.time = (i / count) * duration;
      mixer.update(0);
      keepInPlace();
      updateProps();
      renderer.render(scene, camera);
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(renderer.domElement, 0, 0);
      return ctx.getImageData(box.left, box.top, box.right - box.left + 1, box.bottom - box.top + 1).data;
    };

    let box = { left: 0, top: 0, right: w - 1, bottom: h - 1 };
    if (trimmed) {
      // One frame around everything the hero reaches in the loop, so the hero doesn't jump inside it.
      let all = null;
      for (let i = 0; i < count; i++) {
        const b = alphaBounds(await draw(i, 0, box), w, h);
        if (b) all = all ? { left: Math.min(all.left, b.left), top: Math.min(all.top, b.top), right: Math.max(all.right, b.right), bottom: Math.max(all.bottom, b.bottom) } : b;
      }
      if (all) box = all;
    }
    // 4:2:0 video: even sizes only.
    box.right -= (box.right - box.left + 1) % 2;
    box.bottom -= (box.bottom - box.top + 1) % 2;
    const cw = box.right - box.left + 1, ch = box.bottom - box.top + 1;

    // Colour, then alpha. The alpha gets the finer quantizer: it is flat almost everywhere and costs little,
    // and any error in it shows as a haze or holes.
    const streams = [{ q: 30, bits: 0.12 }, { q: 4, bits: 0.12 }];
    const chunks = [[], []];
    let failure = null, wake = null;
    for (const [k, { bits }] of streams.entries()) {
      const encoder = new VideoEncoder({
        output: (chunk) => {
          const data = new Uint8Array(chunk.byteLength);
          chunk.copyTo(data);
          chunks[k].push({ key: chunk.type === "key", data });
          wake?.();
        },
        error: (err) => {
          failure = err;
          wake?.();
        },
      });
      encoder.addEventListener("dequeue", () => wake?.());
      const rate = quantizer ? { bitrateMode: "quantizer" } : { bitrate: Math.round(Math.max(1e6, cw * ch * FPS * bits)) };
      encoder.configure({ codec: VP9, width: cw, height: ch, framerate: FPS, ...rate });
      encoders.push(encoder);
    }
    const colorSpace = { primaries: "bt709", transfer: "bt709", matrix: "bt709", fullRange: false };
    for (let i = 0; i < count; i++) {
      const planes = toI420(await draw(i, passes - 1, box), cw, ch);
      const init = { format: "I420", codedWidth: cw, codedHeight: ch, timestamp: Math.round((i * 1e6) / FPS), duration: Math.round(1e6 / FPS), colorSpace };
      planes.forEach((data, k) => {
        const frame = new VideoFrame(data, init);
        encoders[k].encode(frame, { keyFrame: i % (2 * FPS) === 0, ...(quantizer && { vp9: { quantizer: streams[k].q } }) });
        frame.close();
      });
      // Wait on the encoders' own output rather than on timers, which a background tab slows down.
      while (!failure && encoders.some((e) => e.encodeQueueSize > 2)) await new Promise((r) => (wake = r));
      if (failure) throw failure;
    }
    await Promise.all(encoders.map((e) => e.flush()));
    if (failure) throw failure;
    if (chunks[0].length !== count || chunks[1].length !== count) throw new Error(`encoded ${chunks[0].length}/${chunks[1].length} of ${count} frames`);

    const blob = webm(cw, ch, chunks[0].map((c, i) => ({ key: c.key && chunks[1][i].key, data: c.data, alpha: chunks[1][i].data })));
    save(blob, `${key}-${name}-${cw}x${ch}.webm`);
    toast(`Сохранено: ${cw}×${ch}, ${plural(count, "кадр", "кадра", "кадров")}, ${(blob.size / 2 ** 20).toFixed(1)} МБ`);
  } catch (err) {
    if (err.message === "aborted") {
      toast("Запись прервана");
    } else {
      console.error(err);
      toast("Не получилось записать видео");
    }
  } finally {
    for (const e of encoders) if (e.state !== "closed") e.close();
    renderer.setPixelRatio(ratio);
    renderer.setSize(size.x, size.y, false);
    viewer.exporting = false;
    if (stillHere()) {
      action.time = wasTime;
      mixer.update(0);
      setPlaying(wasPlaying);
    }
    btn.disabled = !viewer.action;
    btn.removeAttribute("aria-busy");
    btn.textContent = "Скачать WebM";
    pngBtn.disabled = false;
    resizeStage();
    viewer.clock.getDelta();
    renderer.setAnimationLoop(tick);
  }
}

// A message instead of setTimeout: timers in a background tab fire once a second at best.
const nextTask = () => new Promise((r) => {
  const { port1, port2 } = new MessageChannel();
  port1.onmessage = r;
  port2.postMessage(0);
});

// Straight RGBA → two I420 frames (BT.709, limited range): the colour, and the alpha as brightness.
function toI420(px, w, h) {
  bleed(px, w, h);
  const n = w * h, half = w >> 1;
  const color = new Uint8Array(n * 1.5), alpha = new Uint8Array(n * 1.5).fill(128, n);
  for (let i = 0; i < n; i++) {
    const p = i * 4;
    color[i] = 16.5 + (0.2126 * px[p] + 0.7152 * px[p + 1] + 0.0722 * px[p + 2]) * (219 / 255);
    alpha[i] = px[p + 3];
  }
  const u = n, v = n + n / 4, k = 224 / 255 / 4; // chroma: the sum of each 2×2 block
  for (let y = 0; y < h; y += 2) {
    for (let x = 0; x < w; x += 2) {
      const p0 = (y * w + x) * 4, p1 = p0 + 4, p2 = p0 + w * 4, p3 = p2 + 4;
      const r = px[p0] + px[p1] + px[p2] + px[p3];
      const g = px[p0 + 1] + px[p1 + 1] + px[p2 + 1] + px[p3 + 1];
      const b = px[p0 + 2] + px[p1 + 2] + px[p2 + 2] + px[p3 + 2];
      const c = (y >> 1) * half + (x >> 1);
      color[u + c] = 128.5 + (-0.114572 * r - 0.385428 * g + 0.5 * b) * k;
      color[v + c] = 128.5 + (0.5 * r - 0.454153 * g - 0.045847 * b) * k;
    }
  }
  return [color, alpha];
}

// Transparent pixels are black. Spread the edge colours a couple of pixels into them, or compression,
// the halved chroma and scaling in the player would mix that black into the outline of the hero.
function bleed(px, w, h, passes = 2) {
  // 1 where the colour is known, inside an empty 1-pixel border: neighbours need no bounds checks, and the
  // loop stays fast (reading outside a typed array throws V8 off its fast path: ten times slower).
  const W = w + 2, known = new Uint8Array(W * (h + 2));
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) known[(y + 1) * W + x + 1] = px[(y * w + x) * 4 + 3] > 0 ? 1 : 0;
  const dk = [-1, 1, -W, W], dp = [-4, 4, -4 * w, 4 * w]; // left, right, up, down in known and in px
  for (let pass = 0; pass < passes; pass++) {
    for (let y = 0; y < h; y++) {
      for (let x = 0, k = (y + 1) * W + 1, i = y * w * 4; x < w; x++, k++, i += 4) {
        // Pixels filled in this same pass are 2 and don't count yet.
        if (known[k] || !((known[k - 1] | known[k + 1] | known[k - W] | known[k + W]) & 1)) continue;
        let r = 0, g = 0, b = 0, c = 0;
        for (let d = 0; d < 4; d++) {
          if (known[k + dk[d]] !== 1) continue;
          const q = i + dp[d];
          r += px[q]; g += px[q + 1]; b += px[q + 2]; c++;
        }
        px[i] = r / c; px[i + 1] = g / c; px[i + 2] = b / c;
        known[k] = 2;
      }
    }
    for (let k = 0; k < known.length; k++) if (known[k] === 2) known[k] = 1;
  }
}

// ── WebM file ──────────────────────────────────────────
// Just enough Matroska for one VP9 track with alpha: AlphaMode on the track, and the alpha frame as
// BlockAdditional 1 next to each colour frame, as ffmpeg writes it.
function cat(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

const bytesOf = (n, len) => {
  const out = new Uint8Array(len);
  for (let i = len - 1; i >= 0; i--, n = Math.floor(n / 256)) out[i] = n % 256;
  return out;
};

const uintOf = (n) => {
  let len = 1;
  while (n >= 256 ** len) len++;
  return bytesOf(n, len);
};

// One element: an unsigned number, a string, raw bytes, or a list of child elements (nulls skipped).
function el(id, body) {
  if (typeof body === "number") body = uintOf(body);
  else if (typeof body === "string") body = new TextEncoder().encode(body);
  else if (Array.isArray(body)) body = cat(body.filter(Boolean));
  let len = 1;
  while (body.length >= 2 ** (7 * len) - 1) len++;
  const size = bytesOf(body.length, len);
  size[0] |= 0x80 >> (len - 1);
  return cat([uintOf(id), size, body]);
}

function webm(width, height, frames) {
  const f64 = (x) => { const b = new Uint8Array(8); new DataView(b.buffer).setFloat64(0, x); return b; };
  const i16 = (x) => { const b = new Uint8Array(2); new DataView(b.buffer).setInt16(0, x); return b; };
  const ms = (i) => Math.round((i * 1000) / FPS);
  // A cluster per keyframe; block times inside it are 16-bit offsets from its start.
  const clusters = [];
  frames.forEach((f, i) => {
    if (f.key || !clusters.length || ms(i) - clusters.at(-1).start > 30000) clusters.push({ start: ms(i), blocks: [] });
    const cluster = clusters.at(-1);
    cluster.blocks.push(el(0xa0, [ // BlockGroup
      el(0xa1, cat([new Uint8Array([0x81]), i16(ms(i) - cluster.start), new Uint8Array([0]), f.data])), // Block: track 1
      f.key ? null : el(0xfb, i16(ms(i - 1) - ms(i))), // ReferenceBlock: the frame before; none on a keyframe
      el(0x75a1, [el(0xa6, [el(0xee, 1), el(0xa5, f.alpha)])]), // BlockAdditions → BlockMore: id 1, the alpha
    ]));
  });
  return new Blob([
    el(0x1a45dfa3, [ // EBML header: webm, version 4 for AlphaMode
      el(0x4286, 1), el(0x42f7, 1), el(0x42f2, 4), el(0x42f3, 8), el(0x4282, "webm"), el(0x4287, 4), el(0x4285, 2),
    ]),
    el(0x18538067, [ // Segment
      el(0x1549a966, [ // Info: times in ms, duration
        el(0x2ad7b1, 1e6), el(0x4d80, "zipline"), el(0x5741, "zipline"), el(0x4489, f64((frames.length * 1000) / FPS)),
      ]),
      el(0x1654ae6b, [el(0xae, [ // Tracks → TrackEntry: video track 1, VP9, frame duration in ns
        el(0xd7, 1), el(0x73c5, 1), el(0x83, 1), el(0x9c, 0), el(0x86, "V_VP9"), el(0x23e383, Math.round(1e9 / FPS)),
        el(0xe0, [ // Video: size, AlphaMode, Colour (BT.709, limited range)
          el(0xb0, width), el(0xba, height), el(0x53c0, 1),
          el(0x55b0, [el(0x55b1, 1), el(0x55b9, 1), el(0x55ba, 1), el(0x55bb, 1)]),
        ]),
      ])]),
      ...clusters.map((c) => el(0x1f43b675, [el(0xe7, c.start), ...c.blocks])), // Cluster: start time, blocks
    ]),
  ], { type: "video/webm" });
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

// Everything that points into the current model, so nothing reaches for it once it is gone.
function forgetModel() {
  // The model stays cached for the next opening: give the shrunk bones their size back.
  for (const b of [...(viewer.propBones || []), ...(viewer.strays || [])]) {
    if (b.userData.hidden) { b.scale.copy(b.userData.hidden); b.userData.hidden = null; }
  }
  viewer.model = viewer.anchor = viewer.mixer = viewer.action = viewer.rootBone = viewer.current = null;
  viewer.reach = viewer.frameBox = null;
  viewer.propBones = [];
  viewer.hands = [];
  viewer.strays = [];
}

function closeViewer() {
  if ($("#viewer").open) return; // «close» arrives a task later: the viewer may already show another hero
  viewer.token++;
  viewer.mixer?.stopAllAction();
  if (viewer.anchor) viewer.scene.remove(viewer.anchor);
  forgetModel();
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
  const setSpeed = (v) => {
    viewer.speed = v;
    $("#anim-speed").value = v;
    $("#anim-speed-value").textContent = `×${v.toFixed(2)}`;
  };
  $("#anim-speed").addEventListener("input", (e) => setSpeed(Number(e.target.value)));
  $("#anim-speed-reset").addEventListener("click", () => setSpeed(1));
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
  $("#export-webm").addEventListener("click", exportWebm);
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
