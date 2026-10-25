"""Before/after media for patch summaries: 3D models (GLB) and textures (WebP).

For each recent build, picks the most visible changed models and textures from its
summary, downloads just those files (and the materials and textures they use) for
the build and the one before it, converts them, and uploads them to a GitHub release
named after the build. Only the newest MEDIA_BUILDS builds keep their media: older
releases are deleted, so nothing heavy ever lands in git history.

    python -m backend.media                # add media to new builds, prune old ones
    python -m backend.media --fetch DIR    # download the kept media into DIR (for the site build)

Needs STEAM_USERNAME / STEAM_PASSWORD of an account that owns Deadlock (Steam Guard off),
and the gh CLI with a token that can write releases.
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
import tarfile
import tempfile
from pathlib import Path

from . import config
from .patches import OUT, REPO, git, show, sync_repo

APP, DEPOT = "1422450", "1422456"
VPK = "game/citadel/pak01_dir.vpk"
MEDIA_BUILDS = int(os.environ.get("MEDIA_BUILDS", "10"))
BUDGET = int(os.environ.get("MEDIA_BUDGET_MB", "600")) * 1024 * 1024
MEDIA_VERSION = 2  # bump to redo the media of the kept builds after changing what is picked
PER_RUN = int(os.environ.get("MEDIA_PER_RUN", "3"))  # each build is a few Steam logins; Steam rate limits them
MAX_MODELS = 20
MAX_TEXTURES = 1000  # a safety cap: normally every changed texture is shown
MAX_GLB = 25 * 1024 * 1024
TEXTURE_SIZE = 1024
TOOLS = Path(os.environ.get("MEDIA_TOOLS_DIR", config.ROOT / "data" / "tools"))
TOOLS_RELEASE = ("SteamTracking/GameTracking", "tools", "tools-linux-x64.tar.gz")


def log(msg: str) -> None:
    print(msg, flush=True)


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {res.stderr.strip()}")
    return res


# ── Tools ──────────────────────────────────────────────

def tool_paths() -> tuple[str, str]:
    """SteamFileDownloader and Source2Viewer-CLI: from env, else SteamDB's prebuilt Linux tools."""
    sfd, vrf = os.environ.get("SFD_PATH"), os.environ.get("VRF_PATH")
    if sfd and vrf:
        return sfd, vrf
    exe = TOOLS / "exe"
    if not exe.exists():
        TOOLS.mkdir(parents=True, exist_ok=True)
        repo, tag, asset = TOOLS_RELEASE
        gh("release", "download", tag, "--repo", repo, "--pattern", asset, "--dir", str(TOOLS), "--clobber")
        with tarfile.open(TOOLS / asset) as tar:
            tar.extractall(TOOLS, filter="tar")
        (TOOLS / asset).unlink()
    return str(exe / "SteamFileDownloader" / "SteamFileDownloader"), str(exe / "Source2Viewer" / "Source2Viewer-CLI")


# ── What to show ───────────────────────────────────────

SKIP_MODEL = re.compile(r"(^|/)(n\d+_lr\d|.*nomerge|.*_physbox|.*_collision)")
# Data maps of materials (normals, roughness, masks…): shown too, but folded away on the page.
SERVICE_TEXTURE = re.compile(r"(normal|rough|ambientocclusion|_ao_|_ao\.|metal|height|mask|trans|selfillum|_rma|_orm|spec|_flow|_noise|_ramp)")


def pick(summary: dict) -> list[dict]:
    """Changed models worth a 3D view (most visible subjects first) and every changed texture."""
    models, textures = [], []
    for subject in summary["subjects"]:
        for group in subject["groups"]:
            for f in sorted(group["files"], key=lambda f: f["s"] != "a"):
                path, status = f["p"], f["s"]
                item = {"path": path, "status": status, "subject": subject["title"]}
                if path.endswith(".vmdl_c") and status != "r" and subject["visual"] and not SKIP_MODEL.search(path):
                    models.append(item | {"kind": "model"})
                elif path.endswith(".vtex_c"):
                    service = bool(SERVICE_TEXTURE.search(path.rsplit("/", 1)[-1]))
                    textures.append(item | {"kind": "texture", "service": service})
    # Icons and hero art first, then textures of models, particle sprites, data maps last.
    textures.sort(key=lambda i: (i["service"], not i["path"].startswith("panorama/"), "/particle" in i["path"]))
    return models[:MAX_MODELS] + textures[:MAX_TEXTURES]


