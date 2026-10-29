"""Balance changes between two builds, read the way a patch note reads them.

backend.patches lists every changed line of the game's .vdata scripts; that is
precise but unreadable for balance ("#0: -12 → -14"). This compares the same data
by meaning instead:

  - hero abilities (the four signature slots) and shop items: every ability
    property (value, spirit scaling) and the three upgrade tiers;
  - heroes: starting stats, per-level growth, stat scaling;
  - hero weapons: bullet damage, fire rate, clip, reload and the like;
  - items: tier and components too.

Labels come from the game's own localization (Russian where the item shop keeps
it), names of heroes, abilities and items stay in English as players use them.

    python -m backend.balance <old sha> <new sha>   # print the changes of one build
    python -m backend.balance --backfill            # add balance to summaries written before it
"""
import json
import re
import sys

from . import config
from .items import SLOTS, Text, fmt, is_on, kv3, number, shop_items, unescape, words
from .patches import GAME, HEROES_VDATA, load_loc, show

ABILITIES_VDATA = f"{GAME}/pak01_dir/scripts/abilities.vdata"
LOC_RU = config.ROOT / "data" / "items" / "loc_ru.json"
SIGNATURE_SLOTS = ("ESlot_Signature_1", "ESlot_Signature_2", "ESlot_Signature_3", "ESlot_Signature_4")

# Lower is better for these (a shorter cooldown is a buff).
LOWER_IS_BETTER = re.compile(r"Cooldown|CastDelay|ChannelTime|Cost|Reload|CycleTime|ReloadDuration|reloadDuration|"
                             r"Spread|Punch|Recoil|DamageTaken|SelfDamage|Delay", re.I)
# No arrow: whether more is better depends on the hero (dash length, gravity...).
NEUTRAL = re.compile(r"DashDuration|Gravity|Acceleration", re.I)
# Sound, camera and visual tuning is not balance.
NOISE = re.compile(r"Pitch|VolScale|Vol_?Scale|Sound|Particle|Camera|Fov|Color|Debug|Anim|Visual", re.I)
TIME = re.compile(r"Cooldown|Duration|Delay|ChannelTime|Time$|Lifetime|reloadDuration", re.I)

# Weapon fields worth a patch note line, with their labels.
WEAPON_FIELDS = {
    "m_flBulletDamage": ("Урон пули", "Bullet Damage"),
    "m_iBullets": ("Пуль за выстрел", "Bullets per Shot"),
    "m_flCycleTime": ("Время между выстрелами", "Time Between Shots"),
    "m_iClipSize": ("Магазин", "Clip Size"),
    "m_reloadDuration": ("Перезарядка оружия", "Reload Time"),
    "m_iBurstShotCount": ("Выстрелов в очереди", "Burst Shots"),
    "m_flIntraBurstCycleTime": ("Пауза внутри очереди", "Burst Delay"),
    "m_flBulletSpeed": ("Скорость пули", "Bullet Speed"),
    "m_flDamageFalloffStartRange": ("Начало падения урона", "Falloff Start"),
    "m_flDamageFalloffEndRange": ("Конец падения урона", "Falloff End"),
    "m_flDamageFalloffEndScale": ("Урон на дальней дистанции", "Long Range Damage"),
    "m_flCritBonusStart": ("Множитель крита", "Headshot Multiplier"),
    "m_flBulletRadius": ("Размер пули", "Bullet Size"),
}
WEAPON_UNITS = {"m_flCycleTime": "с", "m_reloadDuration": "с", "m_flIntraBurstCycleTime": "с"}

