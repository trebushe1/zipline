"""What changed inside a Deadlock model, in words, when a 3D view cannot show it.

Two questions for a model that changed between builds:
  - does it look different? (look_signature: geometry, textures, materials and the
    skeleton by bone name; GLB node order and file layout do not count)
  - what changed inside? (inner_changes: ragdoll physics, built-in animations,
    morphs, bones, body groups, skins and settings, read from the compiled file)
"""
import array
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path

# ── Look ───────────────────────────────────────────────


def _glb(data: bytes) -> tuple[dict, bytes]:
    n = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + n])
    rest = 20 + n
    binary = data[rest + 8:rest + 8 + struct.unpack_from("<I", data, rest)[0]] if rest < len(data) else b""
    return doc, binary


def _view(doc: dict, binary: bytes, index: int | None) -> bytes:
    if index is None:
        return b""
    v = doc["bufferViews"][index]
    start = v.get("byteOffset", 0)
    return binary[start:start + v["byteLength"]]


def _joint_names(doc: dict, binary: bytes, accessor: dict, joint_names: list[str]) -> str:
    """Skin weights by bone name: the exporter numbers bones in file order, which Valve may shuffle."""
    raw = _view(doc, binary, accessor.get("bufferView"))
    values = array.array("B" if accessor["componentType"] == 5121 else "H")
    values.frombytes(raw[:len(raw) - len(raw) % values.itemsize])
    return hashlib.sha1("\0".join(joint_names[v] if v < len(joint_names) else "?" for v in values).encode()).hexdigest()


def look_signature(glb: bytes) -> str:
    """A hash of what the model looks like."""
    doc, binary = _glb(glb)
    acc = doc.get("accessors", [])
    mats = doc.get("materials", [])
    nodes = doc.get("nodes", [])
    skins = doc.get("skins", [])
    joint_names = [nodes[j].get("name") for j in skins[0]["joints"]] if skins else []
    meshes = []
    for mesh in doc.get("meshes", []):
        for p in mesh["primitives"]:
            data = {k: _joint_names(doc, binary, acc[i], joint_names) if k.startswith("JOINTS_")
                    else hashlib.sha1(_view(doc, binary, acc[i].get("bufferView"))).hexdigest()
                    for k, i in p["attributes"].items()}
            if "indices" in p:
                data["indices"] = hashlib.sha1(_view(doc, binary, acc[p["indices"]].get("bufferView"))).hexdigest()
            data["material"] = mats[p["material"]].get("name") if "material" in p else None
            meshes.append(json.dumps(data, sort_keys=True))
    images = sorted(hashlib.sha1(_view(doc, binary, i.get("bufferView"))).hexdigest() for i in doc.get("images", []))
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    rounded = lambda v: [round(x, 3) for x in v] if v else v
    bones = sorted(json.dumps([n.get("name"), nodes[parent[i]].get("name") if i in parent else None,
                               rounded(n.get("translation")), rounded(n.get("rotation")), rounded(n.get("scale"))])
                   for i, n in enumerate(nodes) if "mesh" not in n)
    materials = sorted(json.dumps({k: v for k, v in m.items() if k != "extras"}, sort_keys=True) for m in mats)
    return hashlib.sha1(json.dumps([sorted(meshes), images, materials, bones]).encode()).hexdigest()


# ── Inside ─────────────────────────────────────────────

ANIM_BLOCKS = {"ANIM", "AGRP", "ASEQ"}


def _blocks(data: bytes) -> dict[str, int]:
    """Block type -> total size, from the header of a compiled resource."""
    sizes: dict[str, int] = {}
    try:
        _, _, _, offset, count = struct.unpack_from("<IHHII", data, 0)
        pos = 8 + offset
        for _ in range(count):
            kind = data[pos:pos + 4].decode("ascii", "replace")
            sizes[kind] = sizes.get(kind, 0) + struct.unpack_from("<II", data, pos + 4)[1]
            pos += 12
    except struct.error:
        pass
    return sizes


def _dump(vrf: str, path: Path, block: str) -> str:
    res = subprocess.run([vrf, "-i", str(path), "-b", block], capture_output=True, text=True)
    return res.stdout


def _bracket(text: str, key: str) -> str:
    """The [...] (or {...}) value that follows `key =`, or ''."""
    m = re.search(rf"\b{re.escape(key)} =\s*", text)
    if not m:
        return ""
    start = m.end()
    if start >= len(text) or text[start] not in "[{":
        return ""
    depth = 0
    for i in range(start, len(text)):
        depth += text[i] in "[{"
        depth -= text[i] in "]}"
        if depth == 0:
            return text[start:i + 1]
    return ""