# ── Steam downloads ────────────────────────────────────

def manifest_id(sha: str) -> str | None:
    m = re.search(r"Manifest ID / date\s*:\s*(\d+)", show(sha, f"manifests/manifest_{DEPOT}.txt"))
    return m[1] if m else None


class SteamFiles:
    """Downloads VPK entries of two builds into <work>/depots/<depot>/<manifest>/."""

    def __init__(self, sfd: str, work: Path, manifests: list[str]):
        self.sfd, self.out, self.manifests = sfd, work / "depots", manifests
        self.requested: set[str] = set()

    def root(self, manifest: str) -> Path:
        return self.out / DEPOT / manifest / "game" / "citadel" / "pak01_dir"

    def get(self, paths: set[str]) -> None:
        paths = sorted(p for p in paths if p not in self.requested)
        if not paths:
            return
        self.requested.update(paths)
        cmd = [self.sfd, "get", APP, *(f"{DEPOT}:{m}" for m in self.manifests), "--output", str(self.out),
               "--username", os.environ["STEAM_USERNAME"], "--password", os.environ["STEAM_PASSWORD"],
               "--", *(f"{VPK}:{p}" for p in paths)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        # 5: an old manifest is no longer served, 6: some path is gone. Both still download the rest.
        if res.returncode not in (0, 5, 6):
            raise RuntimeError(f"SteamFileDownloader exit {res.returncode}: {res.stderr.strip()[-800:]}")
        if res.returncode == 5:
            log("  [warn] Steam did not give access to one of the manifests (old build?)")

    def fetch_with_dependencies(self, paths: set[str]) -> None:
        """Models need their materials, materials their textures: follow references a few levels down."""
        wanted = set(paths)
        for _ in range(4):
            self.get(wanted)
            wanted = set()
            for manifest in self.manifests:
                root = self.root(manifest)
                for p in self.requested:
                    f = root / p
                    if f.exists() and f.suffix in (".vmdl_c", ".vmat_c"):
                        wanted.update(r + "_c" for r in references(f.read_bytes())
                                      if r.endswith((".vmat", ".vtex", ".vmdl")))
            wanted -= self.requested
            if not wanted:
                return


def references(data: bytes) -> list[str]:
    """Resource names from the RERL block of a compiled Source 2 resource."""
    try:
        _, _, _, block_offset, count = struct.unpack_from("<IHHII", data, 0)
        pos = 8 + block_offset
        for _ in range(count):
            offset, _size = struct.unpack_from("<II", data, pos + 4)
            if data[pos:pos + 4] == b"RERL":
                start = pos + 4 + offset
                rel, n = struct.unpack_from("<II", data, start)
                out = []
                for j in range(n):
                    entry = start + rel + j * 16
                    name_at = entry + 8 + struct.unpack_from("<Q", data, entry + 8)[0]
                    out.append(data[name_at:data.index(b"\0", name_at)].decode("utf-8", "replace"))
                return out
            pos += 12
    except (struct.error, ValueError):
        pass
    return []


# ── Conversion ─────────────────────────────────────────

def game_root(files: SteamFiles, manifest: str, sha: str, work: Path) -> Path:
    """Source2Viewer resolves dependencies through gameinfo.gi search paths: game/citadel/<path>."""
    citadel = work / f"root-{manifest}" / "game" / "citadel"
    if not citadel.exists():
        # Downloads are done by now; a symlink would not do, the loader resolves it back to pak01_dir.
        citadel.parent.mkdir(parents=True)
        content = files.root(manifest)
        content.mkdir(parents=True, exist_ok=True)
        content.rename(citadel)
        (citadel / "gameinfo.gi").write_text(show(sha, "game/citadel/gameinfo.gi"), "utf-8")
    return citadel


def convert_model(vrf: str, citadel: Path, path: str, tmp: Path) -> bytes | None:
    src = citadel / path
    if not src.exists():
        return None
    tmp.mkdir(parents=True)
    out = tmp / "model.glb"
    subprocess.run([vrf, "-i", str(src), "--game", str(citadel / "gameinfo.gi"), "-o", str(out), "-d",
                    "--gltf_export_format", "glb", "--gltf_export_materials", "--gltf_textures_adapt"], capture_output=True, text=True)
    if not out.exists():
        return None
    data = pack_glb(out)
    return data if len(data) <= MAX_GLB else None


def convert_texture(vrf: str, citadel: Path, path: str, tmp: Path) -> bytes | None:
    src = citadel / path
    if not src.exists():
        return None
    tmp.mkdir(parents=True)
    # The CLI writes the PNG into the folder of the -o path, under the texture's own name.
    subprocess.run([vrf, "-i", str(src), "-o", str(tmp / "texture.png"), "-d"], capture_output=True, text=True)
    pngs = sorted(tmp.rglob("*.png"))
    if not pngs:
        return None
    from PIL import Image
    with Image.open(pngs[0]) as img:
        img.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=85)
    return buf.getvalue()


