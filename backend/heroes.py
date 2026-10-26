"""Hero gallery: icons and animated 3D models of every playable Deadlock hero.

Reads the hero list from the newest build in SteamDB's GameTracking-Deadlock, downloads
each hero's model with everything it uses (meshes, materials, textures, skeleton,
animation graph and clips) straight from Steam and converts it with Source2Viewer-CLI.
Each hero becomes two files in the GitHub release HEROES_TAG: the model (GLB, compressed
with gltfpack) and a pack of its most telling animations (see animation_pack), which the
page fetches one clip at a time. Icons go to data/heroes/icons/ (small, kept in git).
A hero is converted again only when one of its files changed.

    python -m backend.heroes                  # convert new and changed heroes
    python -m backend.heroes --fetch DIR      # download the models into DIR (for the site build)
    python -m backend.heroes --raw DIR HERO   # debug: download one hero's game files into DIR

Needs STEAM_USERNAME / STEAM_PASSWORD (see backend/media.py), the gh CLI, numpy and pillow.
"""
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

from . import config
from .media import TOOLS, SteamFiles, game_root, gh, log, manifest_id, references, tool_paths
from .patches import GAME, HEROES_VDATA, PAK_LIST, REPO, build_commits, load_loc, show, sync_repo

OUT = config.ROOT / "data" / "heroes"
HEROES_TAG = "heroes"
MODEL_VERSION = 3  # bump to convert every hero again after changing the conversion
VRF_VERSION = "20.1"  # first release that exports Deadlock's animation graph clips to glTF
GLTFPACK_VERSION = "v1.3"
COLOR_SIZE = 4096  # colour keeps the game's own resolution (2048 for most heroes, 4096 for some): quality first
NORMAL_SIZE = 2048  # surface detail
TEXTURE_SIZE = 1024  # roughness, metalness, occlusion and glow masks: smooth, so small is enough
ICON_HEIGHT = 384

# Not real heroes: the template every hero inherits from and the practice dummy.
NOT_HEROES = {"hero_base", "hero_targetdummy", "hero_genericperson"}
HERO_BLOCK = re.compile(r"^\t(hero_\w+) = $", re.M)
FIELD = r"^\t\t{} = (.+)$"
RESOURCE = re.compile(r'"(?:resource_name:|panorama:)?"?([^"]+)"')
# Sounds and particles are not part of the model, and pull in thousands of files.
NOT_FOLLOWED = (".vsndevts", ".vsnd", ".vpcf", ".vsndstck", ".vpulse")


# ── Hero list ──────────────────────────────────────────

def field(block: str, name: str) -> str | None:
    m = re.search(FIELD.format(name), block, re.M)
    return m[1].strip() if m else None


def resource(value: str | None) -> str | None:
    m = value and RESOURCE.search(value)
    return m[1] if m else None


ABILITIES_VDATA = f"{GAME}/pak01_dir/scripts/abilities.vdata"
# The disc and ring the game draws ability icons on (hud_ability_icon.css: .ability_bg, .ability_ring),
# as vectors so they stay sharp at any size, with the bitmaps as a fallback.
UI_ASSETS = {f"ability_frame_{n}.{ext}": f"panorama/images/hud/core/ability_frame_passive_{n}{suffix}"
             for n in (0, 1) for ext, suffix in (("svg", ".vsvg_c"), ("png", "_psd.vtex_c"))}
SIGNATURE_SLOT = re.compile(r'ESlot_Signature_(\d) = "(\w+)"')
ABILITY_BLOCK = re.compile(r"^\t(\w+) = $", re.M)


def panorama_path(value: str | None) -> str | None:
    """panorama:"file://{images}/hud/abilities/bull_charge.psd" → panorama/images/hud/abilities/bull_charge_psd.vtex_c"""
    path = resource(value)
    return re.sub(r"^file://\{images\}/", "panorama/images/", path).replace(".", "_") + ".vtex_c" if path else None


def ability_images(sha: str) -> dict[str, str]:
    text = show(sha, ABILITIES_VDATA)
    starts = [(m.start(), m[1]) for m in ABILITY_BLOCK.finditer(text)]
    images = {}
    for i, (pos, key) in enumerate(starts):
        block = text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        if image := panorama_path(field(block, "m_strAbilityImage")):
            images[key] = image
    return images