# Starting stats and per-level growth that the localization does not name.
HERO_STATS = {
    "EMaxHealth": ("Здоровье", "Max Health"), "EMaxMoveSpeed": ("Скорость бега", "Move Speed"),
    "ESprintSpeed": ("Скорость спринта", "Sprint Speed"), "ELightMeleeDamage": ("Лёгкий удар", "Light Melee"),
    "EHeavyMeleeDamage": ("Тяжёлый удар", "Heavy Melee"), "EBaseHealthRegen": ("Регенерация", "Health Regen"),
    "EStamina": ("Выносливость", "Stamina"), "EBulletArmorDamageReduction": ("Пулевая броня", "Bullet Resist"),
    "ETechArmorDamageReduction": ("Духовная броня", "Spirit Resist"), "EMoveAcceleration": ("Ускорение", "Acceleration"),
    "ECrouchSpeed": ("Скорость в приседе", "Crouch Speed"), "EStaminaRegenPerSecond": ("Восстановление выносливости", "Stamina Regen"),
    "EReloadSpeed": ("Скорость перезарядки", "Reload Speed"), "ETechPower": ("Сила духа", "Spirit Power"),
    "EBulletDamage": ("Урон пули", "Bullet Damage"), "ECritDamageBonusScale": ("Урон в голову", "Headshot Damage"),
    "EAirDashDuration": ("Длительность рывка в воздухе", "Air Dash Duration"),
    "EGroundDashDuration": ("Длительность рывка", "Dash Duration"),
}
LEVEL_UP = {
    "MODIFIER_VALUE_BASE_BULLET_DAMAGE_FROM_LEVEL": ("Урон пули за уровень", "Bullet Damage per Level"),
    "MODIFIER_VALUE_BASE_HEALTH_FROM_LEVEL": ("Здоровье за уровень", "Health per Level"),
    "MODIFIER_VALUE_BASE_MELEE_DAMAGE_FROM_LEVEL": ("Урон ближнего боя за уровень", "Melee Damage per Level"),
    "MODIFIER_VALUE_TECH_POWER": ("Сила духа за уровень", "Spirit Power per Level"),
    "MODIFIER_VALUE_BULLET_ARMOR_DAMAGE_RESIST": ("Пулевая броня за уровень", "Bullet Resist per Level"),
    "MODIFIER_VALUE_TECH_RESIST": ("Духовная броня за уровень", "Spirit Resist per Level"),
}
UPGRADE_KIND = {"EAddToScale": ("масштабирование от духа", "spirit scaling"),
                "EMultiplyScale": ("множитель масштабирования от духа", "spirit scaling multiplier"),
                "EMultiplyBase": ("множитель", "multiplier")}
SPIRIT = (": масштабирование от духа", ": spirit scaling")


def suffixed(lbl: dict, ru: str, en: str) -> dict:
    """A label with a note added; a label without Russian still gets the Russian note."""
    return {"ru": f"{lbl.get('ru') or lbl['en']}{ru}", "en": f"{lbl['en']}{en}"}


class Missing(Exception):
    """The scripts of a build are not in the repository (e.g. a build only our own tracker has)."""


def load(sha: str) -> tuple[dict, dict, Text]:
    abilities, heroes = show(sha, ABILITIES_VDATA), show(sha, HEROES_VDATA)
    if not abilities.strip() or not heroes.strip():
        raise Missing(sha)
    ru = json.loads(LOC_RU.read_text("utf-8")) if LOC_RU.exists() else {}
    return kv3(abilities), kv3(heroes), Text(load_loc(sha), ru)


def as_number(value) -> tuple[float | None, str]:
    if isinstance(value, bool):
        return None, ""
    if isinstance(value, (int, float)):
        return float(value), ""
    return number(value)


def label(text: Text, token: str, prop: dict | None = None) -> dict:
    display = (prop or {}).get("m_eDisplayType") or ""
    found = text.get(f"{token}_label") or (text.get(f"StatDesc_{display[1:]}") if display else None)
    if found:
        found = {k: v[:1].upper() + v[1:] for k, v in found.items()}
        return found if "ru" in found else {"en": found["en"]}
    return {"en": words(token)}


def direction(name: str, old: float, new: float) -> str:
    if NEUTRAL.search(name):
        return ""
    better = new < old if LOWER_IS_BETTER.search(name) else new > old
    return "buff" if better else "nerf"


def change(lbl: dict, old: float | None, new: float | None, unit: str, name: str, **extra) -> dict:
    row = {"label": lbl, "old": None if old is None else fmt(old), "new": None if new is None else fmt(new), "unit": unit}
    if old is not None and new is not None and (d := direction(name, old, new)):
        row["dir"] = d
    return row | extra