def pack_glb(path: Path) -> bytes:
    """Embed the PNGs Source2Viewer writes next to a .glb, shrunk, so one small file holds the model."""
    from PIL import Image
    data = path.read_bytes()
    json_len = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + json_len])
    rest = 20 + json_len
    binary = bytearray(data[rest + 8:rest + 8 + struct.unpack_from("<I", data, rest)[0]]) if rest < len(data) else bytearray()

    for image in doc.get("images", []):
        uri = image.pop("uri", None)
        if not uri or uri.startswith("data:"):
            continue
        img_path = path.parent / uri
        if not img_path.exists():
            continue
        with Image.open(img_path) as img:
            img.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
            buf = io.BytesIO()
            if img.mode in ("RGBA", "LA") and img.getchannel("A").getextrema()[0] < 255:
                img.save(buf, "PNG", optimize=True)
                image["mimeType"] = "image/png"
            else:
                img.convert("RGB").save(buf, "JPEG", quality=82)
                image["mimeType"] = "image/jpeg"
        while len(binary) % 4:
            binary.append(0)
        doc.setdefault("bufferViews", []).append({"buffer": 0, "byteOffset": len(binary), "byteLength": buf.tell()})
        image["bufferView"] = len(doc["bufferViews"]) - 1
        binary += buf.getvalue()
    for material in doc.get("materials", []):
        material.pop("extras", None)  # Source 2 shader parameters, not needed to render
    if binary:
        while len(binary) % 4:
            binary.append(0)
        doc.setdefault("buffers", [{}])[0]["byteLength"] = len(binary)

    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    chunks = struct.pack("<II", len(js), 0x4E4F534A) + js
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + bytes(binary)
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


# ── Per build ──────────────────────────────────────────

def asset_name(path: str, side: str, kind: str) -> str:
    return f"{hashlib.sha1(path.encode()).hexdigest()[:12]}-{side}.{'glb' if kind == 'model' else 'webp'}"


