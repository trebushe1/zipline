"""Patch tracker: what changed in Deadlock's files from build to build.

SteamDB publishes the game's file list (with checksums), its text scripts and
English localization in the SteamDatabase/GameTracking-Deadlock repository —
one commit per Steam build. This module diffs those commits and turns each one
into a readable summary: which heroes, objects and effects changed, which game
strings and ability values moved.

    python -m backend.patches            # fetch the tracking repo, summarise new builds
    python -m backend.patches --rebuild  # summarise the recent builds again from scratch

Output: data/patches/index.json (list of builds) and one JSON file per build.
"""
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from . import config

REPO_URL = "https://github.com/SteamDatabase/GameTracking-Deadlock"
REPO = Path(os.environ.get("GAMETRACKING_DIR", config.ROOT / "data" / "gametracking"))
OUT = config.ROOT / "data" / "patches"
KEEP_BUILDS = int(os.environ.get("PATCH_BUILDS", "30"))
FETCH_DEPTH = 120

GAME = "game/citadel"
PAK_LIST = f"{GAME}/pak01_dir.txt"
LOC_DIR = f"{GAME}/resource/localization"
HEROES_VDATA = f"{GAME}/pak01_dir/scripts/heroes.vdata"
BUILD_MSG = re.compile(r"^(\d+) \| ")

# Per-group caps keep a build file small even when a patch touches 10k files.
MAX_FILES = {"model": 300, "texture": 1500, "material": 300}
MAX_FILES_DEFAULT = 60
MAX_STRINGS = 400
MAX_VDATA_ENTITIES = 250
MAX_VDATA_ROWS = 40


# ── git ────────────────────────────────────────────────

def git(*args: str, check: bool = True) -> str:
    res = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def sync_repo() -> None:
    """Blobless shallow clone: commits and trees now, file contents on demand."""
    if not (REPO / ".git").exists():
        REPO.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", f"--depth={FETCH_DEPTH}",
                        "--single-branch", REPO_URL, str(REPO)], check=True)
        return
    git("fetch", "-q", f"--depth={FETCH_DEPTH}", "origin")
    git("reset", "-q", "--hard", "FETCH_HEAD")


def show(sha: str, path: str) -> str:
    return git("show", f"{sha}:{path}", check=False)


def build_commits() -> list[dict]:
    """Steam build commits, newest first (repo maintenance commits are skipped)."""
    out = git("log", "--format=%H%x1f%ct%x1f%P%x1f%s", f"-{FETCH_DEPTH}")
    commits = []
    for line in out.splitlines():
        sha, ts, parents, subject = line.split("\x1f", 3)
        m = BUILD_MSG.match(subject)
        if m and parents:
            commits.append({"sha": sha, "parent": parents.split()[0], "build": int(m[1]), "time": int(ts) * 1000})
    return commits


# ── File list (pak01_dir.txt) ──────────────────────────

PAK_LINE = re.compile(r"^(.+?) CRC:\w+ size:(\d+)$")

KINDS = {
    "vmdl_c": "model", "vmesh_c": "model", "vphys_c": "model", "vmorf_c": "model",
    "vtex_c": "texture", "vmat_c": "material",
    "vpcf_c": "particle", "vsnd_c": "sound", "vsndevts_c": "sound", "vsndstck_c": "sound",
    "vanim_c": "anim", "vagrp_c": "anim", "vseq_c": "anim", "vnmgraph_c": "anim", "vnmclip_c": "anim",
    "vnmskel_c": "anim", "vnmgraphvariation_c": "anim", "vnmidle_c": "anim",
    "vmap_c": "map", "vwrld_c": "map", "vwnod_c": "map", "vents_c": "map", "vvis_c": "map",
    "vcd_c": "scene", "vxml_c": "ui", "vcss_c": "ui", "vjs_c": "ui", "vsvg_c": "ui", "vts_c": "ui",
    "vdata_c": "data", "txt": "data", "vrman_c": "data", "vpost_c": "data", "vpulse_c": "data",
}


