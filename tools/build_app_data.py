#!/usr/bin/env python3
"""
Готовит данные для приложения: из манифестов таймингов делает app/data.js.

Отдельный файл, а не fetch манифеста, потому что приложение открывается с диска
(file://), а там fetch JSON запрещён политикой браузера. <script src> работает.

    python3 tools/build_app_data.py
    python3 tools/build_app_data.py --build build --out app/data.js
"""

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка data.js для приложения")
    ap.add_argument("--build", default="build", help="где лежат *.timings.json")
    ap.add_argument("--out", default="app/data.js")
    ap.add_argument("--audio-prefix", default="../build/",
                    help="как приложение видит папку с аудио")
    args = ap.parse_args()

    build = Path(args.build)
    manifests = sorted(build.glob("*.timings.json"))
    if not manifests:
        print(f"! в {build}/ нет манифестов - сначала собери аудио")
        return 1

    lessons = []
    for path in manifests:
        m = json.loads(path.read_text(encoding="utf-8"))
        audio = build / m["audio"]
        if not audio.exists():
            print(f"! {m['audio']} не найден, пропускаю")
            continue

        by_item = {}
        for step in m.get("steps", []):
            by_item.setdefault(step["item"], []).append(
                {"k": step["step"], "s": step["start_ms"], "e": step["end_ms"]})

        words = []
        for it in m["items"]:
            words.append({
                "i": it["index"], "id": it["id"], "kind": it["kind"],
                "gr": it["gr"], "ru": it.get("ru"), "tr": it.get("tr"),
                "syl": it.get("syl"), "sylp": it.get("syl_phrase"),
                "phrase": it.get("phrase"), "phrase_ru": it.get("phrase_ru"),
                "s": it["start_ms"], "e": it["end_ms"],
                "steps": by_item.get(it["index"], []),
            })

        lessons.append({
            "id": m["pack"], "tier": m.get("tier"), "title": m.get("title"),
            "theme": m.get("theme"), "level": m.get("level"),
            "audio": args.audio_prefix + m["audio"],
            "duration": m["duration_ms"],
            "counts": m.get("counts", {}),
            "words": words,
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("// Сгенерировано tools/build_app_data.py - руками не править.\n"
                   "window.LESSONS = " +
                   json.dumps(lessons, ensure_ascii=False, separators=(",", ":")) + ";\n",
                   encoding="utf-8")

    size = out.stat().st_size / 1024
    print(f"{out}: {len(lessons)} уроков, {size:.0f} КБ")
    for l in lessons:
        new = sum(1 for w in l["words"] if w["kind"] == "new")
        print(f"  {l['id']}: {l['duration']//60000}:{l['duration']//1000%60:02d}, "
              f"слов {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
