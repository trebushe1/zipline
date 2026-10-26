"""Our own build tracker: reads new Deadlock builds straight from Steam.

SteamDB's GameTracking repository can lag behind or stop for days. This checks the
latest manifest of the content depot with our Steam account and, when it is new,
downloads the same files GameTracking keeps (file list of pak01, scripts, English
localization, steam.inf) for that build and the previous one. Both snapshots go into
a throwaway git repository, so backend.patches summarises them exactly like SteamDB's
commits.

    python -m backend.tracker

Needs STEAM_USERNAME / STEAM_PASSWORD (see backend/media.py).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import media, patches
from .patches import OUT

FILES_JSON = {media.DEPOT: [
    "game/citadel/steam.inf",
    "game/citadel/gameinfo.gi",
    r"regex:^game/citadel/resource/localization/.+_english\.txt$",
    "game/citadel/pak01_dir.vpk",
    r"game/citadel/pak01_dir.vpk:regex:^scripts/.+\.vdata_c$",
]}


def log(msg: str) -> None:
    print(msg, flush=True)


def steam_login() -> list[str]:
    return ["--username", os.environ["STEAM_USERNAME"], "--password", os.environ["STEAM_PASSWORD"]]


def latest_manifest(sfd: str) -> str:
    res = subprocess.run([sfd, "depots", media.APP, *steam_login()], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        m = re.match(rf"{media.APP}/{media.DEPOT}:(\d+)\t", line)
        if m:
            return m[1]
    raise RuntimeError(f"no manifest for depot {media.DEPOT} (exit {res.returncode}): {res.stderr.strip()[-600:]}")


def snapshot(sfd: str, vrf: str, manifest: str, out: Path) -> None:
    """GameTracking's layout for one build: game/citadel/... and manifests/manifest_<depot>.txt."""
    out.mkdir(parents=True)
    (out / "files.json").write_text(json.dumps(FILES_JSON), "utf-8")
    pinned = out.parent / f"pin-{manifest}"
    pinned.mkdir()
    (pinned / f"manifest_{media.DEPOT}.txt").write_text(f"Manifest ID / date     : {manifest} / -\n", "utf-8")
    res = subprocess.run([sfd, "track", media.APP, "--output", ".", "--manifests", str(pinned), "--save-manifest",
                          *steam_login()], cwd=out, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"track {manifest} exit {res.returncode}: {res.stderr.strip()[-800:]}")
    vpk = out / "game/citadel/pak01_dir.vpk"
    listing = subprocess.run([vrf, "--input", str(vpk), "--vpk_list"], capture_output=True, text=True)
    (out / patches.PAK_LIST).write_text(listing.stdout, "utf-8")
    subprocess.run([vrf, "--input", str(vpk), "--output", str(out / "game/citadel/pak01_dir"), "--vpk_decompile",
                    "--vpk_extensions", "vdata_c", "--vpk_filepath", "scripts/"], capture_output=True, text=True)
    # Keep only what GameTracking has: no archives, no download bookkeeping.
    for f in list(out.rglob("*")):
        if f.is_file() and (f.suffix == ".vpk" or f.name in ("files.json", "steam_buildid.txt")):
            f.unlink()
    saved = out / "manifests" / f"manifest_{media.DEPOT}.txt"
    if not saved.exists():
        saved.parent.mkdir(exist_ok=True)
        saved.write_text(f"Content Manifest for Depot {media.DEPOT}\n\nManifest ID / date     : {manifest} / -\n", "utf-8")


def client_version(folder: Path) -> int:
    m = re.search(r"^ClientVersion=(\d+)", (folder / "game/citadel/steam.inf").read_text("utf-8", "replace"), re.M)
    return int(m[1]) if m else 0


def commit_tree(repo: Path, tree: Path, message: str) -> str:
    for f in repo.iterdir():
        if f.name != ".git":
            shutil.rmtree(f) if f.is_dir() else f.unlink()
    shutil.copytree(tree, repo, dirs_exist_ok=True)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("add", "-A")
    git("-c", "user.name=zipline", "-c", "user.email=zipline@localhost", "commit", "-q", "--allow-empty", "-m", message)
    return git("rev-parse", "HEAD").strip()


def previous_manifest(index: dict) -> str | None:
    """The newest build we know: our own last one, else SteamDB's latest commit."""
    own = [b for b in index.get("builds", []) if b.get("manifest")]
    if own:
        return max(own, key=lambda b: b["time"])["manifest"]
    if not (patches.REPO / ".git").exists():
        patches.sync_repo()
    return media.manifest_id("HEAD")


def main() -> int:
    if not (os.environ.get("STEAM_USERNAME") and os.environ.get("STEAM_PASSWORD")):
        log("STEAM_USERNAME / STEAM_PASSWORD are not set: skipping the Steam build check")
        return 0
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text("utf-8"))
    sfd, vrf = media.tool_paths()

    new = latest_manifest(sfd)
    old = previous_manifest(index)
    known = {b.get("manifest") for b in index["builds"]}
    if new == old or new in known:
        log(f"no new build on Steam (manifest {new})")
        return 0
    log(f"new build on Steam: manifest {new} (previous {old})")

    work = Path(tempfile.mkdtemp(prefix="tracker-"))
    try:
        snapshot(sfd, vrf, old, work / "old")
        snapshot(sfd, vrf, new, work / "new")
        repo = work / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        old_build, new_build = client_version(work / "old"), client_version(work / "new")
        parent = commit_tree(repo, work / "old", f"{old_build} | previous build")
        sha = commit_tree(repo, work / "new", f"{new_build} | from Steam")

        patches.REPO = repo
        commit = {"sha": sha, "parent": parent, "build": new_build, "time": int(time.time() * 1000)}
        summary = patches.summarise(commit)
        # This commit only lives in this run: keep what later steps need to find the build again.
        summary |= {"manifests": {"before": old, "after": new}, "gameinfo": (work / "new/game/citadel/gameinfo.gi")
                    .read_text("utf-8", "replace"), "diffUrl": None}
        file = f"{new_build}-m{new[-8:]}.json"
        (OUT / file).write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")
        entry = patches.entry(summary, file) | {"manifest": new}
        index["builds"] = sorted(index["builds"] + [entry], key=lambda b: -b["time"])[:patches.KEEP_BUILDS]
        index["updatedAt"] = int(time.time() * 1000)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
        log(f"build {new_build}: {summary['size']}, {summary['headline'][:2]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