def pak_changes(parent: str, sha: str) -> dict[str, list]:
    """Added / changed / removed game files between two builds."""
    diff = git("diff", "-U0", "--no-color", parent, sha, "--", PAK_LIST)
    old, new = {}, {}
    for line in diff.splitlines():
        if line[:1] not in "+-" or line.startswith(("+++", "---")):
            continue
        m = PAK_LINE.match(line[1:])
        if m:
            (new if line[0] == "+" else old)[m[1]] = int(m[2])
    return {
        "added": sorted((p, s) for p, s in new.items() if p not in old),
        "changed": sorted((p, s) for p, s in new.items() if p in old),
        "removed": sorted((p, s) for p, s in old.items() if p not in new),
    }


def kind_of(path: str) -> str:
    return KINDS.get(path.rsplit(".", 1)[-1], "other")


# ── Heroes ─────────────────────────────────────────────

HERO_BLOCK = re.compile(r"^\t(hero_\w+) = ", re.M)
MODEL_NAME = re.compile(r'm_strModelName = resource_name:"([^"]+)"')
SIGNATURE = re.compile(r'ESlot_(?:Signature_\d|Weapon_\w+) = "(\w+)"')
GENERIC_TOKENS = {"hero", "heroes", "gen_man", "base", "testhero", "targetdummy", "genericperson"}


def hero_index(sha: str, loc: dict) -> dict:
    """codename → name, plus the path tokens and ability keys that belong to each hero."""
    text = show(sha, HEROES_VDATA)
    starts = [(m.start(), m[1]) for m in HERO_BLOCK.finditer(text)]
    heroes, tokens, abilities = {}, {}, {}
    for i, (pos, key) in enumerate(starts):
        block = text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        code = key[5:]
        if code in GENERIC_TOKENS or code == "base":
            continue
        name = loc.get(f"{key}:n") or loc.get(key) or loc.get(f"Steam_RP_{key}") or code.capitalize()
        heroes[code] = {"name": name, "named": name.lower() != code}
        tokens[code] = code
        if m := MODEL_NAME.search(block):
            parts = m[1].split("/")
            for t in (parts[-2], re.sub(r"_v\d+$", "", parts[-2]), parts[-1].rsplit(".", 1)[0]):
                if t not in GENERIC_TOKENS:
                    tokens.setdefault(t, code)
        for ability in SIGNATURE.findall(block):
            abilities[ability] = code
    return {"heroes": heroes, "tokens": tokens, "abilities": abilities, "keys": {f"hero_{c}" for c in heroes}}


# ── Where a file belongs ───────────────────────────────

AREAS = {
    "world": ("Карта и окружение", "Здания, пропы, растительность и материалы мира."),
    "items": ("Предметы и косметика", "Модели и эффекты предметов магазина и косметики."),
    "npc": ("Юниты и нейтралы", "Крипы, стражи, боссы и прочие NPC."),
    "fx": ("Эффекты", "Частицы и визуальные эффекты вне героев."),
    "ui": ("Интерфейс", "Иконки, картинки и вёрстка меню и HUD."),
    "audio": ("Звук и музыка", "Звуки, музыка и реплики, не привязанные к герою."),
    "data": ("Скрипты и данные", "Игровые скрипты и таблицы параметров."),
    "other": ("Прочее", "Шейдеры, служебные и прочие файлы."),
}


def area_of(path: str, heroes: dict) -> tuple[str, str]:
    """('hero', codename) or ('area', id)."""
    parts = path.split("/")
    stem = parts[-1].split(".")[0]
    tokens = heroes["tokens"]
    for seg in parts[:-1] + [stem]:
        if seg in tokens:
            return "hero", tokens[seg]
    top, second = parts[0], parts[1] if len(parts) > 2 else ""
    if top == "models" and second.startswith("heroes"):
        return "hero", parts[2] if len(parts) > 3 else "unknown"
    if "cosmetic" in path or top == "models" and second in ("items", "weapons") or second in ("items", "upgrades", "mods"):
        return "area", "items"
    if second.startswith("npc") or second in ("trooper", "creeps", "neutrals"):
        return "area", "npc"
    if top == "maps" or top == "models" or top == "materials" and second != "particle":
        return "area", "world"
    if top in ("panorama", "ui"):
        return "area", "ui"
    if top in ("particles", "materials"):
        return "area", "fx"
    if top in ("sounds", "soundevents", "soundstacks", "scenes"):
        return "area", "audio"
    if top in ("scripts", "resource"):
        return "area", "data"
    return "area", "other"


