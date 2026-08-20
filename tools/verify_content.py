#!/usr/bin/env python3
"""
Проверка контента курса до синтеза звука.

Ловит то, что глазами не видно, а на слух вылезает уроком позже:
смешанные алфавиты (греческая ε внутри русского слова), пропущенный тонос,
фразу без самого слова, дубли слов между пакетами.

    python3 tools/verify_content.py content/*.json
    python3 tools/verify_content.py content/base-01.json --strict
"""

import argparse
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path

GREEK = r"Ͱ-Ͽἀ-῿"
CYRIL = r"Ѐ-ӿ"
ACCENTED = "άέήίόύώΐΰΆΈΉΊΌΎΏ"
# Буквосочетания, которые звучат как один слог.
DIGRAPHS = ("ου", "ΟΥ", "αι", "ει", "οι", "υι", "αυ", "ευ", "ηυ")
VOWELS = "αεηιουωάέήίόύώϊϋΐΰ"

GREEK_FIELDS = ("gr", "phrase")
RU_FIELDS = ("ru", "phrase_ru")
LAT_FIELDS = ("tr",)   # транслитерация - только латиница


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn")


def syllables(word: str) -> int:
    """Грубый счёт слогов: гласные группы, дифтонги считаются за одну."""
    w = word.lower()
    for d in DIGRAPHS:
        w = w.replace(d.lower(), "")
    count, prev_vowel = 0, False
    for ch in w:
        is_vowel = ch == "" or ch in VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    return count


def check_item(pack_id: str, idx: int, item: dict, seen: dict, seen_words: dict,
               seen_bare: dict, errors: list, warns: list):
    where = f"{pack_id} #{idx} ({item.get('gr', '?')})"

    for field in ("gr", "ru"):
        if not item.get(field):
            errors.append(f"{where}: нет обязательного поля '{field}'")

    # 1. Смешанные алфавиты. Ровно этот баг был в сохранённом прототипе.
    for field in GREEK_FIELDS:
        value = item.get(field)
        if value and re.search(f"[{CYRIL}]", value):
            bad = re.findall(f"\\S*[{CYRIL}]\\S*", value)
            errors.append(f"{where}: в греческом поле '{field}' кириллица: {bad}")
    for field in RU_FIELDS:
        value = item.get(field)
        if value and re.search(f"[{GREEK}]", value):
            bad = re.findall(f"\\S*[{GREEK}]\\S*", value)
            errors.append(f"{where}: в русском поле '{field}' греческие буквы: {bad}")

    # Транслитерация - латиница: греческая или русская буква тут почти всегда опечатка.
    for field in LAT_FIELDS:
        value = item.get(field)
        if value and re.search(f"[{GREEK}{CYRIL}]", value):
            bad = re.findall(f"\\S*[{GREEK}{CYRIL}]\\S*", value)
            errors.append(f"{where}: в латинском поле '{field}' не латиница: {bad}")

    # 2. Тонос. В многосложном греческом слове ударение обязательно.
    for field in GREEK_FIELDS:
        value = item.get(field)
        if not value:
            continue
        for word in re.findall(f"[{GREEK}]+", value):
            if syllables(word) >= 2 and not any(c in ACCENTED for c in word):
                warns.append(f"{where}: '{word}' многосложное, но без тоноса ('{field}')")

    # 3. Фраза должна содержать само слово - хотя бы корнем (язык флективный).
    phrase, word = item.get("phrase"), item.get("gr")
    if phrase and word:
        flat = strip_accents(phrase)

        def stem(text: str) -> str:
            # Короткому слову хватает трёх букв: у τρώω корень τρω, а не τρωω.
            clean = strip_accents(re.sub(f"[^{GREEK}]", "", text))
            return clean[:3] if len(clean) <= 5 else clean[:4]

        # Составные выражения ('Πού είναι', 'θα πάρω') разбираем по словам:
        # иначе склеенный корень никогда не найдётся во фразе с пробелом.
        candidates = []
        for source in [word] + (item.get("forms") or []):
            candidates += re.findall(f"[{GREEK}]+", source)
        if candidates and not any(stem(c) and stem(c) in flat for c in candidates):
            warns.append(f"{where}: фраза не содержит слово (корень '{stem(word)}'): '{phrase}'")

    if phrase and not item.get("phrase_ru"):
        errors.append(f"{where}: есть фраза, но нет её перевода 'phrase_ru'")

    # 4. Дубли между пакетами: одно слово - один id на весь курс.
    key = item.get("id") or item["gr"]
    if key in seen:
        errors.append(f"{where}: дубль '{key}', уже есть в {seen[key]}")
    else:
        seen[key] = where

    # То же слово под другим id - на пяти тысячах слов это неизбежно, если не ловить.
    # Ударение НЕ игнорируем: в греческом его место различает слова
    # (πότε «когда» и ποτέ «никогда» - разные слова, а не опечатка).
    word = (item.get("gr") or "").lower()
    if word:
        if word in seen_words:
            errors.append(f"{where}: слово '{item['gr']}' уже есть в {seen_words[word]} "
                          f"(под другим id)")
        else:
            seen_words[word] = where
        # Совпадение с точностью до ударения - повод посмотреть глазами.
        bare = strip_accents(word)
        twin = seen_bare.get(bare)
        if twin and twin != where:
            warns.append(f"{where}: отличается от {twin} только ударением - проверь, "
                         "что это правда разные слова")
        seen_bare.setdefault(bare, where)

    if item.get("forms") and not isinstance(item["forms"], list):
        errors.append(f"{where}: 'forms' должно быть списком")


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка контента курса греческого")
    ap.add_argument("packs", nargs="+")
    ap.add_argument("--strict", action="store_true", help="считать предупреждения ошибками")
    args = ap.parse_args()

    paths = []
    for pattern in args.packs:
        paths.extend(sorted(Path(p) for p in glob.glob(pattern)))
    if not paths:
        print("! файлы не найдены", file=sys.stderr)
        return 1

    errors, warns, seen, seen_words, seen_bare, total = [], [], {}, {}, {}, 0
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "packs" in data:          # это файл курса, не пакет
            continue
        pack_id = data.get("id") or path.stem
        items = (data.get("items") or []) + (data.get("review") or [])
        total += len(items)
        for idx, item in enumerate(items, 1):
            check_item(pack_id, idx, item, seen, seen_words, seen_bare, errors, warns)
        print(f"{path}: {len(items)} элементов")

    for w in warns:
        print(f"  warn  {w}")
    for e in errors:
        print(f"  ОШИБКА {e}")

    print(f"\nвсего элементов: {total} | уникальных слов: {len(seen)} | "
          f"ошибок: {len(errors)} | предупреждений: {len(warns)}")
    return 1 if errors or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