def hero_list(sha: str) -> list[dict]:
    """Playable heroes in the build: not disabled, with a model and a card. «In development» is no filter:
    released heroes such as Violet and Baba still carry that flag."""
    text, loc, images = show(sha, HEROES_VDATA), load_loc(sha), ability_images(sha)
    starts = [(m.start(), m[1]) for m in HERO_BLOCK.finditer(text)]
    heroes = []
    for i, (pos, key) in enumerate(starts):
        block = text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        if key in NOT_HEROES or field(block, "m_bDisabled") == "true":
            continue
        model, card = resource(field(block, "m_strModelName")), resource(field(block, "m_strIconHeroCard"))
        if not (model and card):
            continue
        heroes.append({
            "key": key[5:], "id": int(field(block, "m_HeroID") or 0),
            "name": loc.get(f"{key}:n") or key[5:].capitalize(),
            "model": model + "_c",
            # file://{images}/heroes/bull_card.psd → panorama/images/heroes/bull_card_psd.vtex_c
            "card": panorama_path(f'"{card}"'),
            # The four abilities on the hero's bar, in slot order (the last one is the ultimate).
            "abilities": [{"key": a, "slot": int(n), "name": loc.get(a) or a, "image": images[a]}
                          for n, a in sorted(SIGNATURE_SLOT.findall(block)) if a in images],
        })
    return sorted(heroes, key=lambda h: h["name"].lower())


def pak_crcs(sha: str) -> dict[str, str]:
    out = {}
    for line in show(sha, PAK_LIST).splitlines():
        parts = line.rsplit(" ", 2)
        if len(parts) == 3 and parts[1].startswith("CRC:"):
            out[parts[0]] = parts[1][4:]
    return out


def fingerprint(deps: list[str], crcs: dict[str, str]) -> str:
    """Changes when any file the hero's model was built from changes in the game."""
    h = hashlib.sha1(f"v{MODEL_VERSION}".encode())
    for path in deps:
        h.update(f"{path}:{crcs.get(path, '-')}\n".encode())
    return h.hexdigest()[:12]


# ── Downloads ──────────────────────────────────────────

def clip_paths(model: str, crcs: dict[str, str]) -> set[str]:
    """Animation clips live next to the model (…/clips/*.vnmclip_c), not referenced by the model itself."""
    folder = model.rsplit("/", 1)[0] + "/"
    return {p for p in crcs if p.startswith(folder) and p.endswith((".vnmclip_c", ".vnmskel_c", ".vnmgraph_c"))}


def fetch_all(files: SteamFiles, wanted: set[str], levels: int = 8) -> None:
    """Like SteamFiles.fetch_with_dependencies, but also through animation graphs, skeletons and clips."""
    for _ in range(levels):
        files.get(wanted)
        wanted = set()
        for manifest in files.manifests:
            root = files.root(manifest)
            for p in files.requested:
                f = root / p
                if f.exists() and not p.endswith(".vtex_c"):
                    wanted.update(r if r.endswith("_c") else r + "_c" for r in references(f.read_bytes())
                                  if "." in r.rsplit("/", 1)[-1] and not r.endswith(NOT_FOLLOWED))
        wanted -= files.requested
        if not wanted:
            return


def vrf_path() -> str:
    """Source2Viewer-CLI of VRF_VERSION (SteamDB's tool bundle may lag behind)."""
    if os.environ.get("VRF_PATH"):
        return os.environ["VRF_PATH"]
    exe = TOOLS / f"vrf-{VRF_VERSION}" / "Source2Viewer-CLI"
    if not exe.exists():
        exe.parent.mkdir(parents=True, exist_ok=True)
        gh("release", "download", VRF_VERSION, "--repo", "ValveResourceFormat/ValveResourceFormat",
           "--pattern", "cli-linux-x64.zip", "--dir", str(exe.parent), "--clobber")
        with zipfile.ZipFile(exe.parent / "cli-linux-x64.zip") as z:
            z.extractall(exe.parent)
        exe.chmod(0o755)
    return str(exe)


# ── Which animations ───────────────────────────────────

# Only running and melee: the forward run of each stance (out of combat, with the weapon, sprint) and
# every melee swing and parry. Strafing, crouched, item and syphon variants and aim layers are left out.
STANCES = {"weapon", "primary", "out", "of", "combat", "outofcombat"}
NOT_THESE = {"item", "syphon", "crouch", "barrage", "heal", "resupply", "sentry", "reload", "shoot", "death",
             "corner", "landing", "ability", "aim", "flinch", "add", "up", "down", "dwn", "lean"}


def keep_clip(name: str) -> bool:
    """Clip names are file names, sometimes with the hero's code name in front (vampirebat_melee_hit)."""
    words = name.lower().split("_")
    if NOT_THESE & set(words):
        return False
    for i, word in enumerate(words):
        if word in ("melee", "parry"):
            return i <= 1
        if word in ("run", "sprint"):
            before, after = words[:i], words[i + 1:]
            if before and before[0] not in STANCES:
                before = before[1:]  # the hero's code name
            return set(before) <= STANCES and after in ([], ["n"], ["fast", "n"])
    return False


def clip_names(paths: list[str]) -> list[str]:
    """Leaf names of the clips (out of the files the model uses) worth showing."""
    names = {p.rsplit("/", 1)[-1].split(".", 1)[0] for p in paths
             if p.endswith(".vnmclip_c") and "+" not in p.rsplit("/", 1)[-1]}
    return sorted(n for n in names if keep_clip(n))