# ── Plain-language notes ───────────────────────────────

KIND_ORDER = ["model", "texture", "material", "particle", "anim", "map", "ui", "scene", "sound", "data", "other"]
KIND_LABEL = {
    "model": "Модели", "texture": "Текстуры", "material": "Материалы", "particle": "Эффекты",
    "anim": "Анимации", "map": "Карты", "ui": "Интерфейс", "scene": "Сценки", "sound": "Звуки",
    "data": "Данные", "other": "Прочее",
}
VISUAL_KINDS = {"model", "texture", "material", "particle", "map"}
VISUAL_WEIGHT = {"model": 8, "texture": 4, "material": 3, "particle": 1.5, "anim": 0.6, "map": 3, "ui": 0.8}


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def pretty(path: str) -> str:
    return path.rsplit("/", 1)[-1].split(".")[0].replace("_", " ")


def notes_for(groups: dict) -> list[str]:
    """One short Russian sentence per meaningful kind of change."""
    notes = []

    def names(items, n=3):
        shown = ", ".join(pretty(p) for p, _ in items[:n])
        return shown + (f" и ещё {len(items) - n}" if len(items) > n else "")

    for kind in KIND_ORDER:
        g = groups.get(kind)
        if not g:
            continue
        a, c, r = g["added"], g["changed"], g["removed"]
        if kind == "model":
            if a:
                notes.append(f"Новые модели: {names(a)}")
            if c:
                notes.append(f"Переделаны модели: {names(c)}")
            if r:
                notes.append(f"Удалены модели: {names(r)}")
        elif kind in ("texture", "material"):
            word = ("текстура", "текстуры", "текстур") if kind == "texture" else ("материал", "материала", "материалов")
            bits = []
            if a:
                bits.append(f"+{plural(len(a), *word)}")
            if c:
                bits.append(f"изменено {plural(len(c), *word)}")
            if r:
                bits.append(f"удалено {len(r)}")
            notes.append(("Текстуры: " if kind == "texture" else "Материалы: ") + ", ".join(bits))
        elif kind == "particle":
            bits = [f"новых {len(a)}"] * bool(a) + [f"изменено {len(c)}"] * bool(c) + [f"удалено {len(r)}"] * bool(r)
            notes.append("Эффекты: " + ", ".join(bits))
        elif kind == "sound":
            bits = [f"новых {len(a)}"] * bool(a) + [f"изменено {len(c)}"] * bool(c) + [f"удалено {len(r)}"] * bool(r)
            notes.append("Звуки и реплики: " + ", ".join(bits))
        elif kind == "anim":
            notes.append(f"Анимации: {plural(len(a) + len(c) + len(r), 'файл', 'файла', 'файлов')}")
        elif kind == "map":
            notes.append(f"Карты и сцены уровня: {plural(len(a) + len(c) + len(r), 'файл', 'файла', 'файлов')}")
    return notes


