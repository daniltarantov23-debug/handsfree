#!/usr/bin/env python3
"""
Греческое слово -> русская транскрипция по слогам с ударением: ψωμί -> псо-МИ.

Нужна на экран плеера: слышишь слово и сразу видишь, как оно распадается на слоги
и куда падает ударение. Ударный слог выделяется заглавными.

Правила приближённые - греческая орфография регулярна, но исключений хватает
(συγγνώμη, γεια). Для таких слов в контенте есть поле `syl`, оно перебивает расчёт.

    python3 tools/translit.py            # самопроверка на эталонном списке
    python3 tools/translit.py Ψωμί Νερό  # посмотреть конкретные слова
"""

import re
import sys
import unicodedata

TONOS = "άέήίόύώΐΰ"
PLAIN = {"ά": "α", "έ": "ε", "ή": "η", "ί": "ι", "ό": "ο", "ύ": "υ", "ώ": "ω",
         "ΐ": "ι", "ΰ": "υ", "ϊ": "ι", "ϋ": "υ"}
VOWELS = set("αεηιουω") | set(TONOS) | set("ϊϋ")

# Гласные диграфы: на слух это один звук, значит и один слог.
VOWEL_DIGRAPHS = {
    "ου": "у", "ού": "у", "οῦ": "у",
    "αι": "е", "αί": "е",
    "ει": "и", "εί": "и",
    "οι": "и", "οί": "и",
    "υι": "и", "υί": "и",
    "αυ": "ав", "αύ": "ав",
    "ευ": "эв", "εύ": "эв",
    "ηυ": "ив", "ηύ": "ив",
}
# αυ/ευ перед глухой согласной звучат как аф/эф.
VOICELESS = set("κπτφθχσξψ")

CONS_DIGRAPHS_INITIAL = {"μπ": "б", "ντ": "д", "γκ": "г"}
CONS_DIGRAPHS_MEDIAL = {"μπ": "мб", "ντ": "нд", "γκ": "нг", "γγ": "нг", "γχ": "нх"}
CONS_DIGRAPHS_ANY = {"τσ": "ц", "τζ": "дз"}

SINGLE = {
    "α": "а", "β": "в", "γ": "г", "δ": "д", "ε": "е", "ζ": "з", "η": "и", "θ": "т",
    "ι": "и", "κ": "к", "λ": "л", "μ": "м", "ν": "н", "ξ": "кс", "ο": "о", "π": "п",
    "ρ": "р", "σ": "с", "ς": "с", "τ": "т", "υ": "и", "φ": "ф", "χ": "х", "ψ": "пс",
    "ω": "о",
}

# Сочетания, которыми греческое слово может начинаться: если такая пара стоит
# между гласными, она целиком уходит в следующий слог (α-στέ-ρι, не ασ-τέ-ρι).
ONSETS = {
    "βλ", "βρ", "γλ", "γρ", "δρ", "θλ", "θρ", "κλ", "κρ", "κτ", "μν", "πλ", "πρ",
    "πτ", "σβ", "σγ", "σθ", "σκ", "σλ", "σμ", "σν", "σπ", "στ", "σφ", "σχ", "τμ",
    "τρ", "τσ", "τζ", "φθ", "φλ", "φρ", "φτ", "χλ", "χρ", "χτ", "μπ", "ντ", "γκ",
    "γγ", "βγ", "δγ", "γν", "γμ", "χν", "θν", "κν", "πν", "φκ", "σρ",
}


def strip_tonos(text: str) -> str:
    return "".join(PLAIN.get(c, c) for c in text)


DOUBLES = ("λλ", "μμ", "νν", "ππ", "ττ", "κκ", "σσ", "ρρ", "ββ", "δδ", "ζζ",
           "θθ", "φφ", "χχ")
# σ перед звонкой согласной звучит как з: κόσμος -> КО-змос.
VOICED_AFTER_S = set("βγδζμνλρ")


def collapse_doubles(word: str) -> str:
    low = word.lower().replace("γγν", "γν")
    for d in DOUBLES:
        low = low.replace(d, d[0])
    return low


def split_units(word: str) -> list:
    """Режет слово на единицы: гласный диграф или одна буква."""
    units, i = [], 0
    low = collapse_doubles(word)
    while i < len(low):
        pair = low[i:i + 2]
        if pair in VOWEL_DIGRAPHS:
            units.append(pair)
            i += 2
        else:
            units.append(low[i])
            i += 1
    return units


def is_vowel_unit(u: str) -> bool:
    return u in VOWEL_DIGRAPHS or u in VOWELS


