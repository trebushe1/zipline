"""One-off export of map props (candles, barrels, soul crates, planks) as GLB, the same way as the hero
models (backend/heroes.py): files straight from Steam, Source2Viewer-CLI, the game's shader colours baked in.
Also dumps the map's object lists (entities and world nodes) to see which of these props the map uses and where.
Lives only on the temporary props-export branch; the result is a workflow artifact, nothing is committed.

    python -m backend.props_export OUT_DIR
"""
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .heroes import bake_materials, fetch_all, gltfpack_path, hide_off_meshes, latest_build, pack, pak_crcs, read_glb, vrf_path
from .media import SteamFiles, game_root, log, manifest_id, tool_paths
from .patches import GAME, show

CANDLES = [
    "models/candles/candle_1_tintable", "models/candles/candle_2_tintable",
    *(f"models/street/street_candle_01/candle_0{n}_{s}_01" for n in (1, 2, 3) for s in ("small", "medium", "tall")),
    *(f"models/props_table/candle_0{n}" for n in (1, 2, 3)),
    *(f"models/props_endgame/amber_hand_candles/candle0{n}" for n in range(1, 9)),
    *(f"models/props_endgame/amber_hand_candles/candle0{n}_large" for n in (2, 4, 6, 8)),
    "models/props_endgame/amber_hand_candles/candle_cluster_large01",
]
BARRELS = [
    "models/props_gameplay/sake_barrel_01/sake_barrel_01",
    "models/architecture/arch_chinatown_01/chinatown_tea_barrel_01",
    *(f"models/props/barrel_0{n}" for n in (1, 2, 3, 4)),
    "models/props/wooden_barrel_a", "models/props_industrial/barrel_01", "models/props_street/barrel_01",
]
CRATES = [
    "models/items/item_crate", "models/items/item_crate_tough", "models/items/item_crate_tough_2",
    "models/items/crystal_crate",
    "models/props_gameplay/wooden_crate_01", "models/props_gameplay/wooden_crate_02/wooden_crate_02",
    "models/props_gameplay/wooden_crate_03/wooden_crate_03",
    "models/props_gameplay/soul_container/soul_container", "models/props_gameplay/gold_crate",
]
PLANKS = [
    "models/architecture/arch_scaffolding/scaff_planks_a", "models/architecture/arch_scaffolding/scaff_planks_b",
    *(f"models/architecture/arch_scaffolding/scaff_board_{c}" for c in "abc"),
    "models/architecture/arch_factory/plank_bridge_floor", "models/architecture/arch_factory/plank_bridge_floor_single",
    "models/architecture/arch_factory/plywood_scrap_a", "models/architecture/arch_factory/plywood_scrap_b",
    *(f"models/npc/core_amber/core_wood_plank_0{n}" for n in (1, 2, 3, 4)),
    "models/npc/core_amber/core_wood_plank_nail_01",
    "models/props/watertower_planks_a", "models/props/watertower_planks_b",
    "models/props_endgame/amber_hand_base/trash_plank_pile_corner01", "models/props_endgame/amber_hand_base/wood_boards01",
    "models/props_industrial/plywood_sheet_48",
    "models/waterfront/pier_details_01/pier_wood_board_01", "models/waterfront/pier_details_01/pier_wood_board_02",
]
GROUPS = {"candle": CANDLES, "barrel": BARRELS, "crate": CRATES, "plank": PLANKS}
MAP = "dl_midtown"
MAP_LUMPS = (".vents_c", ".vwnod_c", ".vwrld_c")


def main() -> int:
    out = Path(sys.argv[1])
    (out / "models").mkdir(parents=True, exist_ok=True)
    (out / "map").mkdir(parents=True, exist_ok=True)
    build = latest_build()
    crcs = pak_crcs(build["sha"])
    wanted = {p + ".vmdl_c": group for group, paths in GROUPS.items() for p in paths}
    missing = sorted(p for p in wanted if p not in crcs)
    if missing:
        log(f"not in this build: {missing}")
    paths = sorted(p for p in wanted if p in crcs)
    manifest = manifest_id(build["sha"])
    sfd, vrf = tool_paths()[0], vrf_path()
    work = Path(tempfile.mkdtemp(prefix="props-"))
    files = SteamFiles(sfd, work, [manifest])

    fetch_all(files, set(paths))
    # The map's object lists: optional, the models matter more.
    try:
        files.get(set(), {f"{GAME}/maps/{MAP}.vpk:regex:\\.({'|'.join(s[1:] for s in MAP_LUMPS)})$"})
    except Exception as exc:
        log(f"map lumps: {exc!r}")
    lumps = [f for f in (work / "depots").rglob("*") if f.is_file() and f.name.endswith(MAP_LUMPS)]
    log(f"{len(lumps)} map lumps")
    for f in lumps:
        res = subprocess.run([vrf, "-i", str(f), "-b", "DATA"], capture_output=True, text=True)
        name = str(f.relative_to(work / "depots")).replace("/", "__")
        with gzip.open(out / "map" / f"{name}.txt.gz", "wt", encoding="utf-8") as fh:
            fh.write(res.stdout)

    citadel = game_root(files, manifest, show(build["sha"], f"{GAME}/gameinfo.gi"), work)
    info = {}
    for path in paths:
        name = path[len("models/"):-len(".vmdl_c")].replace("/", "__")
        tmp = work / "tmp" / name
        tmp.mkdir(parents=True)
        glb = tmp / "model.glb"
        try:
            res = subprocess.run([vrf, "-i", str(citadel / path), "--game", str(citadel / "gameinfo.gi"), "-o", str(glb), "-d",
                                  "--gltf_export_format", "glb", "--gltf_export_materials", "--gltf_textures_adapt"],
                                 capture_output=True, text=True)
            if not glb.exists():
                log(f"{path}: conversion failed: {(res.stdout + res.stderr).strip()[-400:]}")
                continue
            doc, binary = read_glb(glb.read_bytes())
            info[name] = {"path": path, "group": wanted[path],
                          "materials": [{"name": m.get("name"), "vmat": (m.get("extras") or {}).get("vmat")}
                                        for m in doc.get("materials", [])]}
            hide_off_meshes(doc, vrf, citadel / path)
            binary = bake_materials(doc, binary, vrf, citadel, tmp)
            (tmp / "static.glb").write_bytes(pack(doc, binary, tmp))
            subprocess.run([gltfpack_path(), "-i", str(tmp / "static.glb"), "-o", str(out / "models" / f"{name}.glb"), "-cc", "-kn"],
                           capture_output=True, text=True)
            log(f"{path}: {(out / 'models' / f'{name}.glb').stat().st_size / 1e3:.0f} KB")
        except Exception as exc:  # one broken prop must not stop the rest
            log(f"{path}: {exc!r}")
    (out / "info.json").write_text(json.dumps(info, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
