"""Hero gallery: icons and animated 3D models of every playable Deadlock hero.

Reads the hero list from the newest build in SteamDB's GameTracking-Deadlock, downloads
each hero's model with everything it uses (meshes, materials, textures, skeleton,
animation graph and clips) straight from Steam, converts it with Source2Viewer-CLI into
one GLB with all animations, and uploads it to the GitHub release HEROES_TAG. Icons go
to data/heroes/icons/ (small, kept in git). A hero is converted again only when one of
its files changed.

    python -m backend.heroes                  # convert new and changed heroes
    python -m backend.heroes --fetch DIR      # download the models into DIR (for the site build)
    python -m backend.heroes --raw DIR HERO   # debug: download one hero's game files into DIR

Needs STEAM_USERNAME / STEAM_PASSWORD (see backend/media.py) and the gh CLI.
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
from pathlib import Path

from . import config
from .media import DEPOT, TOOLS, SteamFiles, game_root, gh, log, manifest_id, references, tool_paths
from .patches import HEROES_VDATA, PAK_LIST, REPO, build_commits, load_loc, show, sync_repo

OUT = config.ROOT / "data" / "heroes"
HEROES_TAG = "heroes"
MODEL_VERSION = 1  # bump to convert every hero again after changing the conversion
VRF_VERSION = "20.1"  # first release that exports Deadlock's animation graph clips to glTF
COLOR_SIZE = 2048  # base color: details matter when zoomed in
TEXTURE_SIZE = 1024  # normal, roughness and other maps
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


def hero_list(sha: str) -> list[dict]:
    """Playable heroes in the build: not disabled, not in development, with a model and a card."""
    text, loc = show(sha, HEROES_VDATA), load_loc(sha)
    starts = [(m.start(), m[1]) for m in HERO_BLOCK.finditer(text)]
    heroes = []
    for i, (pos, key) in enumerate(starts):
        block = text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        if key in NOT_HEROES or any(field(block, f) == "true" for f in ("m_bDisabled", "m_bInDevelopment", "m_bNeedsTesting")):
            continue
        model, card = resource(field(block, "m_strModelName")), resource(field(block, "m_strIconHeroCard"))
        if not (model and card):
            continue
        heroes.append({
            "key": key[5:], "id": int(field(block, "m_HeroID") or 0),
            "name": loc.get(f"{key}:n") or key[5:].capitalize(),
            "model": model + "_c",
            # file://{images}/heroes/bull_card.psd → panorama/images/heroes/bull_card_psd.vtex_c
            "card": re.sub(r"^file://\{images\}/", "panorama/images/", card).replace(".", "_") + ".vtex_c",
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


# ── Conversion ─────────────────────────────────────────

def convert_model(vrf: str, citadel: Path, path: str, tmp: Path) -> tuple[bytes, list[str]] | None:
    tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / "model.glb"
    res = subprocess.run([vrf, "-i", str(citadel / path), "--game", str(citadel / "gameinfo.gi"), "-o", str(out), "-d",
                          "--gltf_export_format", "glb", "--gltf_textures_adapt", "--gltf_export_animations"],
                         capture_output=True, text=True)
    if not out.exists():
        log(f"  conversion failed: {(res.stdout + res.stderr).strip()[-600:]}")
        return None
    return pack(out)


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


def keep_animation(anim: dict) -> bool:
    """Additive layers (aim offsets, flinches) only make sense on top of another animation."""
    extras = anim.get("extras") or {}
    name = anim.get("name", "")
    return not (extras.get("additive") or extras.get("isAdditive") or "+non_additive" in name or name.endswith("_additive"))


def pack(path: Path) -> tuple[bytes, list[str]]:
    """One self-contained GLB: textures embedded and shrunk, standalone additive layers dropped, and
    every buffer view that is no longer used left out."""
    from PIL import Image
    doc, binary = read_glb(path.read_bytes())
    views = doc.get("bufferViews", [])
    accessors = doc.get("accessors", [])

    doc["animations"] = [a for a in doc.get("animations", []) if keep_animation(a)]
    color_images = {doc["textures"][m["pbrMetallicRoughness"]["baseColorTexture"]["index"]].get("source")
                    for m in doc.get("materials", []) if "baseColorTexture" in m.get("pbrMetallicRoughness", {})}

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

    used_accessors = set()
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            used_accessors.update(prim.get("attributes", {}).values())
            if "indices" in prim:
                used_accessors.add(prim["indices"])
            for target in prim.get("targets", []):
                used_accessors.update(target.values())
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            used_accessors.add(skin["inverseBindMatrices"])
    for anim in doc["animations"]:
        for s in anim.get("samplers", []):
            used_accessors.update((s["input"], s["output"]))

    acc_map, new_accessors = {}, []
    for i in sorted(used_accessors):
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
    for anim in doc["animations"]:
        for s in anim.get("samplers", []):
            s["input"], s["output"] = acc_map[s["input"]], acc_map[s["output"]]
        anim.pop("extras", None)

    for n, image in enumerate(doc.get("images", [])):
        if "uri" in image:
            img_path = path.parent / image.pop("uri")
            if not img_path.exists():
                continue
            raw = img_path.read_bytes()
        elif "bufferView" in image:
            v = views[image["bufferView"]]
            raw = binary[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
        else:
            continue
        with Image.open(io.BytesIO(raw)) as img:
            size = COLOR_SIZE if n in color_images else TEXTURE_SIZE
            img.thumbnail((size, size))
            buf = io.BytesIO()
            if img.mode in ("RGBA", "LA") and img.getchannel("A").getextrema()[0] < 255:
                img.save(buf, "PNG", optimize=True)
                image["mimeType"] = "image/png"
            else:
                img.convert("RGB").save(buf, "JPEG", quality=85)
                image["mimeType"] = "image/jpeg"
        image["bufferView"] = add_blob(buf.getvalue())
        image.pop("name", None)

    for material in doc.get("materials", []):
        material.pop("extras", None)
    for part in doc.get("meshes", []) + doc.get("nodes", []):
        if "name" in part:
            part["name"] = part["name"].rsplit("/", 1)[-1]
    doc["accessors"], doc["bufferViews"] = new_accessors, new_views
    doc["buffers"] = [{"byteLength": len(new_bin)}]
    return write_glb(doc, bytes(new_bin)), [a.get("name", "") for a in doc["animations"]]


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
    limit = int(os.environ.get("HEROES_PER_RUN", "0")) or len(todo)
    if only := os.environ.get("HEROES_ONLY"):
        todo = [h for h in todo if h["key"] in only.split(",")]
    todo = todo[:limit]
    log(f"build {build['build']}: {len(heroes)} heroes, {len(todo)} to convert")

    if todo:
        manifest = manifest_id(build["sha"])
        sfd, vrf = tool_paths()[0], vrf_path()
        work = Path(tempfile.mkdtemp(prefix="heroes-"))
        try:
            files = SteamFiles(sfd, work, [manifest])
            # Each SteamFileDownloader call is a Steam login: fetch every hero together, level by level.
            fetch_all(files, {h["model"] for h in todo} | {h["card"] for h in todo}
                      | {p for h in todo for p in clip_paths(h["model"], crcs)})
            citadel = game_root(files, manifest, build["sha"], work)
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
                made = convert_model(vrf, citadel, h["model"], work / "tmp" / key)
                if not made:
                    continue
                glb, animations = made
                used = reachable(citadel, {h["model"], h["card"]} | clip_paths(h["model"], crcs))
                deps[key] = used
                digest = fingerprint(used, crcs)
                name = f"{key}-{digest}.glb"
                (work / name).write_bytes(glb)
                gh("release", "upload", HEROES_TAG, str(work / name), "--clobber")
                if old.get(key, {}).get("file") and old[key]["file"] != name and old[key]["file"] in existing:
                    gh("release", "delete-asset", HEROES_TAG, old[key]["file"], "--yes", check=False)
                old[key] = {"hash": digest, "file": name, "bytes": len(glb), "animations": len(animations)}
                log(f"  {len(glb) / 1e6:.1f} MB, {len(animations)} animations")
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
    files = [h["file"] for h in json.loads(index_path.read_text("utf-8"))["heroes"] if h.get("file")]
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        res = gh("release", "download", HEROES_TAG, "--pattern", name, "--dir", str(dest), "--clobber", check=False)
        if res.returncode:
            log(f"{name}: {res.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