def subjects_from(files: dict, heroes: dict) -> list[dict]:
    """Group changed files by hero / area, then by kind."""
    buckets: dict[tuple, dict] = defaultdict(lambda: defaultdict(lambda: {"added": [], "changed": [], "removed": []}))
    for status, items in files.items():
        for path, size in items:
            buckets[area_of(path, heroes)][kind_of(path)][status].append((path, size))

    subjects = []
    for (typ, key), groups in buckets.items():
        # Rebuilt assets show up as "changed" by the hundred; new ones say more about the patch.
        weight = sum(VISUAL_WEIGHT.get(k, 0.05) * (len(g["added"]) + 0.4 * len(g["changed"]) + 0.5 * len(g["removed"]))
                     for k, g in groups.items())
        total = sum(len(g["added"]) + len(g["changed"]) + len(g["removed"]) for g in groups.values())
        if typ == "hero":
            info = heroes["heroes"].get(key, {"name": key.capitalize(), "named": False})
            title, desc = info["name"], f"Герой · {key}"
        else:
            title, desc = AREAS[key]
        tags = []
        model_added = groups.get("model", {}).get("added", [])
        if typ == "hero" and any(p.startswith("models/heroes") and p.count("/") == 3 for p, _ in model_added):
            tags.append("новая модель героя")
        out_groups = []
        for kind in KIND_ORDER:
            g = groups.get(kind)
            if not g:
                continue
            cap = MAX_FILES.get(kind, MAX_FILES_DEFAULT)
            out_groups.append({
                "kind": kind, "label": KIND_LABEL[kind],
                "counts": {s: len(g[s]) for s in ("added", "changed", "removed")},
                "files": [{"s": s[0], "p": p, "size": size}
                          for s in ("added", "changed", "removed") for p, size in g[s]][:cap],
            })
        subjects.append({
            "type": typ, "id": key, "title": title, "desc": desc, "tags": tags,
            "weight": round(weight, 1), "total": total,
            "visual": key not in ("audio", "data", "other") and any(k in VISUAL_KINDS for k in groups),
            "notes": notes_for(groups), "groups": out_groups,
        })
    subjects.sort(key=lambda s: (-s["weight"], -s["total"]))
    return subjects


# ── Localization ───────────────────────────────────────

LOC_LINE = re.compile(r'^\s*"([^"]+)"\s+"((?:[^"\\]|\\.)*)"', re.M)


def parse_loc(text: str) -> dict[str, str]:
    return {k: v for k, v in LOC_LINE.findall(text) if k not in ("Language", "lang", "Tokens")}


def loc_files(sha: str) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", sha, "--", LOC_DIR)
    return [p for p in out.splitlines() if p.endswith("_english.txt")]


def load_loc(sha: str) -> dict[str, str]:
    loc = {}
    for path in loc_files(sha):
        loc.update(parse_loc(show(sha, path)))
    return loc


def loc_changes(parent: str, sha: str, changed_paths: list[str]) -> list[dict]:
    rows = []
    for path in changed_paths:
        if not (path.startswith(LOC_DIR) and path.endswith("_english.txt")):
            continue
        section = path.rsplit("/", 1)[-1].removesuffix("_english.txt")
        old, new = parse_loc(show(parent, path)), parse_loc(show(sha, path))
        for k in new.keys() | old.keys():
            o, n = old.get(k), new.get(k)
            if o != n:
                rows.append({"file": section, "key": k, "old": o, "new": n,
                             "s": "added" if o is None else "removed" if n is None else "changed"})
    # Voice-line transcripts flood big patches; keep them after the real UI strings.
    rows.sort(key=lambda r: (r["file"] == "citadel_generated_vo", r["file"], r["key"]))
    return rows


# ── KV3 scripts (.vdata) ───────────────────────────────

KV_LINE = re.compile(r"^\s*([\w.:@-]+)\s*=\s*(.*?)\s*$")
VALUE_KEYS = {"m_strValue", "m_flValue", "m_nValue", "m_Value", "m_strBonus"}
# Editor bookkeeping and per-item pick statistics: not worth a reader's attention.
SKIP_KEYS = {"m_flPickPct", "m_flWinratePct", "folder_name"}
NOISE_KEYS = {"m_mapAbilityProperties", "m_mapStartingStats", "m_mapStandardLevelUpUpgrades",
              "m_vecAbilityUpgrades", "m_vecPropertyUpgrades", "m_mapBoundAbilities", "m_WeaponInfo"}


