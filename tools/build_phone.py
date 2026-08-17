#!/usr/bin/env python3
"""
Собирает приложение в ОДИН самодостаточный файл для телефона.

Внутрь запекается всё: данные урока, аудио как data-URI и шрифты. Внешних
запросов нет вообще - файл работает без интернета и в песочнице, где внешние
домены заблокированы (артефакт claude.ai, например).

    python3 tools/build_phone.py                 # урок 10 минут
    python3 tools/build_phone.py --tier 20       # урок 20 минут (тяжелее)

Шрифты берутся из кэша app/fonts/, при первом запуске качаются с Google Fonts.
"""

import argparse
import base64
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "app" / "fonts"

# Ссылки на файлы шрифтов не угадываем - берём настоящий CSS у Google Fonts
# и вынимаем из него и адреса, и unicode-range. Нужны только греческий,
# кириллица и латиница: остальные подмножества в файл не тащим.
FONTS_CSS = ("https://fonts.googleapis.com/css2?"
             "family=EB+Garamond:wght@400..800&family=Manrope:wght@400..800"
             "&family=Unbounded:wght@400..800&display=swap")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
WANTED = ("greek", "cyrillic", "latin")


def parse_css(css: str) -> list:
    """Вынимает из CSS блоки @font-face: семейство, вес, диапазон, адрес."""
    faces = []
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.S):
        family = re.search(r"font-family:\s*'([^']+)'", block)
        weight = re.search(r"font-weight:\s*([^;]+);", block)
        rng = re.search(r"unicode-range:\s*([^;]+);", block)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if family and url and rng:
            faces.append({"family": family.group(1),
                          "weight": (weight.group(1).strip() if weight else "400"),
                          "range": rng.group(1).strip(),
                          "url": url.group(1)})
    return faces


def subset_name(unicode_range: str) -> str:
    """Определяет подмножество по диапазону - в CSS они идут без имён."""
    if "U+0370" in unicode_range or "U+1F00" in unicode_range:
        return "greek"
    if "U+0400" in unicode_range:
        return "cyrillic"
    if "U+0000-00FF" in unicode_range:
        return "latin"
    return "other"


def fetch_fonts() -> str:
    """Возвращает готовый CSS с зашитыми в base64 шрифтами."""
    FONTS.mkdir(parents=True, exist_ok=True)
    cached_css = FONTS / "google.css"
    if not cached_css.exists():
        r = subprocess.run(["curl", "-sSfL", "-A", UA, FONTS_CSS],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ! CSS шрифтов не скачался: {r.stderr.strip()[:150]}")
            return ""
        cached_css.write_text(r.stdout, encoding="utf-8")
    faces = parse_css(cached_css.read_text(encoding="utf-8"))

    out, taken = [], 0
    for f in faces:
        sub = subset_name(f["range"])
        if sub not in WANTED:
            continue
        name = f"{f['family'].replace(' ', '')}-{sub}-{f['weight'].replace(' ', '')}.woff2"
        path = FONTS / name
        if not path.exists():
            r = subprocess.run(["curl", "-sSfL", "-A", UA, "-o", str(path), f["url"]],
                               capture_output=True, text=True)
            if r.returncode != 0 or not path.exists() or path.stat().st_size < 500:
                path.unlink(missing_ok=True)
                print(f"  ! {name} не скачался")
                continue
        data = base64.b64encode(path.read_bytes()).decode()
        out.append(f"@font-face{{font-family:'{f['family']}';font-style:normal;"
                   f"font-weight:{f['weight']};font-display:swap;"
                   f"src:url(data:font/woff2;base64,{data}) format('woff2');"
                   f"unicode-range:{f['range']};}}")
        taken += path.stat().st_size
    print(f"  шрифтов вшито: {len(out)}, {taken/1024:.0f} КБ")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Один файл приложения для телефона")
    ap.add_argument("--tier", type=int, default=10, help="длина урока: 10, 20 или 40")
    ap.add_argument("--out", default="app/phone.html")
    args = ap.parse_args()

    src = (ROOT / "app" / "index.html").read_text(encoding="utf-8")
    data = (ROOT / "app" / "data.js").read_text(encoding="utf-8")
    lessons = json.loads(data.split("=", 1)[1].rstrip(";\n"))

    lesson = next((l for l in lessons if l.get("tier") == args.tier), None)
    if not lesson:
        print(f"! урока на {args.tier} минут нет, собери его сначала")
        return 1

    audio_path = ROOT / "build" / Path(lesson["audio"]).name
    if not audio_path.exists():
        print(f"! нет файла {audio_path}")
        return 1

    print(f"  урок {lesson['id']}, аудио {audio_path.stat().st_size/1e6:.1f} МБ")
    b64 = base64.b64encode(audio_path.read_bytes()).decode()
    lesson = dict(lesson, audio=f"data:audio/mpeg;base64,{b64}")

    html = src
    # Внешние шрифты в песочнице заблокированы - вшиваем их в файл.
    css = fetch_fonts()
    html = re.sub(r'<link rel="preconnect"[^>]*>\s*', "", html)
    html = re.sub(r'<link href="https://fonts\.googleapis\.com[^>]*>',
                  "<style>" + css + "</style>", html)

    # Данные вместо внешнего data.js.
    html = html.replace('<script src="data.js"></script>',
                        "<script>window.LESSONS = " +
                        json.dumps([lesson], ensure_ascii=False, separators=(",", ":")) +
                        ";</script>")
    # На телефоне подсказки про клавиатуру и путь к файлу не нужны.
    html = html.replace("Здесь - кнопки и клавиши, чтобы поклацать с ноутбука.",
                        "Здесь - кнопки, чтобы попробовать без наушников.")
    html = html.replace("<title>ΑΚΟΥ</title>",
                        f"<title>ΑΚΟΥ</title>\n<meta name='theme-color' content='#0B2739'>"
                        f"<meta name='apple-mobile-web-app-capable' content='yes'>")

    out = ROOT / args.out
    out.write_text(html, encoding="utf-8")
    size = out.stat().st_size / 1e6
    print(f"\n{out}: {size:.1f} МБ, урок {args.tier} мин, внешних запросов 0")
    if size > 15:
        print("  ! больше 15 МБ - для артефакта многовато, возьми --tier 10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