def syllabify(word: str) -> list:
    """
    Делит на слоги по правилам новогреческого:
    одна согласная между гласными уходит вперёд; пара согласных - вперёд,
    если ею может начинаться слово, иначе разрывается.
    """
    units = split_units(word)
    if not units:
        return []

    syllables, cur = [], []
    i = 0
    while i < len(units):
        u = units[i]
        cur.append(u)
        # Синизис: безударное «и» перед гласной сливается с ней в один слог.
        if is_vowel_unit(u) and strip_tonos(u) in ("ι", "υ", "ει", "οι") \
           and not any(c in TONOS for c in u) \
           and i + 1 < len(units) and is_vowel_unit(units[i + 1]):
            i += 1
            cur.append(units[i])
        if is_vowel_unit(u):
            # Сколько согласных идёт до следующей гласной.
            j = i + 1
            cons = []
            while j < len(units) and not is_vowel_unit(units[j]):
                cons.append(units[j])
                j += 1
            if j >= len(units):            # согласные в конце слова - остаются здесь
                cur.extend(cons)
                i = j
                break
            if len(cons) == 0:
                pass
            elif len(cons) == 1:
                pass                        # одна согласная - к следующему слогу
            else:
                pair = strip_tonos(cons[0] + cons[1])
                # Сюда попадаем только из позиции после гласной, значит кластер
                # заведомо в середине слова - назал остаётся в этом слоге всегда.
                nasal_stop = pair in ("ντ", "μπ", "γκ", "γγ")
                if pair not in ONSETS or nasal_stop:
                    # Разрываем: первая согласная остаётся в этом слоге.
                    # Для назал+смычная это обязательно, иначе теряется «н»
                    # (εντάξει -> эн-ДА-кси, а не е-ДА-кси).
                    cur.append(cons[0])
                    cons = cons[1:]
                # иначе весь кластер уходит вперёд
                units[i + 1:j] = cons
            syllables.append(cur)
            cur = []
            i += 1
            continue
        i += 1
    if cur:
        if syllables and not any(is_vowel_unit(u) for u in cur):
            syllables[-1].extend(cur)       # хвост без гласной приклеиваем назад
        else:
            syllables.append(cur)
    return syllables


VOICING_AFTER_NASAL = {"τ": "д", "π": "б", "κ": "г"}


def transcribe_units(units: list, next_unit: str = "", prev_unit: str = "",
                     first_syllable: bool = False) -> str:
    """Переводит единицы одного слога в русские буквы."""
    out = []
    i = 0
    while i < len(units):
        u = units[i]
        nxt = units[i + 1] if i + 1 < len(units) else next_unit
        prv = units[i - 1] if i > 0 else prev_unit
        bare = strip_tonos(u)

        # Смычная после назала звучит звонко и через границу слога: εν|τά -> эн|ДА.
        if bare in VOICING_AFTER_NASAL and strip_tonos(prv) in ("ν", "μ", "γ") and i == 0:
            out.append(VOICING_AFTER_NASAL[bare])
            i += 1
            continue
        # σ перед звонкой согласной -> з.
        if bare in ("σ", "ς") and strip_tonos(nxt)[:1] in VOICED_AFTER_S:
            out.append("з")
            i += 1
            continue
        # Начальное ε читается как «э», внутри слова как «е».
        if bare == "ε" and first_syllable and i == 0 and u not in VOWEL_DIGRAPHS:
            out.append("э")
            i += 1
            continue

        if u in VOWEL_DIGRAPHS:
            value = VOWEL_DIGRAPHS[u]
            if value.endswith("в") and strip_tonos(nxt)[:1] in VOICELESS:
                value = value[:-1] + "ф"
            out.append(value)
            i += 1
            continue

        inside = i + 1 < len(units)          # вторая буква пары в этом же слоге
        pair = bare + strip_tonos(nxt) if (nxt and inside) else ""
        if pair in CONS_DIGRAPHS_ANY:
            out.append(CONS_DIGRAPHS_ANY[pair])
            i += 2
            continue
        if pair in CONS_DIGRAPHS_MEDIAL:
            table = CONS_DIGRAPHS_INITIAL if not out and i == 0 else CONS_DIGRAPHS_MEDIAL
            out.append(table.get(pair, CONS_DIGRAPHS_MEDIAL[pair]))
            i += 2
            continue

        # γ перед и/е звучит как й: γεια -> я, γυναίκα -> йи...
        if bare == "γ" and strip_tonos(nxt)[:1] in ("ε", "ι", "η", "υ") or \
           (bare == "γ" and nxt in ("ει", "εί", "αι", "αί")):
            out.append("й")
            i += 1
            continue

        out.append(SINGLE.get(bare, bare))
        i += 1
    return "".join(out)


# Слитные пары «й + гласная» - иначе выходит «йа» вместо «я».
SOFT = {"йа": "я", "йо": "ё", "йу": "ю", "йе": "е"}
CONSONANTS_RU = "бвгдзклмнпрстфхцчшщй"


