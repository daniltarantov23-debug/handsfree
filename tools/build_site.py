#!/usr/bin/env python3
"""
Собирает папку site/ для выкладки на GitHub Pages.

Кладёт туда приложение, данные и аудио сессий. Всё статическое: GitHub Pages
отдаёт Range-запросы, поэтому перемотка и фоновое воспроизведение работают.

    python3 tools/build_site.py
    python3 tools/build_site.py --only session-01

Дальше - git. Инструкция в DEPLOY.md.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка site/ для GitHub Pages")
    ap.add_argument("--only", default="", help="выложить только эту сессию")
    ap.add_argument("--out", default="site")
    args = ap.parse_args()

    out = ROOT / args.out
    data_js = ROOT / "app" / "data.js"
    if not data_js.exists():
        print("! нет app/data.js - сначала python3 tools/build_app_data.py")
        return 1

    lessons = json.loads(data_js.read_text(encoding="utf-8").split("=", 1)[1].rstrip(";\n"))
    if args.only:
        lessons = [l for l in lessons if l["id"] == args.only]
    if not lessons:
        print("! нечего выкладывать")
        return 1

    out.mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(exist_ok=True)
    # Без этого GitHub Pages прогоняет всё через Jekyll и может съесть файлы.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    total = 0
    for lesson in lessons:
        src = ROOT / "build" / Path(lesson["audio"]).name
        if not src.exists():
            print(f"! нет {src}, пропускаю")
            continue
        dst = out / "audio" / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
        lesson["audio"] = "audio/" + src.name
        total += src.stat().st_size
        print(f"  {src.name}: {src.stat().st_size/1e6:.1f} МБ")

    (out / "data.js").write_text(
        "// Сгенерировано tools/build_site.py\nwindow.LESSONS = " +
        json.dumps(lessons, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8")

    html = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    # На телефоне это добавляется на домашний экран и работает как приложение.
    html = html.replace("<title>ΑΚΟΥ</title>", (
        "<title>ΑΚΟΥ</title>\n"
        "<meta name='theme-color' content='#0B2739'>\n"
        "<meta name='apple-mobile-web-app-capable' content='yes'>\n"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>\n"
        "<meta name='apple-mobile-web-app-title' content='ΑΚΟΥ'>"))
    html = html.replace("Здесь - кнопки и клавиши, чтобы поклацать с ноутбука.",
                        "Здесь - кнопки, если слушаешь без наушников.")
    (out / "index.html").write_text(html, encoding="utf-8")

    print(f"\n{out}: {len(lessons)} сессий, аудио {total/1e6:.0f} МБ")
    print("дальше - DEPLOY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