def kv_paths(text: str) -> list[tuple]:
    """For every line of a KV3 file: the key path leading to it (None for brackets)."""
    stack: list[list] = []  # [name, is_array, next_index]
    last_key = None
    paths = []
    for line in text.split("\n"):
        s = line.strip()
        if s in ("{", "["):
            if stack and stack[-1][1] and last_key is None:
                name = f"#{stack[-1][2]}"
                stack[-1][2] += 1
            else:
                name = last_key or "#"
            stack.append([name, s == "[", 0])
            last_key = None
            paths.append(None)
            continue
        if s.rstrip(",") in ("}", "]"):
            if stack:
                stack.pop()
            last_key = None
            paths.append(None)
            continue
        m = KV_LINE.match(line)
        if m:
            key, value = m[1], m[2]
            opens = value in ("", "subclass:")
            last_key = key if opens else None
            paths.append(None if opens else (*[e[0] for e in stack[1:]], key))
        else:
            if s and stack and stack[-1][1]:
                paths.append((*[e[0] for e in stack[1:]], f"#{stack[-1][2]}"))
                stack[-1][2] += 1
            else:
                paths.append(None)
            last_key = None
    return paths


def kv_value(line: str) -> str:
    m = KV_LINE.match(line)
    v = (m[2] if m else line.strip()).rstrip(",")
    v = re.sub(r'^(resource_name|panorama|soundevent|subclass):', "", v)
    return v.strip('"')


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def vdata_changes(parent: str, sha: str, path: str, loc: dict, heroes: dict) -> list[dict]:
    diff = git("diff", "-U0", "--no-color", parent, sha, "--", path)
    if not diff:
        return []
    old_text, new_text = show(parent, path), show(sha, path)
    old_lines, new_lines = old_text.split("\n"), new_text.split("\n")
    old_paths, new_paths = kv_paths(old_text), kv_paths(new_text)

    removed: dict[tuple, str] = {}
    added: dict[tuple, str] = {}
    o = n = 0
    for line in diff.splitlines():
        if m := HUNK.match(line):
            o, n = int(m[1]), int(m[3])
            continue
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            p = old_paths[o - 1] if 0 < o <= len(old_paths) else None
            if p:
                removed[p] = kv_value(old_lines[o - 1])
            o += 1
        elif line.startswith("+"):
            p = new_paths[n - 1] if 0 < n <= len(new_paths) else None
            if p:
                added[p] = kv_value(new_lines[n - 1])
            n += 1

    # A value that only moved inside its entity (blocks get restructured) is not a change.
    moved = defaultdict(int)
    for p, v in removed.items():
        if p not in added:
            moved[(p[0], p[-1], v)] += 1
    for p, v in list(added.items()):
        if p not in removed and moved[(p[0], p[-1], v)] > 0:
            moved[(p[0], p[-1], v)] -= 1
            del added[p]
            moved[(p[0], p[-1], v, "gone")] += 1
    for p, v in list(removed.items()):
        if p not in added and moved.get((p[0], p[-1], v, "gone"), 0) > 0:
            moved[(p[0], p[-1], v, "gone")] -= 1
            del removed[p]

    by_entity: dict[str, list] = defaultdict(list)
    for p in sorted(removed.keys() | added.keys()):
        old_v, new_v = removed.get(p), added.get(p)
        if old_v == new_v or len(p) < 2 or any(k.startswith("_") or k in SKIP_KEYS for k in p[1:]):
            continue
        by_entity[p[0]].append({"label": field_label(p, loc), "ctx": context_of(p), "old": old_v, "new": new_v})

    file_name = path.rsplit("/", 1)[-1]
    old_entities = {p[0] for p in old_paths if p}
    new_entities = {p[0] for p in new_paths if p}
    out = []
    for entity, rows in by_entity.items():
        code = heroes["abilities"].get(entity) or (entity[5:] if entity in heroes["keys"] else None)
        if code is None:
            for t, c in heroes["tokens"].items():
                if f"_{t}_" in f"_{entity}_":
                    code = c
                    break
        out.append({
            "file": file_name, "key": entity, "name": loc.get(entity) or loc.get(f"{entity}:n"),
            "hero": heroes["heroes"].get(code, {}).get("name") if code else None,
            "added": entity not in old_entities, "removed": entity not in new_entities,
            "total": len(rows), "rows": rows[:MAX_VDATA_ROWS],
        })
    return out