def _strings(text: str, key: str) -> list[str]:
    return re.findall(r'"([^"]*)"', _bracket(text, key))


def _names(text: str, key: str) -> list[str]:
    return re.findall(r'\bm_name = "([^"]*)"', _bracket(text, key))


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    return f"{n} {one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many}"


def _listed(names) -> str:
    names = sorted(names)
    return ", ".join(names[:8]) + (f" и ещё {len(names) - 8}" if len(names) > 8 else "")


def _physics(before: str, after: str) -> list[str]:
    count = lambda text, what: len(re.findall(rf"\b{what} = ", text))
    parts = lambda text: count(text, "m_Capsule") + count(text, "m_Hull") + count(text, "m_Sphere")
    b_parts, a_parts = parts(before), parts(after)
    b_joints, a_joints = count(before, "m_Frame1"), count(after, "m_Frame1")
    b_bodies, a_bodies = set(_strings(before, "m_boneNames")), set(_strings(after, "m_boneNames"))
    notes = []
    if (b_parts, b_joints) != (a_parts, a_joints):
        notes.append(f"Физика тела (рэгдолл): частей {b_parts} → {a_parts}, шарниров {b_joints} → {a_joints}")
    else:
        notes.append("Физика тела (рэгдолл и столкновения) настроена заново")
    if a_bodies - b_bodies:
        notes.append(f"Своя физика у: {_listed(a_bodies - b_bodies)}")
    if b_bodies - a_bodies:
        notes.append(f"Убрана физика у: {_listed(b_bodies - a_bodies)}")
    return notes


def _settings(before: str, after: str) -> list[str]:
    notes = []
    b_bones, a_bones = set(_strings(before, "m_boneName")), set(_strings(after, "m_boneName"))
    if a_bones - b_bones:
        notes.append(f"Новые кости: {_listed(a_bones - b_bones)}")
    if b_bones - a_bones:
        notes.append(f"Удалены кости: {_listed(b_bones - a_bones)}")
    b_groups, a_groups = set(_strings(before, "m_meshGroups")), set(_strings(after, "m_meshGroups"))
    if a_groups - b_groups:
        notes.append(f"Новые части модели (bodygroups): {_listed(a_groups - b_groups)}")
    if b_groups - a_groups:
        notes.append(f"Убраны части модели: {_listed(b_groups - a_groups)}")
    b_skins, a_skins = set(_names(before, "m_materialGroups")), set(_names(after, "m_materialGroups"))
    if a_skins - b_skins:
        notes.append(f"Новые варианты окраски (скины): {_listed(a_skins - b_skins)}")
    if b_skins - a_skins:
        notes.append(f"Убраны варианты окраски: {_listed(b_skins - a_skins)}")
    kv = lambda text: re.search(r'm_keyValueText = ("""[\s\S]*?"""|"[^"]*")', text)
    b_kv, a_kv = kv(before), kv(after)
    if (b_kv and b_kv[1]) != (a_kv and a_kv[1]):
        notes.append("Изменены игровые настройки модели (keyvalues)")
    return notes


def inner_changes(vrf: str, before: Path, after: Path) -> list[str]:
    """Plain-language notes on what changed inside a compiled model."""
    b_blocks, a_blocks = _blocks(before.read_bytes()), _blocks(after.read_bytes())
    changed = {k for k in b_blocks.keys() | a_blocks.keys() if b_blocks.get(k) != a_blocks.get(k)}
    notes = []
    if "PHYS" in changed:
        notes += _physics(_dump(vrf, before, "PHYS"), _dump(vrf, after, "PHYS"))
    if changed & ANIM_BLOCKS:
        delta = sum(a_blocks.get(k, 0) - b_blocks.get(k, 0) for k in ANIM_BLOCKS)
        size = f" ({'+' if delta >= 0 else '−'}{abs(delta) // 1024} КБ)" if abs(delta) >= 1024 else ""
        notes.append(f"Изменены встроенные анимации{size}")
    if "MRPH" in changed:
        notes.append("Изменена мимика (морфы лица и тела)")
    if "DATA" in changed:
        notes += _settings(_dump(vrf, before, "DATA"), _dump(vrf, after, "DATA"))
    return notes
