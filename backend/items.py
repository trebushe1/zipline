"""Item shop: every item of Deadlock's Curiosity Shop with its stats, text and icon.

Reads the items from abilities.vdata of the newest build in SteamDB's GameTracking-Deadlock (tier prices
from generic_data.vdata, English text from its localization). What the repository does not keep comes
straight from Steam: the shop icons (the game's own pictures, lossless at their own size, converted by
Source2Viewer-CLI) and the Russian localization. An icon is converted again only when its file changed,
the Russian text is downloaded again only when the game's file changed (its checksum is in the depot
manifest), so most runs never log in to Steam.

Writes data/items/index.json, data/items/icons/<item>.png (with a small WebP for the shop grid in
data/items/thumbs/) and data/items/loc_ru.json (the Russian strings the items use, kept so a balance patch
does not need a Steam login).

    python -m backend.items             # refresh the list, fetch new and changed icons and Russian text
    python -m backend.items --offline   # only the list, from the tracking repository and the kept text

Needs STEAM_USERNAME / STEAM_PASSWORD (see backend/media.py) for icons and Russian; without them the
list is still written.
"""
import html
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import config
from .heroes import convert_ability_icon, latest_build, pak_crcs, panorama_path, vrf_path
from .media import DEPOT, SteamFiles, game_root, log, manifest_id, tool_paths
from .patches import GAME, load_loc, show

OUT = config.ROOT / "data" / "items"
ABILITIES_VDATA = f"{GAME}/pak01_dir/scripts/abilities.vdata"
GENERIC_VDATA = f"{GAME}/pak01_dir/scripts/generic_data.vdata"
LOC_RU = [f"{GAME}/resource/localization/{name}/{name}_russian.txt"
          for name in ("citadel_attributes", "citadel_mods", "citadel_gc_mod_names")]
SLOTS = {"EItemSlotType_WeaponMod": "weapon", "EItemSlotType_Armor": "vitality", "EItemSlotType_Tech": "spirit"}
ITEMS_VERSION = 1  # bump to convert every icon again after changing the conversion
THUMB_SIZE = 192  # the shop grid: a tile is at most ~100 px wide, twice that on sharp screens


# ── KV3 text (.vdata) ──────────────────────────────────

KV3_TOKEN = re.compile(r'"""(.*?)"""|"((?:[^"\\]|\\.)*)"|([{}\[\]=,])|([^\s{}\[\]=,"]+)', re.S)


def kv3(text: str):
    """The text form of KeyValues3 as plain Python values. Type prefixes (resource_name:, panorama:,
    subclass:) are dropped: only the values matter here."""
    tokens = []
    for m in KV3_TOKEN.finditer(re.sub(r"^\s*<!--.*?-->", "", text, flags=re.S)):
        if m[1] is not None or m[2] is not None:
            tokens.append(("s", m[1] if m[1] is not None else m[2]))
        elif m[3]:
            tokens.append((m[3], None))
        elif not m[4].endswith(":"):
            tokens.append(("w", m[4]))
    pos = 0

    def value():
        nonlocal pos
        kind, v = tokens[pos]
        pos += 1
        if kind in "{[":
            out = {} if kind == "{" else []
            close = "}" if kind == "{" else "]"
            while tokens[pos][0] != close:
                if kind == "{":
                    key = tokens[pos][1]
                    pos += 2  # the key and "="
                    out[key] = value()
                else:
                    out.append(value())
                if tokens[pos][0] == ",":
                    pos += 1
            pos += 1
            return out
        if kind == "s":
            return v
        if v in ("true", "false"):
            return v == "true"
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v

    return value()


# ── Localization ───────────────────────────────────────

LOC_LINE = re.compile(r'^\s*"([^"]+)"\s+"((?:[^"\\]|\\.)*)"', re.M)


def parse_loc_bytes(data: bytes) -> dict[str, str]:
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("utf-8-sig", "replace")
    return {k: v for k, v in LOC_LINE.findall(text) if k not in ("Language", "lang", "Tokens")}


def unescape(s: str) -> str:
    return re.sub(r'\\(["\\n])', lambda m: "\n" if m[1] == "n" else m[1], s)