# Russian names for the parameters that move most often in balance patches.
FIELD_RU = {
    "AbilityCooldown": "Перезарядка", "AbilityDuration": "Длительность", "AbilityCastRange": "Дальность применения",
    "AbilityCastDelay": "Задержка применения", "AbilityCharges": "Заряды", "AbilityChannelTime": "Время канала",
    "AbilityCooldownBetweenCharge": "Пауза между зарядами", "AbilityUnitTargetLimit": "Лимит целей",
    "Damage": "Урон", "BaseDamage": "Базовый урон", "Radius": "Радиус", "Range": "Дальность",
    "BonusDamage": "Доп. урон", "BonusHealth": "Доп. здоровье", "BonusMoveSpeed": "Доп. скорость",
    "SlowPercent": "Замедление, %", "StunDuration": "Оглушение", "TechDamage": "Духовный урон",
    "DPS": "Урон в секунду", "HealAmount": "Лечение", "BarrierAmount": "Барьер",
    "EMaxHealth": "Здоровье", "EMaxMoveSpeed": "Скорость бега", "ESprintSpeed": "Скорость спринта",
    "ELightMeleeDamage": "Лёгкий удар", "EHeavyMeleeDamage": "Тяжёлый удар", "EBaseHealthRegen": "Регенерация",
    "EStamina": "Выносливость", "EBulletArmorDamageReduction": "Пулевая броня", "ETechArmorDamageReduction": "Духовная броня",
    "m_flCooldown": "Перезарядка", "m_flDuration": "Длительность", "m_flDamage": "Урон", "m_flRadius": "Радиус",
    "m_iClipSize": "Магазин", "m_flCycleTime": "Темп стрельбы (с между выстрелами)", "m_flReloadDuration": "Перезарядка оружия",
    "m_flBulletDamage": "Урон пули", "m_iBullets": "Пуль за выстрел", "m_bInDevelopment": "В разработке",
    "m_bDisabled": "Отключён", "m_nComplexity": "Сложность", "m_strModelName": "Модель",
}
HUNGARIAN = re.compile(r"^m_(?:fl|n|i|b|str|vec|map|h|e|s|ui|un|cl|sz|color|ang)?(?=[A-Z])")


def field_label(path: tuple, loc: dict) -> str:
    key = path[-1]
    if key in VALUE_KEYS and len(path) > 2:
        key = path[-2]
    if key.startswith("#") and len(path) > 2:
        key = path[-2]
    if key in FIELD_RU:
        return FIELD_RU[key]
    if label := loc.get(f"{key}_label"):
        return label
    bare = HUNGARIAN.sub("", key).removeprefix("m_")
    bare = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", bare).replace("_", " ").strip()
    return bare[:1].upper() + bare[1:] if bare else key


def context_of(path: tuple) -> str:
    inner = [p for p in path[1:-1] if p not in NOISE_KEYS and not p.startswith("#")]
    if path[-1] in VALUE_KEYS:
        inner = inner[:-1]
    return " › ".join(inner[-2:])


# ── One build ──────────────────────────────────────────