def soften(text: str) -> str:
    for a, b in SOFT.items():
        text = text.replace(a, b)
    # Синизис после согласной: перед «а» и «е» пишем мягкую гласную (ду-ЛЯ),
    # перед «о» и «у» - мягкий знак (АВ-рьо). «рё» читается похоже, но выглядит
    # как другое слово, а «льа» не читается вовсе.
    text = re.sub(f"([{CONSONANTS_RU}])и([ае])",
                  lambda m: m.group(1) + {"а": "я", "е": "е"}[m.group(2)], text)
    text = re.sub(f"([{CONSONANTS_RU}])и([оу])",
                  lambda m: m.group(1) + "ь" + m.group(2), text)
    text = re.sub(r"^и([аоу])", lambda m: {"а": "я", "о": "ё", "у": "ю"}[m.group(1)], text)
    # «й» ведёт себя как согласная, но удвоения не терпит: γεια -> я, не йя.
    for a, b in (("йя", "я"), ("йё", "ё"), ("йю", "ю"), ("йе", "е"),
                 ("йьо", "ё"), ("йьу", "ю")):
        text = text.replace(a, b)
    return text


def syllabize_word(word: str) -> str:
    """Одно греческое слово -> слоги через дефис, ударный слог заглавными."""
    syls = syllabify(word)
    if not syls:
        return ""
    stressed = -1
    for n, s in enumerate(syls):
        if any(c in TONOS for u in s for c in u):
            stressed = n
    parts = []
    for n, s in enumerate(syls):
        nxt = syls[n + 1][0] if n + 1 < len(syls) else ""
        prv = syls[n - 1][-1] if n > 0 else ""
        text = soften(transcribe_units(s, nxt, prv, first_syllable=(n == 0)))
        parts.append(text.upper() if n == stressed else text)
    return "-".join(p for p in parts if p)


def syllabize(text: str) -> str:
    """Фраза или слово -> транскрипция. Слова разделяются точкой-разделителем."""
    cleaned = re.sub(r"[^\w\s\u0370-\u03FF\u1F00-\u1FFF]", " ", text)
    words = [w for w in cleaned.split() if w]
    return " · ".join(syllabize_word(w) for w in words if any(
        unicodedata.name(c, "").startswith("GREEK") for c in w))


# --------------------------------------------------------------------------
# самопроверка: список сверен вручную по звучанию
# --------------------------------------------------------------------------

CASES = [
    ("Ψωμί", "псо-МИ"),
    ("Νερό", "не-РО"),
    ("Καφές", "ка-ФЕС"),
    ("Καλημέρα", "ка-ли-МЕ-ра"),
    ("Καλησπέρα", "ка-ли-СПЕ-ра"),
    ("Ευχαριστώ", "эф-ха-ри-СТО"),
    ("Παρακαλώ", "па-ра-ка-ЛО"),
    ("Σπίτι", "СПИ-ти"),
    ("Θάλασσα", "ТА-ла-са"),
    ("Πόσο", "ПО-со"),
    ("Τραπέζι", "тра-ПЕ-зи"),
    ("Λογαριασμός", "ло-га-ря-ЗМОС"),
    ("Δέκα", "ДЕ-ка"),
    ("Μπορώ", "бо-РО"),
    ("Ντομάτα", "до-МА-та"),
    ("Αυτοκίνητο", "аф-то-КИ-ни-то"),
    ("Εντάξει", "эн-ДА-кси"),
    ("Τσάι", "ЦА-и"),
    ("Ώρα", "О-ра"),
    ("Κλειδί", "кли-ДИ"),
    ("Γεια", "я"),
    ("Συγγνώμη", "си-ГНО-ми"),
    ("Κόσμος", "КО-змос"),
    ("Δουλειά", "ду-ЛЯ"),
    ("Αύριο", "АВ-рьо"),
    ("Καλημέρα!", "ка-ли-МЕ-ра"),
    ("Πόσο κάνει;", "ПО-со · КА-ни"),
    ("Γεια σου", "я · су"),
    ("Γιατί", "я-ТИ"),
    ("Γυναίκα", "йи-НЕ-ка"),
    ("Καινούργιο", "ке-НУР-ё"),
]


def main() -> int:
    if len(sys.argv) > 1:
        for word in sys.argv[1:]:
            print(f"{word:<20} {syllabize(word)}")
        return 0

    bad = 0
    for word, expect in CASES:
        got = syllabize(word)
        mark = "ok  " if got == expect else "МИМО"
        if got != expect:
            bad += 1
        print(f"  {mark} {word:<14} {got:<20} ждали: {expect}")
    print(f"\n{len(CASES) - bad}/{len(CASES)} совпало")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