def unit_of(name: str, prop: dict, raw_unit: str) -> str:
    if re.search(r"Percent|Pct", name):
        return "%"
    if raw_unit == "m" or prop.get("m_eDisplayUnits") == "EDisplayUnit_Meters":
        return "м"
    if prop.get("m_eDisplayUnits") == "EDisplayUnit_MetersPerSecond":
        return "м/с"
    return "с" if TIME.search(name) else ""


# ── Abilities and items ────────────────────────────────

def spirit_scale(prop: dict) -> float | None:
    scale = prop.get("m_subclassScaleFunction") or {}
    if scale.get("m_eSpecificStatScaleType") == "ETechPower" and isinstance(scale.get("m_flStatScale"), (int, float)):
        return float(scale["m_flStatScale"])
    return None


def upgrades(ability: dict) -> list[dict]:
    """Tier → {(property, kind): bonus}."""
    out = []
    for tier in ability.get("m_vecAbilityUpgrades") or []:
        bonuses = {}
        for up in (tier or {}).get("m_vecPropertyUpgrades") or []:
            value, _ = as_number(up.get("m_strBonus"))
            if value is not None and up.get("m_strPropertyName"):
                bonuses[(up["m_strPropertyName"], up.get("m_eUpgradeType") or "")] = value
        out.append(bonuses)
    return out


def ability_changes(old: dict, new: dict, text: Text) -> list[dict]:
    rows = []
    old_props, new_props = old.get("m_mapAbilityProperties") or {}, new.get("m_mapAbilityProperties") or {}
    for name in sorted(old_props.keys() | new_props.keys()):
        o, n = old_props.get(name) or {}, new_props.get(name) or {}
        if not isinstance(o, dict) or not isinstance(n, dict) or NOISE.search(name):
            continue
        prop = n or o
        token = prop.get("m_strLocTokenOverride") or name
        (ov, ounit), (nv, nunit) = as_number(o.get("m_strValue")), as_number(n.get("m_strValue"))
        unit = unit_of(name, prop, nunit or ounit)
        if ov is None and nv is None:
            continue
        if (ov or 0) != (nv or 0) and not (ov is None and nv == 0) and not (nv is None and ov == 0):
            status = {"status": "removed"} if nv is None else {"status": "added"} if ov is None else {}
            rows.append(change(label(text, token, prop), ov, nv, unit, name, **status))
        os_, ns = spirit_scale(o), spirit_scale(n)
        if os_ != ns and (os_ or ns):
            rows.append(change(suffixed(label(text, token, prop), *SPIRIT), os_, ns, "", name))
    old_up, new_up = upgrades(old), upgrades(new)
    for tier in range(max(len(old_up), len(new_up))):
        o = old_up[tier] if tier < len(old_up) else {}
        n = new_up[tier] if tier < len(new_up) else {}
        for key in sorted(o.keys() | n.keys()):
            if o.get(key) == n.get(key):
                continue
            name, kind = key
            prop = new_props.get(name) or old_props.get(name) or {}
            lbl = label(text, prop.get("m_strLocTokenOverride") or name if isinstance(prop, dict) else name,
                        prop if isinstance(prop, dict) else None)
            suffix = UPGRADE_KIND.get(kind)
            lbl = suffixed(lbl, f", {suffix[0]}", f", {suffix[1]}") if suffix else lbl
            unit = "" if suffix else unit_of(name, prop if isinstance(prop, dict) else {}, "")
            rows.append(change(lbl, o.get(key), n.get(key), unit, name, tier=tier + 1))
    return rows


def weapon_changes(old: dict, new: dict, text: Text) -> list[dict]:
    rows = []
    oi = ((old.get("m_mapWeaponInfos") or {}).get("primary")) or old.get("m_WeaponInfo") or {}
    ni = ((new.get("m_mapWeaponInfos") or {}).get("primary")) or new.get("m_WeaponInfo") or {}
    for field, (ru, en) in WEAPON_FIELDS.items():
        (ov, _), (nv, _) = as_number(oi.get(field)), as_number(ni.get(field))
        if ov is not None and nv is not None and ov != nv:
            rows.append(change({"ru": ru, "en": en}, ov, nv, WEAPON_UNITS.get(field, ""), field))
    return rows + ability_changes(old, new, text)