def make_media(summary: dict, sfd: str, vrf: str) -> tuple[str, dict, int] | None:
    items = pick(summary)
    if not items:
        return None
    old, new = manifest_id(summary["parent"]), manifest_id(summary["sha"])
    if not new:
        return None
    work = Path(tempfile.mkdtemp(prefix=f"media-{summary['build']}-"))
    try:
        sides = [("after", new, summary["sha"])] + ([("before", old, summary["parent"])] if old and old != new else [])
        files = SteamFiles(sfd, work, [m for _, m, _ in sides])
        files.fetch_with_dependencies({i["path"] for i in items})

        out_dir, media = work / "out", {}
        out_dir.mkdir()
        for n, item in enumerate(items):
            entry = {"kind": item["kind"], "subject": item["subject"], "status": item["status"]}
            if item.get("service"):
                entry["service"] = True
            for side, manifest, sha in sides:
                if side == "before" and item["status"] == "a" or side == "after" and item["status"] == "r":
                    continue
                citadel = game_root(files, manifest, sha, work)
                convert = convert_model if item["kind"] == "model" else convert_texture
                blob = convert(vrf, citadel, item["path"], work / "tmp" / f"{n}-{side}")
                if blob:
                    name = asset_name(item["path"], side, item["kind"])
                    (out_dir / name).write_bytes(blob)
                    entry[side] = name
            if "after" in entry or "before" in entry:
                media[item["path"]] = entry
        if not media:
            return None

        tag = f"media-{summary['build']}-{summary['sha'][:8]}"
        gh("release", "delete", tag, "--yes", "--cleanup-tag", check=False)
        gh("release", "create", tag, "--title", f"Медиа билда {summary['build']}", "--prerelease", "--latest=false",
           "--notes", "Модели и текстуры «до/после» для страницы «Изменения». Удаляется автоматически.")
        assets = sorted(out_dir.iterdir())
        for i in range(0, len(assets), 50):
            gh("release", "upload", tag, *map(str, assets[i:i + 50]))
        return tag, media, sum(a.stat().st_size for a in assets)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def drop_media(path: Path, summary: dict) -> None:
    if summary.get("mediaTag"):
        gh("release", "delete", summary["mediaTag"], "--yes", "--cleanup-tag", check=False)
    for key in ("media", "mediaTag", "mediaBytes", "mediaDone", "mediaVersion"):
        summary.pop(key, None)
    path.write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")


def main() -> int:
    if "--fetch" in sys.argv:
        return fetch(Path(sys.argv[sys.argv.index("--fetch") + 1]))
    if not (os.environ.get("STEAM_USERNAME") and os.environ.get("STEAM_PASSWORD")):
        log("STEAM_USERNAME / STEAM_PASSWORD are not set: skipping models and textures")
        return 0

    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text("utf-8"))
    builds = index["builds"]
    if not (REPO / ".git").exists():
        sync_repo()

    tools = None
    used = made_now = 0
    stopped = False
    for i, entry in enumerate(builds):
        path = OUT / entry["file"]
        summary = json.loads(path.read_text("utf-8"))
        if i >= MEDIA_BUILDS or used >= BUDGET:
            if summary.get("mediaTag") or entry.get("media"):
                log(f"build {entry['build']}: dropping old media")
                drop_media(path, summary)
                entry.pop("media", None)
            continue
        stale = summary.get("mediaVersion", 1) < MEDIA_VERSION
        if (not summary.get("mediaDone") or stale) and entry["size"] != "empty" and not stopped and made_now < PER_RUN:
            tools = tools or tool_paths()
            log(f"build {entry['build']}: making media")
            made_now += 1
            try:
                made = make_media(summary, *tools)
            except Exception as exc:
                # Login trouble or rate limits: try again on the next run, keep what is done.
                log(f"::warning::build {entry['build']}: media failed: {exc}")
                stopped = True
                continue
            if made:
                summary["mediaTag"], summary["media"], summary["mediaBytes"] = made
                log(f"  {len(made[1])} items, {made[2] / 1e6:.1f} MB")
            else:
                if summary.get("mediaTag"):
                    gh("release", "delete", summary["mediaTag"], "--yes", "--cleanup-tag", check=False)
                for key in ("media", "mediaTag", "mediaBytes"):
                    summary.pop(key, None)
                entry.pop("media", None)
            summary["mediaDone"], summary["mediaVersion"] = True, MEDIA_VERSION
            path.write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")
        used += summary.get("mediaBytes", 0)
        if summary.get("media"):
            entry["media"] = len(summary["media"])

    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return 0


def fetch(dest: Path) -> int:
    """Download the media releases of the builds in data/patches into dest/<tag>/."""
    for path in sorted(OUT.glob("*.json")):
        if path.name == "index.json":
            continue
        tag = json.loads(path.read_text("utf-8")).get("mediaTag")
        if tag:
            res = gh("release", "download", tag, "--dir", str(dest / tag), "--clobber", check=False)
            log(f"{tag}: {'ok' if res.returncode == 0 else res.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