def summarise(commit: dict) -> dict:
    parent, sha = commit["parent"], commit["sha"]
    changed_paths = git("diff", "--name-only", parent, sha, "--", "game").splitlines()
    loc = load_loc(sha)
    heroes = hero_index(sha, loc)

    files = pak_changes(parent, sha) if PAK_LIST in changed_paths else {"added": [], "changed": [], "removed": []}
    subjects = subjects_from(files, heroes)
    strings = loc_changes(parent, sha, changed_paths)
    vdata = []
    for path in changed_paths:
        if path.endswith(".vdata") and path.startswith(f"{GAME}/pak01_dir/"):
            vdata.extend(vdata_changes(parent, sha, path, loc, heroes))
    vdata.sort(key=lambda e: (e["hero"] is None, e["hero"] or "", e["file"], e["key"]))

    steam_inf = show(sha, f"{GAME}/steam.inf")
    version_date = " ".join(re.findall(r"^Version(?:Date|Time)=(.+)$", steam_inf, re.M))
    new_heroes = sorted({heroes["heroes"][e["key"][5:]]["name"] for e in vdata
                         if e["added"] and e["key"] in heroes["keys"]})
    counts = {s: len(files[s]) for s in files} | {
        "strings": len(strings), "vdata": sum(e["total"] for e in vdata), "code": sum(
            1 for p in changed_paths if p.startswith(("game/citadel/bin", "game/bin", "DumpSource2", "Protobufs")))}
    visual = [s for s in subjects if s["visual"]]
    weight = sum(s["weight"] for s in subjects) + len(strings) * 0.2 + counts["vdata"] * 0.3
    size = "major" if weight > 400 or new_heroes else "update" if weight > 25 else "hotfix" if weight > 0 else "empty"

    headline = []
    if new_heroes:
        headline.append("Новый герой в файлах: " + ", ".join(new_heroes))
    for s in visual[:3]:
        headline.append(f"{s['title']}: {s['notes'][0][0].lower() + s['notes'][0][1:]}" if s["notes"] else s["title"])
    if strings:
        headline.append(f"Тексты игры: {plural(len(strings), 'строка', 'строки', 'строк')}")
    if vdata:
        headline.append(f"Параметры: {plural(counts['vdata'], 'значение', 'значения', 'значений')}")
    if not headline:
        headline.append("Изменения только в коде игры" if counts["code"] else "Только техническое обновление")

    return {
        "build": commit["build"], "sha": sha, "parent": parent, "time": commit["time"],
        "versionDate": version_date, "size": size, "weight": round(weight),
        "headline": headline[:5], "newHeroes": new_heroes, "counts": counts,
        "subjects": subjects, "strings": strings[:MAX_STRINGS], "stringsTotal": len(strings),
        "vdata": vdata[:MAX_VDATA_ENTITIES], "vdataTotal": len(vdata),
    }


def entry(summary: dict, file: str) -> dict:
    return {k: summary[k] for k in ("build", "sha", "time", "versionDate", "size", "weight", "headline",
                                    "newHeroes", "counts")} | {"file": file}


def main() -> int:
    sync_repo()
    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text("utf-8")) if index_path.exists() and "--rebuild" not in sys.argv else {}
    known = {b["sha"]: b for b in index.get("builds", [])}

    # Builds read straight from Steam (backend.tracker) are not in SteamDB's history: keep them,
    # and skip SteamDB's commit of a build we already have from Steam.
    own = [b for b in index.get("builds", []) if b.get("manifest")]
    own_builds = {b["build"] for b in own}
    builds = []
    for commit in build_commits()[:KEEP_BUILDS]:
        if commit["build"] in own_builds:
            continue
        if commit["sha"] in known:
            builds.append(known[commit["sha"]])
            continue
        started = time.time()
        summary = summarise(commit)
        file = f"{commit['build']}-{commit['sha'][:8]}.json"
        (OUT / file).write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")
        builds.append(entry(summary, file))
        print(f"build {commit['build']} ({commit['sha'][:8]}): {summary['size']}, {time.time() - started:.1f}s")

    builds = sorted(builds + own, key=lambda b: -b["time"])[:KEEP_BUILDS]
    if builds == index.get("builds"):
        print("no new builds")
        return 0
    keep = {b["file"] for b in builds}
    for f in OUT.glob("*.json"):
        if f.name != "index.json" and f.name not in keep:
            f.unlink()
    index |= {"updatedAt": int(time.time() * 1000), "source": REPO_URL, "builds": builds}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