def item_changes(old: dict, new: dict, text: Text) -> list[dict]:
    rows = []
    ot, nt = str(old.get("m_iItemTier", ""))[-1:], str(new.get("m_iItemTier", ""))[-1:]
    if ot.isdigit() and nt.isdigit() and ot != nt:
        rows.append(change({"ru": "Тир", "en": "Tier"}, float(ot), float(nt), "", "Tier"))
    oc, nc = list(old.get("m_vecComponentItems") or []), list(new.get("m_vecComponentItems") or [])
    if oc != nc:
        names = lambda keys: ", ".join(text.raw(k, "en") or k for k in keys) or "—"
        rows.append({"label": {"ru": "Компоненты", "en": "Components"}, "old": names(oc), "new": names(nc), "unit": ""})
    return rows + ability_changes(old, new, text)


# ── Heroes ─────────────────────────────────────────────

def stat_rows(old: dict, new: dict, names: dict, text: Text, scaling: bool = False) -> list[dict]:
    rows = []
    for key in sorted(old.keys() | new.keys()):
        o, n = old.get(key), new.get(key)
        if scaling:
            o, n = (o or {}).get("flScale"), (n or {}).get("flScale")
        (ov, _), (nv, _) = as_number(o), as_number(n)
        if ov is None and nv is None:
            continue
        ov, nv = ov or 0.0, nv or 0.0  # a stat that is missing counts as zero
        if ov == nv:
            continue
        ru, en = names.get(key) or (None, None)
        if ru:
            lbl = {"ru": ru, "en": en}
        else:
            lbl = label(text, f"StatDesc_{key[1:]}" if key.startswith("E") else key)
            if lbl["en"].startswith("Stat Desc_"):  # not in the localization: plain words of the stat
                lbl = {"en": words(key[1:] if key.startswith("E") else key)}
        if scaling:
            lbl = suffixed(lbl, *SPIRIT)
        rows.append(change(lbl, ov, nv, "", key))
    return rows


def hero_changes(old: dict, new: dict, text: Text) -> list[dict]:
    return (stat_rows(old.get("m_mapStartingStats") or {}, new.get("m_mapStartingStats") or {}, HERO_STATS, text)
            + stat_rows(old.get("m_mapStandardLevelUpUpgrades") or {}, new.get("m_mapStandardLevelUpUpgrades") or {},
                        LEVEL_UP, text)
            + stat_rows(old.get("m_mapScalingStats") or {}, new.get("m_mapScalingStats") or {}, HERO_STATS, text,
                        scaling=True))


def playable(hero: dict) -> bool:
    return isinstance(hero, dict) and not is_on(hero.get("m_bDisabled")) and bool(hero.get("m_mapBoundAbilities"))


# ── The whole build ────────────────────────────────────

def diff(parent: str, sha: str) -> dict:
    old_ab, old_he, _ = load(parent)
    new_ab, new_he, text = load(sha)
    name = lambda key: text.raw(key, "en") or text.raw(f"{key}:n", "en") or words(key)

    heroes = []
    for key in sorted(new_he):
        hero, before = new_he[key], old_he.get(key)
        if not key.startswith("hero_") or not playable(hero) or not playable(before):
            continue
        sections = []
        if rows := hero_changes(before, hero, text):
            sections.append({"kind": "stats", "name": "Характеристики", "changes": rows})
        weapon = hero["m_mapBoundAbilities"].get("ESlot_Weapon_Primary")
        if weapon and weapon in old_ab and weapon in new_ab:
            if rows := weapon_changes(old_ab[weapon], new_ab[weapon], text):
                sections.append({"kind": "weapon", "key": weapon, "name": "Оружие", "changes": rows})
        for slot_no, slot in enumerate(SIGNATURE_SLOTS, 1):
            ability = hero["m_mapBoundAbilities"].get(slot)
            if ability and ability in old_ab and ability in new_ab:
                if rows := ability_changes(old_ab[ability], new_ab[ability], text):
                    sections.append({"kind": "ability", "key": ability, "slot": slot_no, "name": name(ability),
                                     "changes": rows})
        if sections:
            heroes.append({"key": key, "hero": name(key), "sections": sections})

    items = []
    old_items, new_items = shop_items(old_ab), shop_items(new_ab)
    for key in sorted(new_items.keys() | old_items.keys(), key=lambda k: name(k)):
        if key not in old_items:
            items.append({"key": key, "name": name(key), "status": "added", "changes": []})
        elif key not in new_items:
            items.append({"key": key, "name": name(key), "status": "removed", "changes": []})
        elif rows := item_changes(old_items[key], new_items[key], text):
            items.append({"key": key, "name": name(key), "slot": SLOTS.get(new_items[key].get("m_eItemSlotType")),
                          "changes": rows})

    total = sum(len(s["changes"]) for h in heroes for s in h["sections"]) + sum(max(1, len(i["changes"])) for i in items)
    return {"heroes": heroes, "items": items, "total": total}