# ── Conversion ─────────────────────────────────────────

def gltfpack_path() -> str:
    if os.environ.get("GLTFPACK_PATH"):
        return os.environ["GLTFPACK_PATH"]
    exe = TOOLS / f"gltfpack-{GLTFPACK_VERSION}" / "gltfpack"
    if not exe.exists():
        exe.parent.mkdir(parents=True, exist_ok=True)
        gh("release", "download", GLTFPACK_VERSION, "--repo", "zeux/meshoptimizer",
           "--pattern", "gltfpack-ubuntu.zip", "--dir", str(exe.parent), "--clobber")
        with zipfile.ZipFile(exe.parent / "gltfpack-ubuntu.zip") as z:
            z.extractall(exe.parent)
        exe.chmod(0o755)
    return str(exe)


def convert_model(vrf: str, citadel: Path, path: str, clips: list[str], tmp: Path) -> tuple[bytes, bytes, int] | None:
    """Model (GLB, meshopt-compressed) and its animations (our own compact pack), and how many clips made it."""
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / "model.glb"
    cmd = [vrf, "-i", str(citadel / path), "--game", str(citadel / "gameinfo.gi"), "-o", str(out), "-d",
           "--gltf_export_format", "glb", "--gltf_export_materials", "--gltf_textures_adapt", "--gltf_export_animations"]
    if clips:
        cmd += ["--gltf_animation_list", ",".join(clips)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if not out.exists():
        log(f"  conversion failed: {(res.stdout + res.stderr).strip()[-600:]}")
        return None
    doc, binary = read_glb(out.read_bytes())
    anims, count = animation_pack(doc, binary)
    doc["animations"] = []
    hide_off_meshes(doc, vrf, citadel / path)
    binary = bake_materials(doc, binary, vrf, citadel, out.parent)
    (tmp / "static.glb").write_bytes(pack(doc, binary, out.parent))
    res = subprocess.run([gltfpack_path(), "-i", str(tmp / "static.glb"), "-o", str(tmp / "packed.glb"), "-cc", "-kn"],
                         capture_output=True, text=True)
    if not (tmp / "packed.glb").exists():
        log(f"  gltfpack failed: {(res.stdout + res.stderr).strip()[-600:]}")
        return None
    return (tmp / "packed.glb").read_bytes(), anims, count


def srgb_to_linear(c):
    import numpy as np
    c = np.clip(c, 0, None)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    import numpy as np
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def adjust_albedo(rgb, params: dict):
    """g_vAlbedoContrastSaturationBrightness1, on linear colour."""
    import numpy as np
    contrast, saturation, brightness = (params.get("g_vAlbedoContrastSaturationBrightness1") or [1, 1, 1])[:3]
    if saturation != 1:
        luma = rgb @ np.array([0.2126, 0.7152, 0.0722])
        rgb = luma[..., None] + (rgb - luma[..., None]) * saturation
    if contrast != 1:
        rgb = (rgb - 0.5) * contrast + 0.5
    return np.clip(rgb * brightness, 0, None)


class Textures:
    """Game textures decoded to images by Source2Viewer-CLI, once each."""

    def __init__(self, vrf: str, citadel: Path, tmp: Path):
        self.vrf, self.citadel, self.tmp, self.cache = vrf, citadel, tmp, {}

    def get(self, resource: str | None):
        from PIL import Image
        if not resource:
            return None
        path = resource if resource.endswith("_c") else resource + "_c"
        if path not in self.cache:
            out = self.tmp / f"t{len(self.cache)}"
            out.mkdir(parents=True, exist_ok=True)
            if (self.citadel / path).exists():
                subprocess.run([self.vrf, "-i", str(self.citadel / path), "-o", str(out / "t.png"), "-d"], capture_output=True)
            pngs = sorted(out.rglob("*.png"))
            self.cache[path] = Image.open(pngs[0]).convert("RGBA") if pngs else None
        return self.cache[path]


def bake_materials(doc: dict, binary: bytes, vrf: str, citadel: Path, folder: Path) -> bytes:
    """Bring the colours Deadlock's shader computes into plain glTF materials. Source2Viewer exports the
    raw colour texture, but the game multiplies it by g_vColorTint1 under a tint mask, adjusts contrast,
    saturation and brightness, and on many meshes colours by vertex colour, which it reads as sRGB
    (glTF vertex colours are linear). The normal map (g_tNormalRoughness) is added as well. The shader
    maths follows Source2Viewer's renderer (complex.vert/frag): albedo × (1 − mask × (1 − vertex × tint))."""
    import numpy as np
    from PIL import Image
    textures = Textures(vrf, citadel, folder / "baked")
    materials = doc.get("materials", [])
    with_vertex_colors = {p.get("material") for m in doc.get("meshes", []) for p in m["primitives"] if "COLOR_0" in p["attributes"]}
    binary = bytearray(binary)

    def add_accessor(values) -> int:
        while len(binary) % 4:
            binary.append(0)
        data = values.astype(np.float32).tobytes()
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(data)})
        binary.extend(data)
        doc["accessors"].append({"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5126,
                                 "count": len(values), "type": "VEC4"})
        return len(doc["accessors"]) - 1

    def add_image(img, name: str) -> int:
        img.save(folder / name)
        doc.setdefault("images", []).append({"uri": name})
        doc.setdefault("textures", []).append({"source": len(doc["images"]) - 1})
        return len(doc["textures"]) - 1

    for n, material in enumerate(materials):
        vmat = (material.get("extras") or {}).get("vmat") or {}
        ints, vectors, texparams = vmat.get("IntParams", {}), vmat.get("VectorParams", {}), vmat.get("TextureParams", {})
        tint = srgb_to_linear(np.array((vectors.get("g_vColorTint1") or [1, 1, 1])[:3], float))
        mask_img = textures.get(texparams.get("g_tTintMaskRimLightMask"))
        pbr = material.setdefault("pbrMetallicRoughness", {})

        # Vertex colours: per vertex, with the tint mask sampled at the vertex.
        if n in with_vertex_colors:
            for mesh in doc["meshes"]:
                for prim in mesh["primitives"]:
                    if prim.get("material") != n or "COLOR_0" not in prim["attributes"]:
                        continue
                    acc = doc["accessors"][prim["attributes"]["COLOR_0"]]
                    if acc["componentType"] != 5126:
                        continue
                    colors = accessor(doc, bytes(binary), prim["attributes"]["COLOR_0"]).astype(float)
                    rgb = srgb_to_linear(colors[:, :3]) * tint
                    if ints.get("g_bMaskVertexColorTint1", 1) and mask_img is not None and "TEXCOORD_0" in prim["attributes"]:
                        uv = accessor(doc, bytes(binary), prim["attributes"]["TEXCOORD_0"])
                        mask = np.asarray(mask_img)[..., 0].astype(float) / 255
                        h, w = mask.shape
                        m = mask[(np.mod(uv[:, 1], 1) * h).astype(int).clip(0, h - 1), (np.mod(uv[:, 0], 1) * w).astype(int).clip(0, w - 1)]
                        rgb = 1 - m[:, None] * (1 - rgb)
                    rgb = adjust_albedo(rgb, vectors)
                    alpha = colors[:, 3:4] if colors.shape[1] == 4 else np.ones((len(colors), 1))
                    prim["attributes"]["COLOR_0"] = add_accessor(np.clip(np.hstack([rgb, alpha]), 0, 1))
        # Colour texture: tint and adjustments baked in (vertex-coloured materials got them per vertex).
        elif "baseColorTexture" in pbr:
            needs_tint = not np.allclose(tint, 1, atol=0.004)
            needs_adjust = (vectors.get("g_vAlbedoContrastSaturationBrightness1") or [1, 1, 1])[:3] != [1, 1, 1]
            image = doc["images"][doc["textures"][pbr["baseColorTexture"]["index"]]["source"]]
            if (needs_tint or needs_adjust) and "uri" in image and (folder / image["uri"]).exists():
                src = Image.open(folder / image["uri"]).convert("RGBA")
                px = np.asarray(src).astype(float) / 255
                rgb = srgb_to_linear(px[..., :3])
                if needs_tint:
                    if ints.get("g_bMaskColorTint1", 1) and mask_img is not None:
                        m = np.asarray(mask_img.resize(src.size, Image.BILINEAR))[..., 0].astype(float)[..., None] / 255
                        rgb = rgb * (1 - m * (1 - tint))
                    else:
                        rgb = rgb * tint
                rgb = adjust_albedo(rgb, vectors)
                out = np.dstack([linear_to_srgb(rgb), px[..., 3]])
                pbr["baseColorTexture"] = {"index": add_image(Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA"), f"baked_color_{n}.png")}
        # Normal map, unless the material only has the flat default.
        normal = textures.get(texparams.get("g_tNormalRoughness"))
        if normal is not None and min(normal.size) > 4 and "normalTexture" not in material:
            material["normalTexture"] = {"index": add_image(normal.convert("RGB"), f"baked_normal_{n}.png")}
    return bytes(binary)


def hide_off_meshes(doc: dict, vrf: str, model: Path) -> None:
    """Drop the meshes the game does not show by default: ability props and alternative body parts
    sit in mesh groups outside m_nDefaultMeshGroupMask (a teapot or a brush parked at the hero's feet).
    Source2Viewer exports the meshes of the first LoD in mesh index order, so the n-th glTF mesh is
    the n-th mesh index of that LoD."""
    res = subprocess.run([vrf, "-i", str(model), "-b", "DATA"], capture_output=True, text=True)
    text = res.stdout

    def ints(field: str) -> list[int]:
        m = re.search(rf"\b{field} =\s*\[([^\]]*)\]", text)
        return [int(x) for x in re.findall(r"-?\d+", m[1])] if m else []

    groups, lods = ints("m_refMeshGroupMasks"), ints("m_refLODGroupMasks")
    default = re.search(r"\bm_nDefaultMeshGroupMask = (\d+)", text)
    if not groups or not default:
        return
    lowest = min((m & -m for m in lods if m), default=1)
    first_lod = [i for i in range(len(groups)) if not lods or i < len(lods) and lods[i] & lowest]
    if len(first_lod) != len(doc.get("meshes", [])):
        log(f"  mesh groups: {len(first_lod)} meshes in the first LoD, {len(doc.get('meshes', []))} exported; keeping all")
        return
    hidden = {n for n, i in enumerate(first_lod) if not groups[i] & int(default[1])}
    for node in doc.get("nodes", []):
        if node.get("mesh") in hidden:
            log(f"  hidden by default: {doc['meshes'][node['mesh']].get('name', '').rsplit('.', 1)[-1]}")
            node.pop("mesh")
            node.pop("skin", None)


def read_glb(data: bytes) -> tuple[dict, bytes]:
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + json_len])
    rest = 20 + json_len
    binary = data[rest + 8:rest + 8 + struct.unpack_from("<I", data, rest)[0]] if rest < len(data) else b""
    return doc, binary


