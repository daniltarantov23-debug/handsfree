#!/usr/bin/env python3
"""
Собирает СЕССИЮ произвольной длины: сколько минут слушаю и сколько из них повтор.

Дней и фиксированных уроков нет. Ты говоришь «сегодня два часа, из них час
повтора вчерашнего» - генератор берёт из потока следующие непройденные слова
на новый блок, а на повтор поднимает те, что давно не звучали.

Выход - ОДИН mp3 на всю сессию. Это принципиально: телефон играет один файл и
не глохнет при свёрнутом экране, а нарезка на клипы фоном не живёт (JS засыпает).

    python3 tools/build_session.py --minutes 120 --review 60
    python3 tools/build_session.py --minutes 60            # всё новое
    python3 tools/build_session.py --minutes 120 --review 60 --dry-run

Прогресс (что уже слышал и когда повторял) лежит в build/progress.json и
обновляется после каждой сборки.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_audio import build_tier, load_marked, VOICE_SETS, DEFAULT_VOICE_SET  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROGRESS = ROOT / "build" / "progress.json"

# Замерено: новое слово стоит ~14.5с (полный цикл + его фраза в финале),
# короткий цикл повтора ~3.7с. Нужно только для прикидки в --dry-run.
SEC_NEW = 14.5
SEC_REVIEW = 3.7


def load_progress() -> dict:
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"session": 0, "words": {}}


def save_progress(p: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def load_stream(course_path: Path) -> list:
    course = json.loads(course_path.read_text(encoding="utf-8"))
    base = course_path.parent
    stream, seen = [], set()
    for entry in course["packs"]:
        pack = json.loads((base / entry).read_text(encoding="utf-8"))
        for item in pack.get("items") or []:
            key = item.get("id") or item["gr"]
            if key in seen:
                raise SystemExit(f"слово '{key}' в курсе дважды")
            seen.add(key)
            item = dict(item)
            item.setdefault("_theme", pack.get("theme"))
            stream.append(item)
    return stream


def pick_review(stream: list, progress: dict, want_seconds: float, marked_ids: list) -> list:
    """
    Что поднять на повтор. Порядок: сначала отмеченные двойным касанием, потом
    те, что дольше всех не звучали и реже всех повторялись - так слово само
    возвращается с растущими интервалами, без ручного расписания.
    """
    words = progress.get("words", {})
    by_id = {(i.get("id") or i["gr"]): i for i in stream}
    order = []

    for key in marked_ids:
        if key in by_id:
            order.append(key)
    rest = [k for k in words if k not in order and k in by_id]
    rest.sort(key=lambda k: (words[k].get("reviews", 0), words[k].get("last", 0)))
    order += rest

    room = int(want_seconds / SEC_REVIEW) + 5      # с запасом, точный отрез - в сборке
    return [{k: v for k, v in by_id[key].items() if k in {"id", "gr", "ru", "tr", "audio"}}
            for key in order[:room]]


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка сессии произвольной длины")
    ap.add_argument("--course", default="content/course.json")
    ap.add_argument("--minutes", type=float, required=True, help="вся сессия, минут")
    ap.add_argument("--review", type=float, default=0,
                    help="сколько из них отдать повтору, минут")
    ap.add_argument("--marked", help="JSON с id, отмеченными двойным касанием")
    ap.add_argument("--out", default="build")
    ap.add_argument("--cache", default="build/.cache")
    ap.add_argument("--format", choices=["mp3", "m4a", "wav"], default="mp3")
    ap.add_argument("--name", default="", help="имя файла; по умолчанию session-NN")
    ap.add_argument("--voice", default=DEFAULT_VOICE_SET, choices=sorted(VOICE_SETS),
                    help="голосовой набор; файл получает суффикс голоса")
    ap.add_argument("--both-voices", action="store_true",
                    help="собрать сессию всеми нейросетевыми голосами - чтобы "
                         "выбирать голос прямо в приложении")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()

    if args.review >= args.minutes:
        ap.error("--review должен быть меньше --minutes")

    course_path = ROOT / args.course if not Path(args.course).is_absolute() else Path(args.course)
    stream = load_stream(course_path)
    progress = load_progress()
    heard = progress.get("words", {})

    fresh_words = [i for i in stream if (i.get("id") or i["gr"]) not in heard]
    marked_ids = []
    if args.marked:
        marked_ids = [m["id"] for m in load_marked(Path(args.marked), stream)]

    review_s = args.review * 60
    new_s = (args.minutes - args.review) * 60
    review_items = pick_review(stream, progress, review_s, marked_ids) if review_s else []

    need_new = int(new_s / SEC_NEW)
    print(f"сессия {args.minutes:.0f} мин = {args.minutes - args.review:.0f} новых + "
          f"{args.review:.0f} повтора")
    print(f"  в потоке непройденных слов: {len(fresh_words)}, нужно примерно {need_new}")
    if len(fresh_words) < need_new:
        не_хватает = (need_new - len(fresh_words)) * SEC_NEW / 60
        print(f"  ! новых слов не хватает на {не_хватает:.0f} мин - "
              f"сессия выйдет короче. Нужен ещё контент.")
    if review_s:
        print(f"  на повтор поднято слов: {len(review_items)} "
              f"(отмеченных касанием: {len(marked_ids)})")

    session_no = progress.get("session", 0) + 1
    base_name = args.name or f"session-{session_no:02d}"
    # Голос попадает в имя файла: приложение по нему и переключает озвучку.
    voices_to_build = ([v for v in sorted(VOICE_SETS) if VOICE_SETS[v]["engine"] == "piper"]
                       if args.both_voices else [args.voice])

    pack = {
        "id": base_name,
        "title": f"Сессия {session_no}",
        "theme": (fresh_words[0].get("_theme") if fresh_words else "Повтор"),
        "level": "Обиход",
        "items": [{k: v for k, v in w.items() if not k.startswith("_")} for w in fresh_words],
        "review": review_items,
    }

    results = []
    for voice in voices_to_build:
        name = f"{base_name}-{voice}"
        print(f"\n--- голос: {VOICE_SETS[voice]['title']} ---")
        results.append(build_tier(pack, str(course_path), int(args.minutes), pack["items"],
                                  review_items, Path(args.out), Path(args.cache),
                                  args.format, args.dry_run, args.fresh,
                                  target_s=args.minutes * 60, review_s=review_s,
                                  label=name, voice_set=voice))
    if args.dry_run:
        return 0
    result = results[0]
    name = f"{base_name}-{voices_to_build[0]}"

    # Обновляем прогресс: что прозвучало новым, что поднималось на повтор.
    manifest = json.loads((Path(args.out) / f"{name}.timings.json").read_text(encoding="utf-8"))
    words = progress.setdefault("words", {})
    new_ids = [i["id"] for i in manifest["items"] if i["kind"] == "new"]
    rev_ids = [i["id"] for i in manifest["items"] if i["kind"] == "marked"]
    for key in new_ids:
        words.setdefault(key, {"heard": session_no, "reviews": 0, "last": session_no})
    for key in rev_ids:
        w = words.setdefault(key, {"heard": session_no, "reviews": 0, "last": session_no})
        w["reviews"] = w.get("reviews", 0) + 1
        w["last"] = session_no
    progress["session"] = session_no
    save_progress(progress)

    total = len(words)
    print(f"\nпрогресс: слышано слов {total} из {len(stream)}; "
          f"в этой сессии новых {len(new_ids)}, повторено {len(rev_ids)}")
    print(f"файл: {result['audio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