def lines(balance: dict, limit: int | None = None) -> list[str]:
    """Plain Russian text, one change per line (for logs, Telegram and tests)."""
    out = []
    arrow = {"buff": "🟢", "nerf": "🔴"}

    def row_text(r: dict) -> str:
        lbl = r["label"].get("ru") or r["label"]["en"]
        tier = f"Улучшение {r['tier']}: " if r.get("tier") else ""
        unit = f" {r['unit']}" if r.get("unit") else ""
        if r.get("status") == "removed":
            return f"▪️ {tier}{lbl}: убрано (было {r['old']}{unit})"
        if r.get("status") == "added":
            return f"▪️ {tier}{lbl}: новое — {r['new']}{unit}"
        old = "—" if r["old"] is None else r["old"]
        new = "—" if r["new"] is None else r["new"]
        return f"{arrow.get(r.get('dir'), '▪️')} {tier}{lbl}: {old} → {new}{unit}"

    for h in balance["heroes"]:
        out.append(f"\n{h['hero']}")
        for s in h["sections"]:
            for r in s["changes"]:
                where = "" if s["kind"] == "stats" else f"{s['name']} — "
                out.append(f"  {row_text(r).replace(' ', ' ' + where, 1) if where else row_text(r)}")
    if balance["items"]:
        out.append("\nПредметы")
        for i in balance["items"]:
            if i.get("status") == "added":
                out.append(f"  ✨ {i['name']}: новый предмет")
            elif i.get("status") == "removed":
                out.append(f"  ❌ {i['name']}: убран из магазина")
            for r in i["changes"]:
                out.append(f"  {row_text(r).replace(' ', ' ' + i['name'] + ' — ', 1)}")
    return out[:limit] if limit else out


def backfill() -> int:
    """Adds the balance section to build summaries written before it existed."""
    from .patches import OUT, entry, plural
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text("utf-8"))
    for item in index["builds"]:
        path = OUT / item["file"]
        summary = json.loads(path.read_text("utf-8"))
        if "balance" in summary or not summary.get("parent"):
            continue
        try:
            bal = diff(summary["parent"], summary["sha"]) if summary["counts"].get("vdata") else None
        except Missing:
            continue
        summary["balance"] = bal
        summary["counts"]["balance"] = bal["total"] if bal else 0
        if bal and bal["total"]:
            who = [h["hero"] for h in bal["heroes"]][:4] + ([f"предметы ({len(bal['items'])})"] if bal["items"] else [])
            line = f"Баланс: {plural(bal['total'], 'изменение', 'изменения', 'изменений')} — " + ", ".join(who)
            summary["headline"] = [line] + [h for h in summary["headline"] if not h.startswith("Параметры:")][:4]
        path.write_text(json.dumps(summary, ensure_ascii=False, separators=(",", ":")), "utf-8")
        item.update({k: v for k, v in entry(summary, item["file"]).items() if k in ("headline", "counts")})
        print(f"build {summary['build']}: balance {bal['total'] if bal else 0}")
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--backfill"]:
        sys.exit(backfill())
    result = diff(sys.argv[1], sys.argv[2])
    print("\n".join(lines(result)))
    print(f"\nвсего изменений: {result['total']}")
