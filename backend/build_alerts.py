"""Telegram alerts about new Deadlock builds.

    python -m backend.build_alerts            # announce new builds as soon as their summary exists
    python -m backend.build_alerts --media    # add the before/after line once their media is ready

The index remembers the newest build already announced (notifiedUpTo), so every build is
announced once. On the first run nothing old is sent: it only sets that mark.
"""
import asyncio
import html
import json
import sys

import httpx

from . import config, telegram
from .patches import OUT, plural

SIZE = {"major": "Крупный патч", "update": "Обновление", "hotfix": "Небольшая правка"}


def format_build(entry: dict, summary: dict) -> str:
    c = entry["counts"]
    lines = [f"🛠 <b>Новый билд Deadlock {entry['build']}</b> · {SIZE.get(entry['size'], '')}"]
    if entry.get("newHeroes"):
        lines.append(f"🆕 Новый герой в файлах: <b>{html.escape(', '.join(entry['newHeroes']))}</b>")
    lines.append("")
    lines += [f"• {html.escape(h)}" for h in entry["headline"]]
    if summary.get("media"):
        models = sum(m["kind"] == "model" for m in summary["media"].values())
        textures = len(summary["media"]) - models
        parts = [p for p in (models and plural(models, "модель", "модели", "моделей") + " в 3D",
                             textures and plural(textures, "текстура", "текстуры", "текстур")) if p]
        lines += ["", f"🖼 Было → стало: {', '.join(parts)}"]
    stats = [f"+{c['added']} новых", f"{c['changed']} изменено", f"{c['removed']} удалено файлов"]
    if c["strings"]:
        stats.append(f"{c['strings']} строк текста")
    if c["vdata"]:
        stats.append(f"{c['vdata']} параметров")
    lines += ["", "📊 " + " · ".join(stats)]
    url = f"{config.SITE_URL}changes.html?build={entry['file'].removesuffix('.json')}"
    lines += ["", f'<a href="{html.escape(url)}">Смотреть изменения на сайте</a>']
    return "\n".join(lines)


async def add_media(index: dict) -> None:
    """Edits announcements sent before the models and textures were ready."""
    async with httpx.AsyncClient() as client:
        for entry in index["builds"]:
            alert = entry.get("alert")
            if not alert or alert.get("media") or not entry.get("media"):
                continue
            summary = json.loads((OUT / entry["file"]).read_text("utf-8"))
            if await telegram.edit(client, alert["id"], format_build(entry, summary)):
                alert["media"] = True
                print(f"added media to the alert of build {entry['build']}")


async def main() -> int:
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text("utf-8"))
    builds = index["builds"]
    if not builds:
        return 0
    if "--media" in sys.argv:
        if telegram.enabled():
            await add_media(index)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
        return 0
    newest = max(b["time"] for b in builds)
    mark = index.get("notifiedUpTo")
    if mark is None or not telegram.enabled():
        index["notifiedUpTo"] = newest
    else:
        async with httpx.AsyncClient() as client:
            for entry in sorted((b for b in builds if b["time"] > mark), key=lambda b: b["time"]):
                if entry["size"] != "empty":
                    summary = json.loads((OUT / entry["file"]).read_text("utf-8"))
                    message_id = await telegram.post(client, format_build(entry, summary))
                    if message_id is None:
                        break  # try again on the next run
                    entry["alert"] = {"id": message_id, "media": bool(entry.get("media"))}
                    print(f"announced build {entry['build']}")
                index["notifiedUpTo"] = entry["time"]
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