def write_glb(doc: dict, binary: bytes) -> bytes:
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


def accessor(doc: dict, binary: bytes, i: int):
    import numpy as np
    a = doc["accessors"][i]
    v = doc["bufferViews"][a["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    return np.frombuffer(binary, np.float32, a["count"] * width,
                         v.get("byteOffset", 0) + a.get("byteOffset", 0)).reshape(a["count"], width)


PATHS = {"rotation": 0, "translation": 1, "scale": 2}


def animation_pack(doc: dict, binary: bytes) -> tuple[bytes, int]:
    """All clips of a hero in one file, each compressed on its own so the page fetches just the one picked
    (HTTP range request). Layout: b"ZAN1", u32 index length, JSON index {nodes, clips: [{name, duration,
    o, n}]}, then the zlib blobs (offset o, length n, counted from the end of the index).

    A blob holds: u16 time arrays, each u16 count + count × u16 milliseconds (delta coded); u16 tracks,
    each u16 node, u8 path (0 rotation, 1 translation, 2 scale), u16 time array, then the keys:
    rotations as count × 4 × i16 (normalised, delta coded), the rest as f32×3 minimum, f32×3 step and
    count × 3 × u16 (delta coded). Tracks that just hold the bind pose are left out."""
    import numpy as np
    nodes = doc["nodes"]
    names, node_ids, clips, blobs, seen = [], {}, [], bytearray(), set()
    for anim in doc.get("animations", []):
        name = anim.get("name", "").rsplit("/", 1)[-1]
        # Only animation graph clips (named by their file path); the model's legacy sequences are older copies.
        if "/" not in anim.get("name", "") or (anim.get("extras") or {}).get("additive") or name in seen:
            continue
        seen.add(name)
        times, tracks, duration = {}, [], 0.0
        for ch in anim["channels"]:
            node, path = ch["target"]["node"], ch["target"]["path"]
            if path not in PATHS:
                continue
            s = anim["samplers"][ch["sampler"]]
            t, o = accessor(doc, binary, s["input"])[:, 0], accessor(doc, binary, s["output"]).astype(np.float64)
            duration = max(duration, float(t.max()))
            n = nodes[node]
            rest = np.array(n.get(path, {"rotation": [0, 0, 0, 1], "translation": [0, 0, 0], "scale": [1, 1, 1]}[path]))
            if np.ptp(o, axis=0).max() < 1e-5:
                if np.abs(o[0] - rest).max() < 1e-4 or path == "rotation" and np.abs(o[0] + rest).max() < 1e-4:
                    continue
                t, o = t[:1], o[:1]
            ms = np.round(t * 1000).astype(np.uint16)
            ti = times.setdefault(ms.tobytes(), len(times))
            if node not in node_ids:
                node_ids[node] = len(names)
                names.append(n.get("name", f"node{node}"))
            tracks.append((node_ids[node], PATHS[path], ti, o))
        if not tracks:
            continue
        out = bytearray(struct.pack("<H", len(times)))
        for key in times:
            ms = np.frombuffer(key, np.uint16)
            out += struct.pack("<H", len(ms)) + np.diff(ms, prepend=np.uint16(0)).astype(np.uint16).tobytes()
        out += struct.pack("<H", len(tracks))
        for node, path, ti, o in tracks:
            out += struct.pack("<HBH", node, path, ti)
            if path == 0:
                q = np.round(np.clip(o, -1, 1) * 32767).astype(np.int16)
                out += np.diff(q, axis=0, prepend=np.zeros((1, 4), np.int16)).astype(np.int16).tobytes()
            else:
                lo, hi = o.min(axis=0), o.max(axis=0)
                step = np.where(hi > lo, (hi - lo) / 65535, 1.0)
                q = np.round((o - lo) / step).astype(np.uint16)
                out += lo.astype(np.float32).tobytes() + step.astype(np.float32).tobytes()
                out += np.diff(q, axis=0, prepend=np.zeros((1, 3), np.uint16)).astype(np.uint16).tobytes()
        blob = zlib.compress(bytes(out), 9)
        clips.append({"name": name, "duration": round(duration, 4), "o": len(blobs), "n": len(blob)})
        blobs += blob
    index = json.dumps({"nodes": names, "clips": clips}, separators=(",", ":")).encode()
    return b"ZAN1" + struct.pack("<I", len(index)) + index + bytes(blobs), len(clips)


def pack(doc: dict, binary: bytes, folder: Path) -> bytes:
    """One self-contained GLB: the PNGs Source2Viewer writes next to it embedded and shrunk, and every
    buffer view that is no longer used left out."""
    from PIL import Image
    views = doc.get("bufferViews", [])
    accessors = doc.get("accessors", [])
    color_images = {doc["textures"][m["pbrMetallicRoughness"]["baseColorTexture"]["index"]].get("source")
                    for m in doc.get("materials", []) if "baseColorTexture" in m.get("pbrMetallicRoughness", {})}
    normal_images = {doc["textures"][m["normalTexture"]["index"]].get("source")
                     for m in doc.get("materials", []) if "normalTexture" in m}

    # Rebuild the binary chunk from scratch: only what the remaining parts point at.
    new_bin, new_views, view_map = bytearray(), [], {}

    def add_blob(blob: bytes) -> int:
        while len(new_bin) % 4:
            new_bin.append(0)
        new_views.append({"buffer": 0, "byteOffset": len(new_bin), "byteLength": len(blob)})
        new_bin.extend(blob)
        return len(new_views) - 1

    def remap_view(i: int) -> int:
        if i not in view_map:
            v = views[i]
            start = v.get("byteOffset", 0)
            view_map[i] = add_blob(binary[start:start + v["byteLength"]])
            for k in ("byteStride", "target"):
                if k in v:
                    new_views[view_map[i]][k] = v[k]
        return view_map[i]

    used = set()
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            used.update(prim.get("attributes", {}).values())
            if "indices" in prim:
                used.add(prim["indices"])
            for target in prim.get("targets", []):
                used.update(target.values())
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            used.add(skin["inverseBindMatrices"])

    acc_map, new_accessors = {}, []
    for i in sorted(used):
        a = dict(accessors[i])
        if "bufferView" in a:
            a["bufferView"] = remap_view(a["bufferView"])
        acc_map[i] = len(new_accessors)
        new_accessors.append(a)
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            prim["attributes"] = {k: acc_map[v] for k, v in prim.get("attributes", {}).items()}
            if "indices" in prim:
                prim["indices"] = acc_map[prim["indices"]]
            if "targets" in prim:
                prim["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in prim["targets"]]
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = acc_map[skin["inverseBindMatrices"]]

    for n, image in enumerate(doc.get("images", [])):
        if "uri" in image:
            img_path = folder / image.pop("uri")
            if not img_path.exists():
                continue
            raw = img_path.read_bytes()
        elif "bufferView" in image:
            v = views[image["bufferView"]]
            raw = binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        else:
            continue
        with Image.open(io.BytesIO(raw)) as img:
            size = COLOR_SIZE if n in color_images else NORMAL_SIZE if n in normal_images else TEXTURE_SIZE
            img.thumbnail((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            if img.mode in ("RGBA", "LA") and img.getchannel("A").getextrema()[0] < 255:
                img.save(buf, "PNG", optimize=True)
                image["mimeType"] = "image/png"
            else:
                # Full chroma for colour (sharp edges between painted areas), standard for the data maps.
                img.convert("RGB").save(buf, "JPEG", quality=92, subsampling=0 if n in color_images else 2)
                image["mimeType"] = "image/jpeg"
        image["bufferView"] = add_blob(buf.getvalue())
        image.pop("name", None)

    for material in doc.get("materials", []):
        material.pop("extras", None)
    for part in doc.get("meshes", []) + doc.get("nodes", []):
        if "name" in part:  # names carry the absolute path of the temporary input file
            part["name"] = part["name"].rsplit("/", 1)[-1]
    doc["accessors"], doc["bufferViews"] = new_accessors, new_views
    doc["buffers"] = [{"byteLength": len(new_bin)}]
    return write_glb(doc, bytes(new_bin))


def convert_ability_icon(vrf: str, citadel: Path, path: str, tmp: Path) -> bytes | None:
    """The game's own icon, lossless at its own size: the page scales it to the size asked for."""
    src = citadel / path
    if not src.exists():
        return None
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run([vrf, "-i", str(src), "-o", str(tmp / "icon.png"), "-d"], capture_output=True, text=True)
    pngs = sorted(tmp.rglob("*.png"))
    if not pngs:
        return None
    from PIL import Image
    with Image.open(pngs[0]) as img:
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def convert_ui_asset(vrf: str, citadel: Path, path: str, tmp: Path) -> bytes | None:
    """A Panorama image as the game ships it: SVG for .vsvg, lossless PNG for textures."""
    src = citadel / path
    if not src.exists():
        return None
    if path.endswith(".vtex_c"):
        return convert_ability_icon(vrf, citadel, path, tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run([vrf, "-i", str(src), "-o", str(tmp / "out.svg"), "-d"], capture_output=True, text=True)
    outs = [f for f in tmp.rglob("*") if f.is_file()]
    svg = next((f for f in outs if f.suffix == ".svg"), outs[0] if outs else None)
    if svg is None:
        return None
    data = svg.read_bytes()
    return data if b"<svg" in data[:2000] else None


def convert_icon(vrf: str, citadel: Path, path: str, tmp: Path) -> bytes | None:
    src = citadel / path
    if not src.exists():
        return None
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run([vrf, "-i", str(src), "-o", str(tmp / "icon.png"), "-d"], capture_output=True, text=True)
    pngs = sorted(tmp.rglob("*.png"))
    if not pngs:
        return None
    from PIL import Image
    with Image.open(pngs[0]) as img:
        img = img.convert("RGBA")
        img.thumbnail((ICON_HEIGHT * 2, ICON_HEIGHT))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=88)
    return buf.getvalue()


# ── Main ───────────────────────────────────────────────

def latest_build() -> dict:
    if not (REPO / ".git").exists():
        sync_repo()
    return build_commits()[0]


def main() -> int:
    if "--fetch" in sys.argv:
        return fetch(Path(sys.argv[sys.argv.index("--fetch") + 1]))
    if not (os.environ.get("STEAM_USERNAME") and os.environ.get("STEAM_PASSWORD")):
        log("STEAM_USERNAME / STEAM_PASSWORD are not set: skipping heroes")
        return 0
    if "--raw" in sys.argv:
        i = sys.argv.index("--raw")
        return raw(Path(sys.argv[i + 1]), sys.argv[i + 2])

    build = latest_build()
    crcs = pak_crcs(build["sha"])
    heroes = hero_list(build["sha"])
    index_path, deps_path = OUT / "index.json", OUT / "deps.json"
    old = {h["key"]: h for h in json.loads(index_path.read_text("utf-8"))["heroes"]} if index_path.exists() else {}
    deps = json.loads(deps_path.read_text("utf-8")) if deps_path.exists() else {}
    (OUT / "icons").mkdir(parents=True, exist_ok=True)

    todo = [h for h in heroes if h["key"] not in old or not deps.get(h["key"])
            or old[h["key"]].get("hash") != fingerprint(deps[h["key"]], crcs)
            or not (OUT / "icons" / f"{h['key']}.webp").exists()]
    if only := os.environ.get("HEROES_ONLY"):  # asked for by hand: convert them again even if nothing changed
        todo = [h for h in heroes if h["key"] in only.split(",")]
    todo = todo[:int(os.environ.get("HEROES_PER_RUN", "0")) or len(todo)]
    log(f"build {build['build']}: {len(heroes)} heroes, {len(todo)} to convert")
    # Ability icons are converted on their own: they change more often than the models.
    (OUT / "abilities").mkdir(parents=True, exist_ok=True)
    icon_crcs = deps.setdefault("_abilities", {})
    icons = [a for h in heroes for a in h["abilities"]
             if icon_crcs.get(a["key"]) != crcs.get(a["image"]) or not (OUT / "abilities" / f"{a['key']}.png").exists()]
    log(f"{len(icons)} ability icons to convert")
    (OUT / "ui").mkdir(parents=True, exist_ok=True)
    ui = {name: path for name, path in UI_ASSETS.items()
          if icon_crcs.get(name) != crcs.get(path) or not (OUT / "ui" / name).exists()}

    if todo or icons or ui:
        manifest = manifest_id(build["sha"])
        sfd, vrf = tool_paths()[0], vrf_path()
        work = Path(tempfile.mkdtemp(prefix="heroes-"))
        try:
            files = SteamFiles(sfd, work, [manifest])
            # Each SteamFileDownloader call is a Steam login: fetch every hero together, level by level.
            fetch_all(files, {h["model"] for h in todo} | {h["card"] for h in todo}
                      | {p for h in todo for p in clip_paths(h["model"], crcs)} | {a["image"] for a in icons}
                      | set(ui.values()))
            citadel = game_root(files, manifest, build["sha"], work)
            for name, path in ui.items():
                blob = convert_ui_asset(vrf, citadel, path, work / "tmp" / f"ui-{name}")
                if blob:
                    (OUT / "ui" / name).write_bytes(blob)
                    icon_crcs[name] = crcs.get(path)
                else:
                    log(f"  could not convert {path}")
            for a in icons:
                png = convert_ability_icon(vrf, citadel, a["image"], work / "tmp" / f"ability-{a['key']}")
                if png:
                    (OUT / "abilities" / f"{a['key']}.png").write_bytes(png)
                    icon_crcs[a["key"]] = crcs.get(a["image"])
            existing = {a["name"] for a in json.loads(gh("release", "view", HEROES_TAG, "--json", "assets").stdout)["assets"]} \
                if gh("release", "view", HEROES_TAG, check=False).returncode == 0 else None
            if existing is None:
                gh("release", "create", HEROES_TAG, "--title", "Модели героев", "--prerelease", "--latest=false",
                   "--notes", "3D-модели героев с анимациями для вкладки «Герои». Обновляется автоматически.")
                existing = set()
            for h in todo:
                key = h["key"]
                log(f"{h['name']}: converting {h['model']}")
                icon = convert_icon(vrf, citadel, h["card"], work / "tmp" / f"{key}-icon")
                if icon:
                    (OUT / "icons" / f"{key}.webp").write_bytes(icon)
                # The animation graph names the clips, wherever they live.
                used = reachable(citadel, {h["model"], h["card"]} | clip_paths(h["model"], crcs))
                made = convert_model(vrf, citadel, h["model"], clip_names(used), work / "tmp" / key)
                if not made:
                    continue
                glb, anims, count = made
                deps[key] = used
                digest = fingerprint(used, crcs)
                entry = {"hash": digest, "file": f"{key}-{digest}.glb", "anims": f"{key}-{digest}.anim",
                         "bytes": len(glb) + len(anims), "animations": count}
                for name, blob in ((entry["file"], glb), (entry["anims"], anims)):
                    (work / name).write_bytes(blob)
                    gh("release", "upload", HEROES_TAG, str(work / name), "--clobber")
                for k in ("file", "anims"):
                    stale = old.get(key, {}).get(k)
                    if stale and stale != entry[k] and stale in existing:
                        gh("release", "delete-asset", HEROES_TAG, stale, "--yes", check=False)
                old[key] = entry
                log(f"  model {len(glb) / 1e6:.1f} MB, {count} animations {len(anims) / 1e6:.1f} MB")
                write_index(index_path, deps_path, build, heroes, old, deps)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    write_index(index_path, deps_path, build, heroes, old, deps)
    return 0


def reachable(citadel: Path, roots: set[str]) -> list[str]:
    """Files are fetched for all heroes together: the ones this hero's model is built from."""
    seen, todo = set(), list(roots)
    while todo:
        p = todo.pop()
        if p in seen or not (citadel / p).exists():
            continue
        seen.add(p)
        if not p.endswith(".vtex_c"):
            todo.extend(r if r.endswith("_c") else r + "_c" for r in references((citadel / p).read_bytes())
                        if "." in r.rsplit("/", 1)[-1] and not r.endswith(NOT_FOLLOWED))
    return sorted(seen)


def write_index(index_path: Path, deps_path: Path, build: dict, heroes: list[dict], made: dict, deps: dict) -> None:
    index = {"build": build["build"], "sha": build["sha"], "tag": HEROES_TAG, "heroes": []}
    for h in heroes:
        entry = {"key": h["key"], "id": h["id"], "name": h["name"]}
        if (OUT / "icons" / f"{h['key']}.webp").exists():
            entry["icon"] = f"{h['key']}.webp"
        entry["abilities"] = [{"key": a["key"], "slot": a["slot"], "name": a["name"], "icon": f"{a['key']}.png"}
                              for a in h["abilities"] if (OUT / "abilities" / f"{a['key']}.png").exists()]
        entry |= made.get(h["key"], {})
        index["heroes"].append(entry)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
    deps_path.write_text(json.dumps(deps, separators=(",", ":"), sort_keys=True), "utf-8")


def raw(dest: Path, key: str) -> int:
    """Debug: the game files of one hero, as the converter sees them."""
    build = latest_build()
    crcs = pak_crcs(build["sha"])
    hero = next(h for h in hero_list(build["sha"]) if h["key"] == key)
    manifest = manifest_id(build["sha"])
    files = SteamFiles(tool_paths()[0], dest, [manifest])
    fetch_all(files, {hero["model"], hero["card"]} | clip_paths(hero["model"], crcs))
    game_root(files, manifest, build["sha"], dest)
    log(f"{len(files.requested)} files requested")
    return 0


def fetch(dest: Path) -> int:
    """Download the models listed in data/heroes/index.json into dest/."""
    index_path = OUT / "index.json"
    if not index_path.exists():
        return 0
    files = [h[k] for h in json.loads(index_path.read_text("utf-8"))["heroes"] for k in ("file", "anims") if h.get(k)]
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        res = gh("release", "download", HEROES_TAG, "--pattern", name, "--dir", str(dest), "--clobber", check=False)
        if res.returncode:
            log(f"{name}: {res.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