def manifest_shas(sha: str) -> dict[str, str]:
    """Loose files of the content depot and their SHA-1, from the manifest SteamDB keeps."""
    text = show(sha, f"manifests/manifest_{DEPOT}.txt")
    return {m[2].replace("\\", "/"): m[1] for m in re.finditer(r"^\s*\d+\s+\d+\s+([0-9a-f]{40})\s+\d+\s+(.+?)\s*$", text, re.M)}


# The Russian strings worth keeping: names, descriptions and stat labels of items.
KEPT_RU = re.compile(r"^(upgrade_|InlineAttribute_|StatDesc_|EShopFilter)|_(label|prefix|postfix|desc|search)$")


# ── Items ──────────────────────────────────────────────

def is_on(value) -> bool:
    return value is True or value == "true"


def shop_items(abilities: dict) -> dict[str, dict]:
    """Items on sale: enabled, in a slot and a tier, with a shop picture (the base classes have none)."""
    out = {}
    for key, v in abilities.items():
        if not isinstance(v, dict) or v.get("m_eAbilityType") != "EAbilityType_Item":
            continue
        if is_on(v.get("m_bDisabled")) or v.get("m_eItemSlotType") not in SLOTS:
            continue
        if not str(v.get("m_iItemTier", "")).startswith("EModTier_") or not v.get("m_strShopIconLarge"):
            continue
        out[key] = v
    return out


NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)(m)?$")
UNITS = {"EDisplayUnit_Meters": "m", "EDisplayUnit_MetersPerSecond": "m/s"}


def number(value) -> tuple[float | None, str]:
    """m_strValue as a number and the unit written into it ("11m")."""
    m = NUMBER.match(str(value).strip())
    return (float(m[1]), m[2] or "") if m else (None, "")


def fmt(x: float) -> str:
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def words(name: str) -> str:
    """BonusClipSizePercent → Bonus Clip Size Percent: the last resort for a label."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


class Text:
    """Strings in both languages; Russian falls back to English key by key."""

    def __init__(self, en: dict[str, str], ru: dict[str, str]):
        self.en, self.ru = en, ru

    def get(self, key: str) -> dict | None:
        en = self.en.get(key)
        if en is None:
            return None
        ru = self.ru.get(key)
        return {"en": unescape(en), "ru": unescape(ru)} if ru and ru != en else {"en": unescape(en)}

    def raw(self, key: str, lang: str) -> str | None:
        v = (self.ru.get(key) if lang == "ru" else None) or self.en.get(key)
        return unescape(v) if v is not None else None


def stat(text: Text, name: str, prop: dict) -> dict | None:
    """One property the way the tooltip prints it: +30% Max Ammo."""
    value, unit = number(prop.get("m_strValue", ""))
    if value is None or value == 0:
        return None
    token = prop.get("m_strLocTokenOverride") or name
    display = prop.get("m_eDisplayType") or ""
    out = {"v": fmt(value)}
    label = text.get(f"{token}_label") or text.get(f"StatDesc_{display[1:]}") or {"en": words(token)}
    out["l"] = label
    for lang in ("en", "ru"):
        prefix = text.raw(f"{token}_prefix", lang) or ""
        postfix = text.raw(f"{token}_postfix", lang)
        if postfix is None:
            postfix = unit or UNITS.get(prop.get("m_eDisplayUnits", ""), "")
        sign = "+" if value > 0 else ""
        out[f"pre_{lang}"] = prefix.replace("{s:sign}", sign) if "{s:sign}" in prefix else prefix
        out[f"post_{lang}"] = postfix
    # Keep both languages only where they differ.
    for part in ("pre", "post"):
        en, ru = out.pop(f"{part}_en"), out.pop(f"{part}_ru")
        if en or ru:
            out[part] = {"en": en, "ru": ru} if ru != en else en
    scale = prop.get("m_subclassScaleFunction") or {}
    if scale.get("m_flStatScale") and scale.get("m_eSpecificStatScaleType") == "ETechPower":
        out["spirit"] = scale["m_flStatScale"]
    if "ConditionallyApplied" in str(prop.get("m_eStatsUsageFlags", "")):
        out["cond"] = True
    return out


INLINE = re.compile(r"\{g:citadel_inline_attribute:'(\w+)'\}")
TAG = re.compile(r"(<[^>]*>)")
SPAN_CLASS = {"highlight": "hl", "diminish": "dim"}


def inline_kind(name: str) -> str:
    n = name.lower()
    if "spirit" in n:
        return "spirit"
    if any(w in n for w in ("weapon", "bullet", "firerate", "melee")):
        return "weapon"
    if any(w in n for w in ("heal", "health", "regen", "fortitude")):
        return "vitality"
    return "plain"


def rich(raw: str, values: dict[str, str], text: Text, lang: str) -> str:
    """A description as safe HTML: the game's highlight spans and line breaks, values filled in,
    inline attributes as coloured words; any other markup is dropped."""
    s = re.sub(r"\{s:(\w+)\}", lambda m: values.get(m[1], "?"), raw)
    s = re.sub(r"\{[a-z]:[^{}]*\}", lambda m: m[0] if m[0].startswith("{g:citadel_inline_attribute") else "", s)
    out, open_spans = [], 0
    for part in TAG.split(s):
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            tag = part.lower()
            if re.match(r"<br\s*/?>", tag):
                out.append("<br>")
            elif tag.startswith("<span"):
                cls = re.search(r'class=\\?"([\w ]+)\\?"', part)
                out.append(f'<span class="{SPAN_CLASS.get(cls[1].split()[0], "hl") if cls else "hl"}">')
                open_spans += 1
            elif tag.startswith("</span") and open_spans:
                out.append("</span>")
                open_spans -= 1
            continue
        pieces = INLINE.split(part)
        for i, piece in enumerate(pieces):
            if i % 2:
                label = text.raw(f"InlineAttribute_{piece}", lang) or words(piece).lower()
                out.append(f'<span class="ia ia-{inline_kind(piece)}">{html.escape(label)}</span>')
            else:
                out.append(html.escape(piece, quote=False))
    out.extend("</span>" * open_spans)
    return "".join(out).strip()


SECTION = {"EArea_Innate": "innate", "EArea_Active": "active", "EArea_Passive": "passive"}


def item_entry(key: str, v: dict, text: Text, prices: list[int]) -> dict:
    props = v.get("m_mapAbilityProperties") or {}
    stats = {name: s for name, p in props.items() if isinstance(p, dict) and (s := stat(text, name, p))}
    values = {name: fmt(n) for name, p in props.items() if isinstance(p, dict) and (n := number(p.get("m_strValue", ""))[0]) is not None}
    tier = int(str(v["m_iItemTier"])[-1])
    activation = v.get("m_eAbilityActivation", "").removeprefix("CITADEL_ABILITY_ACTIVATION_").lower()
    entry = {
        "key": key, "slot": SLOTS[v["m_eItemSlotType"]], "tier": tier,
        "cost": prices[tier] if tier < len(prices) else None,
        "name": text.get(key) or {"en": words(key.removeprefix("upgrade_")).replace("_", " ").title()},
    }
    if activation != "passive":
        entry["active"] = "toggle" if "toggle" in activation else True
    if search := text.get(f"{key}_search"):
        entry["search"] = search
    if v.get("m_vecComponentItems"):
        entry["components"] = list(v["m_vecComponentItems"])

    def pick(names) -> list[dict]:
        return [stats[n] for n in names or [] if isinstance(n, str) and n in stats]

    sections = []
    for info in v.get("m_vecTooltipSectionInfo") or []:
        kind = SECTION.get(info.get("m_eAbilitySectionType"), "passive")
        section = {"type": kind, "blocks": []}
        for attrs in info.get("m_vecSectionAttributes") or []:
            names = [n for n in attrs.get("m_vecAbilityProperties") or [] if isinstance(n, str)]
            if "AbilityCooldown" in names and "AbilityCooldown" in stats and kind != "innate":
                section["cooldown"] = stats["AbilityCooldown"]["v"]
                names.remove("AbilityCooldown")
            block = {}
            if loc := attrs.get("m_strLocString"):
                key_ = loc.lstrip("#")
                raw_en = text.raw(key_, "en")
                if raw_en:
                    block["text"] = {"en": rich(raw_en, values, text, "en")}
                    if (raw_ru := text.ru.get(key_)) and raw_ru != text.en.get(key_):
                        block["text"]["ru"] = rich(text.raw(key_, "ru"), values, text, "ru")
            important = [i.get("m_strImportantProperty") for i in attrs.get("m_vecImportantAbilityProperties") or []
                         if isinstance(i, dict)]
            for part, names_ in (("big", important), ("elevated", attrs.get("m_vecElevatedAbilityProperties")),
                                 ("stats", names)):
                if found := pick(names_):
                    block[part] = found
            if block:
                section["blocks"].append(block)
        if section["blocks"] or section.get("cooldown"):
            sections.append(section)
    if not sections:  # no tooltip layout: the description and every stat that is set
        block = {"stats": list(stats.values())}
        if raw_en := text.raw(f"{key}_desc", "en"):
            block["text"] = {"en": rich(raw_en, values, text, "en")}
        sections.append({"type": "passive", "blocks": [block]})
    entry["sections"] = sections
    entry["filters"] = shop_filters(v, entry, props)
    return entry


# Most shop filters follow from the stats an item provides; the rest are set on the item itself.
PROVIDED_FILTER = {
    "HEALTH_MAX": "HP", "HEALTH_MAX_PERCENT": "HP", "BASE_HEALTH_PERCENT": "HP",
    "WEAPON_DAMAGE_INCREASE": "WeaponDamage", "CLOSE_RANGE_WEAPON_DAMAGE_INCREASE": "WeaponDamage",
    "LONG_RANGE_BULLET_DAMAGE_INCREASE": "WeaponDamage",
    "TECH_POWER": "SpiritDamage", "TECH_POWER_PERCENT": "SpiritDamage",
    "MOVEMENT_SPEED_MAX": "MoveSpeed", "SPRINT_SPEED_BONUS": "Sprint",
    "OUT_OF_COMBAT_HEALTH_REGEN": "OutOfCombatRegen", "HEALTH_REGEN_PER_SECOND": "Regen",
    "FIRE_RATE": "WeaponFireRate", "TECH_RESIST": "SpiritResist", "BULLET_ARMOR_DAMAGE_RESIST": "PhysicalResist",
    "MELEE_RESIST": "MeleeResist", "STATUS_RESISTANCE": "DebuffResist", "MOVEMENT_SLOW_RESISTANCE": "SlowResist",
    "MOVEMENT_SPEED_SLOW_PERCENT": "MobilityReduction", "MOVEMENT_GROUND_DASH_REDUCTION_PERCENT": "MobilityReduction",
    "TECH_RANGE_PERCENT": "SpiritRange", "TECH_RADIUS_PERCENT": "SpiritRange",
    "COOLDOWN_REDUCTION_PERCENTAGE": "SpiritCooldownAndCharges", "BONUS_ABILITY_CHARGES": "SpiritCooldownAndCharges",
    "COOLDOWN_BETWEEN_CHARGE_REDUCTION_PERCENTAGE": "SpiritCooldownAndCharges",
    "ITEM_COOLDOWN_REDUCTION_PERCENTAGE": "SpiritCooldownAndCharges",
    "ULTIMATE_COOLDOWN_REDUCTION_PERCENTAGE": "SpiritCooldownAndCharges", "ENABLE_CHARGES": "SpiritCooldownAndCharges",
    "BONUS_ABILITY_DURATION_PERCENTAGE": "SpiritDuration",
    "AMMO_CLIP_SIZE_PERCENT": "WeaponAmmo", "AMMO_CLIP_SIZE": "WeaponAmmo", "RELOAD_SPEED": "WeaponAmmo",
    "BONUS_BULLET_SPEED_PERCENT": "WeaponBulletVelocity", "BONUS_ATTACK_RANGE_PERCENT": "WeaponRange",
    "MELEE_DAMAGE_INCREASE": "Melee", "MELEE_TRAVEL_DISTANCE_PERCENTAGE": "Melee",
    "BULLET_AND_MELEE_RESIST_REDUCTION": "BulletVuln", "TECH_RESIST_REDUCTION": "SpiritVuln",
    "BARRIER_HEALTH": "Barrier", "TECH_LIFESTEAL": "Lifesteal", "BULLET_LIFESTEAL": "Lifesteal",
    "STAMINA": "Stamina", "STAMINA_REGEN_PER_SECOND_PERCENTAGE": "Stamina", "AIR_MOVE_DISTANCE_INCREASE_PERCENT": "Stamina",
    "FIRE_RATE_SLOW": "FireRateReduction",
    "AIR_CONTROL_PERCENT": "JumpAndDash", "AIR_CONTROL_ACCEL_PERCENT": "JumpAndDash", "MOVEMENT_SLIDE_DISTANCE_SCALE": "JumpAndDash",
}
# Words of the description that name an effect (the English text, highlighted).
STATUS_WORDS = {"stun": "Status_Stun", "silence": "Status_Silence", "disarm": "Status_Disarm",
                "immobiliz": "Status_Immobilize", "curse": "Status_Curse", "invulnerab": "Invulnerability",
                "invisib": "Stealth", "stealth": "Stealth", "teleport": "Teleport", "heal": "Healing", "barrier": "Barrier"}
# Items that protect from these effects name them too; the words then do not mean the item applies them.
PROTECTIVE = {"AntiCC", "DebuffResist", "Invulnerability"}


def shop_filters(v: dict, entry: dict, props: dict) -> list[str]:
    found = set()
    for p in props.values():
        if not isinstance(p, dict) or number(p.get("m_strValue", ""))[0] in (None, 0):
            continue
        kind = str(p.get("m_eProvidedPropertyType", "")).removeprefix("MODIFIER_VALUE_")
        if kind in ("HEAL_AMP_REGEN_PERCENT", "HEAL_AMP_RECEIVE_PERCENT", "HEAL_AMP_CAST_PERCENT"):
            found.add("AntiHeal" if number(p["m_strValue"])[0] < 0 else "Healing")
        elif kind in PROVIDED_FILTER:
            found.add(PROVIDED_FILTER[kind])
    for f in str(v.get("m_eAdditionalShopFilters") or "").split("|"):
        if f.strip().startswith("EShopFilter"):
            found.add(f.strip().removeprefix("EShopFilter"))
    highlighted = " ".join(re.findall(r'<span class="(?:hl|ia[^"]*)">([^<]+)</span>',
                                      " ".join(b.get("text", {}).get("en", "") for s in entry["sections"] for b in s["blocks"])))
    words_found = {f for word, f in STATUS_WORDS.items() if word in highlighted.lower()}
    if found & PROTECTIVE:
        words_found = {f for f in words_found if not f.startswith("Status_")}
    if "AntiHeal" in found:
        words_found.discard("Healing")
    if entry["slot"] == "weapon" and "weapon damage" in highlighted.lower():
        words_found.add("PhysicalAdditionalDamage")
    found |= words_found
    # Spirit damage it deals: a damage value that grows with spirit power.
    for name, p in props.items():
        scale = isinstance(p, dict) and p.get("m_subclassScaleFunction") or {}
        if scale.get("m_eSpecificStatScaleType") == "ETechPower" and re.search(r"Damage|DPS", name) \
                and number(p.get("m_strValue", ""))[0]:
            found.add("SpiritAdditionalDamage")
    if entry.get("active"):
        found.add("Active")
    if "TARGET_ABILITY_BEHAVIOR_IMBUE" in json.dumps(v):
        found.add("Imbue")
    return sorted(found)


# ── Steam ──────────────────────────────────────────────

def fetch_from_steam(build: dict, icons: dict[str, str], loc: list[str]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """Converted icons (item → PNG) and the raw Russian files (depot path → bytes), in one Steam login."""
    manifest = manifest_id(build["sha"])
    sfd = tool_paths()[0]
    vrf = vrf_path() if icons else None
    work = Path(tempfile.mkdtemp(prefix="items-"))
    pngs, texts = {}, {}
    try:
        files = SteamFiles(sfd, work, [manifest])
        files.get(set(icons.values()), set(loc))
        for path in loc:
            f = files.out / DEPOT / manifest / path
            if f.exists():
                texts[path] = f.read_bytes()
            else:
                log(f"  not downloaded: {path}")
        if icons:
            citadel = game_root(files, manifest, show(build["sha"], f"{GAME}/gameinfo.gi"), work)
            for key, path in icons.items():
                if png := convert_ability_icon(vrf, citadel, path, work / "tmp" / key):
                    pngs[key] = png
                else:
                    log(f"  could not convert {path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return pngs, texts


def make_thumb(png: Path, out: Path) -> None:
    """The small WebP the shop grid shows; the PNG itself is for the item card and downloads."""
    from PIL import Image
    with Image.open(png) as img:
        img = img.convert("RGBA")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        img.save(out, "WEBP", quality=86, method=6)


# ── Main ───────────────────────────────────────────────

def main() -> int:
    offline = "--offline" in sys.argv or not (os.environ.get("STEAM_USERNAME") and os.environ.get("STEAM_PASSWORD"))
    build = latest_build()
    sha = build["sha"]
    abilities = kv3(show(sha, ABILITIES_VDATA))
    generic = kv3(show(sha, GENERIC_VDATA))
    prices = [int(p) for p in generic.get("m_nItemPricePerTier") or [0, 800, 1600, 3200, 6400, 9999]]
    items = shop_items(abilities)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icons").mkdir(exist_ok=True)
    deps_path, ru_path = OUT / "deps.json", OUT / "loc_ru.json"
    deps = json.loads(deps_path.read_text("utf-8")) if deps_path.exists() else {}
    if deps.get("version") != ITEMS_VERSION:
        deps = {"version": ITEMS_VERSION}
    ru = json.loads(ru_path.read_text("utf-8")) if ru_path.exists() else {}

    crcs, shas = pak_crcs(sha), manifest_shas(sha)
    icon_paths = {key: panorama_path(f'"{v["m_strShopIconLarge"]}"') for key, v in items.items()}
    icon_deps, loc_deps = deps.setdefault("icons", {}), deps.setdefault("loc_ru", {})
    todo_icons = {k: p for k, p in icon_paths.items()
                  if p and (icon_deps.get(k) != crcs.get(p) or not (OUT / "icons" / f"{k}.png").exists())}
    todo_loc = [p for p in LOC_RU if p in shas and loc_deps.get(p) != shas[p]]
    log(f"build {build['build']}: {len(items)} items, {len(todo_icons)} icons and {len(todo_loc)} Russian files to fetch")

    if (todo_icons or todo_loc) and offline:
        log("offline (no STEAM_USERNAME / STEAM_PASSWORD): icons and Russian text stay as they are")
    elif todo_icons or todo_loc:
        pngs, texts = fetch_from_steam(build, todo_icons, todo_loc)
        for key, png in pngs.items():
            (OUT / "icons" / f"{key}.png").write_bytes(png)
            icon_deps[key] = crcs.get(todo_icons[key])
        if texts:
            # Whole files are replaced together: a string can move from one file to another.
            merged = dict(ru) if len(texts) < len(todo_loc) else {}
            for path in LOC_RU:
                if path in texts:
                    merged.update({k: v for k, v in parse_loc_bytes(texts[path]).items() if KEPT_RU.search(k)})
                    loc_deps[path] = shas[path]
            ru = merged
            ru_path.write_text(json.dumps(ru, ensure_ascii=False, sort_keys=True, indent=0) + "\n", "utf-8")
        log(f"  {len(pngs)} icons converted, {len(texts)} Russian files read")

    text = Text(load_loc(sha), ru)
    entries = [item_entry(key, v, text, prices) for key, v in items.items()]
    (OUT / "thumbs").mkdir(exist_ok=True)
    for e in entries:
        png, thumb = OUT / "icons" / f"{e['key']}.png", OUT / "thumbs" / f"{e['key']}.webp"
        if png.exists():
            e["icon"] = png.name
            if not thumb.exists() or thumb.stat().st_mtime < png.stat().st_mtime:
                make_thumb(png, thumb)
            e["thumb"] = thumb.name
    entries.sort(key=lambda e: (list(SLOTS.values()).index(e["slot"]), e["tier"], e["name"]["en"].lower()))
    index = {"build": build["build"], "sha": sha, "prices": prices[1:], "russian": bool(ru), "items": entries}
    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8")
    # Icons of items that left the shop.
    for f in [*(OUT / "icons").glob("*.png"), *(OUT / "thumbs").glob("*.webp")]:
        if f.stem not in items:
            f.unlink()
            icon_deps.pop(f.stem, None)
    deps_path.write_text(json.dumps(deps, sort_keys=True, indent=1) + "\n", "utf-8")
    log(f"wrote {len(entries)} items ({sum(1 for e in entries if 'icon' in e)} with icons)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
