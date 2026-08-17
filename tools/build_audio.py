#!/usr/bin/env python3
"""
Генератор аудио-уроков курса греческого.

Собирает из JSON готовый урок точной длины (10 / 20 / 40 минут) по методике из
METHOD.md: пауза стоит ПЕРЕД ответом (извлечение, а не предъявление).

Урок состоит только из УНИКАЛЬНЫХ слов - повторов внутри урока нет. Слово
возвращается лишь если отмечено двойным касанием наушника (--marked).

Структура урока:
    интро → разогрев отмеченных слов → новые слова полным циклом
          → финальный проход фраз на нормальной скорости → аутро

Кроме аудио пишет манифест таймингов: по нему приложение находит, какое слово
звучит сейчас, чтобы двойное касание наушника отметило именно его (TAPS.md).

Зависимости: macOS (`say`, `afconvert`) + стандартная библиотека.
MP3 - через первый найденный энкодер (ffmpeg / lame / lameenc), иначе .m4a.

Использование:
    python3 tools/build_audio.py --course content/course.json
    python3 tools/build_audio.py content/base-01.json --tier 10
    python3 tools/build_audio.py content/base-01.json --dry-run
"""

import argparse
import array
import glob
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from translit import syllabize  # noqa: E402

# Канонический формат промежуточных клипов: 22.05 kHz, моно, 16 бит.
SR = 22050
CHANNELS = 1
SAMPLE_WIDTH = 2

TRIM_THRESHOLD = 300     # амплитуда (из 32768), ниже которой считаем тишиной
TRIM_PADDING_MS = 30     # сколько тишины оставляем по краям клипа
DEFAULT_GAP = 0.35       # склейка между шагами по умолчанию, сек
AUTO_PAUSE_BASE = 0.45   # добавка к паузе на припоминание, сек
AUTO_PAUSE_MIN = 1.8     # пол паузы: на коротком слове тоже нужно успеть вспомнить

# Полный цикл первого знакомства со словом. Пауза - перед ответом.
FULL_CYCLE = [
    "gr", "pause:auto*1.6", "ru", "gap:0.5",
    "gr", "forms", "gap:0.5",
    "phrase", "pause:auto*1.4", "phrase_ru", "gap:0.9",
]
# Короткий цикл: узнавание без фразы. Для отмеченных касанием слов.
REVIEW_CYCLE = ["gr", "pause:auto*1.5", "ru", "gap:0.8"]
# Финальный проход: фразы на нормальной скорости, без дидактики.
FINAL_CYCLE = ["phrase_fast", "gap:0.7"]

# Три длины урока из прототипа. Длина не задаёт число слов - слова берутся из
# потока, пока влезают. Замер: слово стоит ~14.5с (цикл 12.2 + фраза в финале 2.3),
# то есть 10 мин ≈ 41 слово, 20 мин ≈ 82, 40 мин ≈ 165.
TIERS = {10: {}, 20: {}, 40: {}}

ROLE_VOICE = {
    "ru": "ru", "gr": "gr", "gr_slow": "gr_slow", "forms": "gr",
    "phrase": "gr", "phrase_fast": "gr_fast", "phrase_ru": "ru",
}
ROLE_FIELD = {
    "ru": "ru", "gr": "gr", "gr_slow": "gr", "forms": "forms",
    "phrase": "phrase", "phrase_fast": "phrase", "phrase_ru": "phrase_ru",
}
SPEECH_ROLES = tuple(ROLE_VOICE)

DEFAULT_VOICES = {
    "ru": {"name": "Milena", "rate": 190},
    "gr": {"name": "Melina", "rate": 170},
    "gr_slow": {"name": "Melina", "rate": 105},
    "gr_fast": {"name": "Melina", "rate": 205},
}

PACK_KEYS = {"id", "title", "level", "theme", "intro", "outro", "voices",
             "pattern", "review_pattern", "final_pattern", "items", "review"}
ITEM_KEYS = {"id", "gr", "ru", "tr", "syl", "phrase", "phrase_ru", "forms", "audio",
             "pattern", "gap_after", "known", "note", "tags"}
COURSE_KEYS = {"id", "title", "note", "packs"}

# Автоматических повторов нет: внутри урока каждое слово звучит один раз.
# Слово возвращается только если отмечено двойным касанием (TAPS.md).


# --------------------------------------------------------------------------
# звук (только stdlib)
# --------------------------------------------------------------------------

def read_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, CHANNELS, SAMPLE_WIDTH):
            raise RuntimeError(
                f"{path}: ожидался {SR} Hz / {CHANNELS} ch / {SAMPLE_WIDTH*8} bit, "
                f"получено {w.getframerate()} / {w.getnchannels()} / {w.getsampwidth()*8}")
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
        return samples


def write_wav(path: Path, samples: array.array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def silence(seconds: float) -> array.array:
    return array.array("h", [0]) * max(0, int(round(seconds * SR)))


def tone(freq: float = 660.0, seconds: float = 0.12, gain: float = 0.18) -> array.array:
    n = int(round(seconds * SR))
    fade = max(1, int(0.015 * SR))
    out = array.array("h", [0]) * n
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        out[i] = int(32767 * gain * env * math.sin(2 * math.pi * freq * i / SR))
    return out


def trim_silence(samples: array.array) -> array.array:
    n = len(samples)
    start, end = 0, n
    while start < n and abs(samples[start]) < TRIM_THRESHOLD:
        start += 1
    while end > start and abs(samples[end - 1]) < TRIM_THRESHOLD:
        end -= 1
    if start >= end:
        return array.array("h")
    pad = int(TRIM_PADDING_MS / 1000 * SR)
    return samples[max(0, start - pad):min(n, end + pad)]


def duration(samples) -> float:
    return len(samples) / SR


# --------------------------------------------------------------------------
# синтез
# --------------------------------------------------------------------------

def tts_clip(text: str, voice: str, rate: int, cache_dir: Path,
             fresh: bool = False) -> array.array:
    """
    Кэш привязан к имени голоса. Но если голос обновился под тем же именем
    (macOS так умеет: скачанный качественный вариант заменяет компактный),
    имя не меняется и кэш отдаст старые роботные клипы. Для такого случая - fresh.
    """
    key = hashlib.sha1(f"{voice}|{rate}|{SR}|{text}".encode("utf-8")).hexdigest()[:16]
    cached = cache_dir / f"{key}.wav"
    if fresh or not cached.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        aiff = cache_dir / f"{key}.aiff"
        subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), "--", text],
                       check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", str(CHANNELS),
                        str(aiff), str(cached)], check=True, capture_output=True)
        aiff.unlink(missing_ok=True)
    return trim_silence(read_wav(cached))


def load_recorded(path: Path, cache_dir: Path) -> array.array:
    """Готовая запись вместо синтеза - для кипрского, который TTS не умеет."""
    if not path.exists():
        raise SystemExit(f"файл записи не найден: {path}")
    key = hashlib.sha1(f"rec|{path}|{path.stat().st_mtime_ns}".encode()).hexdigest()[:16]
    cached = cache_dir / f"rec-{key}.wav"
    if not cached.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", str(CHANNELS),
                        str(path), str(cached)], check=True, capture_output=True)
    return trim_silence(read_wav(cached))


def installed_voices() -> set:
    """Локаль в выводе `say -v ?` бывает el_GR, en-scotland или просто en."""
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+[a-z]{2}([_-][A-Za-z]+)?\s+#", line)
        if m:
            names.add(m.group(1).strip())
    return names


# Качество голоса по убыванию. Компактный (без суффикса) - конкатенативный,
# звучит роботом; Premium и Enhanced - нейросетевые, качество несопоставимо.
VOICE_TIERS = (" (Premium)", " (Enhanced)", "")


def best_voice(name: str, installed: set) -> str:
    """
    Берёт лучший установленный вариант голоса. Имя с суффиксом в скобках
    считаем явным выбором и не трогаем.
    """
    if "(" in name:
        return name
    for suffix in VOICE_TIERS:
        if name + suffix in installed:
            return name + suffix
    return name


# --------------------------------------------------------------------------
# схема проговаривания
# --------------------------------------------------------------------------

def parse_step(token: str):
    """
    Токены:
      gr | ru | gr_slow | forms | phrase | phrase_ru | phrase_fast
      pause:2.5                    - фиксированная пауза, сек
      pause:auto | pause:auto*1.6  - пауза на припоминание: от длины последней
                                     прозвучавшей реплики, не короче AUTO_PAUSE_MIN
      gap | gap:0.5                - склейка
      tone                         - короткий сигнал
    """
    name, _, arg = token.partition(":")
    name, arg = name.strip(), arg.strip()
    if name in SPEECH_ROLES:
        return ("speak", name, None)
    if name == "tone":
        return ("tone", None, float(arg) if arg else 0.12)
    if name == "gap":
        return ("silence", None, float(arg) if arg else DEFAULT_GAP)
    if name == "pause":
        if arg.startswith("auto"):
            return ("auto_pause", None, float(arg.split("*", 1)[1]) if "*" in arg else 1.0)
        if not arg:
            raise ValueError("pause без длительности: укажи pause:2.0 или pause:auto")
        return ("silence", None, float(arg))
    raise ValueError(f"неизвестный токен схемы: '{token}'")


def item_text(item: dict, role: str):
    value = item.get(ROLE_FIELD[role])
    if role == "forms":
        if not value:
            return None
        return ", ".join(value) if isinstance(value, list) else str(value)
    return value


# --------------------------------------------------------------------------
# рендер одного цикла
# --------------------------------------------------------------------------

class Ctx:
    def __init__(self, voices: dict, cache_dir: Path, fresh: bool = False):
        self.voices = voices
        self.cache = cache_dir
        self.fresh = fresh

    def clip(self, item: dict, role: str) -> array.array:
        audio = item.get("audio")
        recorded = audio.get(role) if isinstance(audio, dict) else None
        if recorded:
            return load_recorded(Path(recorded), self.cache)
        cfg = self.voices[ROLE_VOICE[role]]
        return tts_clip(item_text(item, role), cfg["name"], cfg["rate"], self.cache,
                        self.fresh)

    def say_ru(self, text: str) -> array.array:
        cfg = self.voices["ru"]
        return tts_clip(text, cfg["name"], cfg["rate"], self.cache, self.fresh)


def render_cycle(item: dict, steps: list, ctx: Ctx):
    """Один цикл слова. Возвращает (сэмплы, список шагов с таймингами от нуля)."""
    out = array.array("h")
    marks = []
    last = None
    for kind, role, arg in steps:
        start = duration(out)
        if kind == "speak":
            piece = ctx.clip(item, role)
            out += piece
            last = duration(piece)
        elif kind == "silence":
            out += silence(arg)
        elif kind == "auto_pause":
            # Пауза считается от последней прозвучавшей реплики: вспоминать надо
            # то, что только что услышал. Поэтому перед переводом фразы она длиннее.
            base = last if last is not None else 0.6
            out += silence(max(AUTO_PAUSE_MIN, base * arg + AUTO_PAUSE_BASE))
        elif kind == "tone":
            out += tone(seconds=arg)
        marks.append({"step": role or kind,
                      "start_ms": int(start * 1000), "end_ms": int(duration(out) * 1000)})
    out += silence(item.get("gap_after", DEFAULT_GAP))
    return out, marks


def compile_pattern(pack_source: str, idx: int, item: dict, pattern: list) -> list:
    steps = []
    for token in pattern:
        try:
            step = parse_step(token)
        except ValueError as e:
            raise SystemExit(f"{pack_source}: элемент #{idx} ({item.get('gr', '?')}): {e}")
        # forms есть не у всех слов - шаг просто выпадает, это не ошибка.
        if step[0] == "speak" and step[1] == "forms" and not item.get("forms"):
            continue
        if step[0] == "speak" and item_text(item, step[1]) is None:
            raise SystemExit(f"{pack_source}: элемент #{idx} ({item.get('gr', '?')}): схема "
                             f"требует '{step[1]}', а поля '{ROLE_FIELD[step[1]]}' нет")
        steps.append(step)
    if not steps:
        raise SystemExit(f"{pack_source}: элемент #{idx} ({item.get('gr','?')}) - пустая схема")
    return steps


# --------------------------------------------------------------------------
# сборка урока точной длины
# --------------------------------------------------------------------------

def validate_pack(pack: dict, source: str) -> None:
    unknown = set(pack) - PACK_KEYS
    if unknown:
        raise SystemExit(f"{source}: неизвестные ключи пакета: {', '.join(sorted(unknown))}. "
                         f"Допустимые: {', '.join(sorted(PACK_KEYS))}")
    for idx, item in enumerate((pack.get("items") or []) + (pack.get("review") or []), 1):
        unknown = set(item) - ITEM_KEYS
        if unknown:
            raise SystemExit(f"{source}: элемент #{idx} ({item.get('gr','?')}): неизвестные "
                             f"ключи {', '.join(sorted(unknown))}")
        if not item.get("gr"):
            raise SystemExit(f"{source}: элемент #{idx} без поля 'gr'")


def build_tier(pack: dict, source: str, tier: int, stream: list, marked: list,
               out_dir: Path, cache_dir: Path, fmt: str, dry_run: bool,
               fresh: bool = False, target_s: float = 0, review_s: float = 0,
               label: str = "") -> dict:
    """
    Собирает урок ровно на tier минут из УНИКАЛЬНЫХ слов: сколько влезет, столько
    и берём из потока. Повторов внутри урока нет - они приходят только из слов,
    отмеченных двойным касанием (`marked`), и идут разогревом в начале.

    Порядок блоков: интро → разогрев отмеченных → новые слова
                    → финальный проход фраз на нормальной скорости → аутро.
    Остаток до точной длины распределяется по паузам между словами.
    """
    pack_id = label or f"{pack.get('id') or Path(source).stem}-{tier}"
    target = target_s or tier * 60.0

    voices = {k: dict(v) for k, v in DEFAULT_VOICES.items()}
    for role, override in (pack.get("voices") or {}).items():
        if role not in voices:
            raise SystemExit(f"{source}: неизвестная роль голоса '{role}'")
        voices[role].update(override)

    full = pack.get("pattern", FULL_CYCLE)
    short = pack.get("review_pattern", REVIEW_CYCLE)
    final = pack.get("final_pattern", FINAL_CYCLE)
    if not full or not short:
        raise SystemExit(f"{source}: пустая схема pattern или review_pattern")

    if not stream:
        raise SystemExit(f"{source}: нет слов для урока")

    if dry_run:
        mm, ss = divmod(int(target), 60)
        print(f"  {pack_id}: цель {mm}:{ss:02d}, в потоке доступно {len(stream)} слов"
              + (f", отмечено на повтор {len(marked)}" if marked else ""))
        return {"pack": pack_id, "items": 0, "dry_run": True}

    names = installed_voices()
    compact = []
    for role, v in voices.items():
        if v["name"] not in names and best_voice(v["name"], names) == v["name"]:
            raise SystemExit(
                f"Голос '{v['name']}' (роль {role}) не установлен. Доступные: "
                f"{', '.join(sorted(names))}\nГреческий (Melina): System Settings > "
                "Accessibility > Spoken Content > System Voice > Manage Voices.")
        v["name"] = best_voice(v["name"], names)
        if "(" not in v["name"]:
            compact.append(v["name"])
    if compact:
        print(f"  голоса компактные ({', '.join(sorted(set(compact)))}) - звучат роботом.\n"
              "    Естественный вариант ставится вручную: System Settings > Accessibility >\n"
              "    Spoken Content > System Voice > Manage Voices > Melina (Premium), 241 МБ.\n"
              "    После установки просто пересобери - генератор подхватит его сам.")

    ctx = Ctx(voices, cache_dir, fresh)
    head = []            # интро и разогрев отмеченных слов
    fixed = 0.0          # то, что занимает место независимо от набора новых слов

    if pack.get("intro"):
        head.append(("intro", None, ctx.say_ru(pack["intro"]) + silence(0.8), []))
    # Блок повтора: сколько влезет в отведённые ему минуты. Если бюджет не задан,
    # берём все отмеченные слова (старое поведение «не понял»).
    used_review = sum(duration(b[2]) for b in head)
    for i, item in enumerate(marked, 1):
        steps = compile_pattern(source, i, item, item.get("pattern", short))
        samples, marks = render_cycle(item, steps, ctx)
        if review_s and used_review + duration(samples) > review_s:
            break
        head.append(("marked", item, samples, marks))
        used_review += duration(samples)
    fixed += sum(duration(b[2]) for b in head)

    outro_block = None
    if pack.get("outro"):
        outro_block = ("outro", None, silence(0.6) + ctx.say_ru(pack["outro"]), [])
        fixed += duration(outro_block[2])

    # Набираем уникальные слова, пока влезают. Цена слова = его полный цикл
    # плюс его же фраза в финальном проходе, иначе урок вылезет за длину.
    final_steps = [parse_step(t) for t in final] if final else []
    new_blocks, final_blocks, used_time = [], [], 0.0
    for i, item in enumerate(stream, 1):
        steps = compile_pattern(source, i, item, item.get("pattern", full))
        samples, marks = render_cycle(item, steps, ctx)
        cost = duration(samples)
        fin = None
        if final_steps and item.get("phrase"):
            fin = render_cycle(item, final_steps, ctx)
            cost += duration(fin[0])
        if fixed + used_time + cost > target:
            break
        new_blocks.append(("new", item, samples, marks))
        if fin:
            final_blocks.append(("final", item, fin[0], fin[1]))
        used_time += cost

    if not new_blocks:
        raise SystemExit(f"{source}: в {tier} минут не влезает ни одно слово")

    short_by = len(stream) - len(new_blocks)
    ordered = head + new_blocks + final_blocks + ([outro_block] if outro_block else [])

    # Точная длина без повторов: остаток раскидываем по паузам между словами,
    # а не добиваем мёртвой тишиной в конце.
    # Но растягивать можно только естественный остаток - тот, что меньше одного
    # слова. Если поток кончился (short_by == 0), урок честно короче цели:
    # добивать его тишиной или повторами нельзя, нужен контент.
    slack = target - (fixed + used_time)
    if short_by > 0:
        per_item = min(0.8, slack / len(new_blocks))
        tail = slack - per_item * len(new_blocks)
    else:
        per_item, tail = 0.0, 0.0

    track = array.array("h")
    timings, manifest_items = [], []
    for kind, item, samples, marks in ordered:
        offset = duration(track)
        track += samples
        if kind == "new" and per_item > 0:
            track += silence(per_item)
        if item is None:
            continue
        idx = len(manifest_items) + 1
        for m in marks:
            timings.append({"item": idx, "step": m["step"],
                            "start_ms": int(offset * 1000) + m["start_ms"],
                            "end_ms": int(offset * 1000) + m["end_ms"]})
        manifest_items.append({
            "index": idx,
            # Стабильный id связывает слово между пакетами: без него «не понял»
            # на повторе не свяжется с тем же словом (TAPS.md).
            "id": item.get("id") or item["gr"],
            "kind": kind,
            "gr": item["gr"],
            "ru": item.get("ru"),
            "tr": item.get("tr"),
            # Транскрипция по слогам для экрана. Считается, но поле `syl`
            # в контенте перебивает расчёт - для слов-исключений.
            "syl": item.get("syl") or syllabize(item["gr"]),
            "syl_phrase": (syllabize(item["phrase"]) if item.get("phrase") else None),
            "phrase": item.get("phrase"),
            "phrase_ru": item.get("phrase_ru"),
            "start_ms": int(offset * 1000),
            "end_ms": int(duration(track) * 1000),
        })
    if tail > 0:
        track += silence(tail)

    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{pack_id}.wav"
    write_wav(wav_path, track)
    audio_path = wav_path if fmt == "wav" else encode(wav_path, out_dir / pack_id, fmt)
    if audio_path != wav_path:
        wav_path.unlink(missing_ok=True)

    counts = {k: sum(1 for b in ordered if b[0] == k)
              for k in ("marked", "new", "final")}
    manifest = {
        "pack": pack_id,
        "base": pack.get("id"),
        "tier": tier,
        "title": pack.get("title"),
        "theme": pack.get("theme"),
        "level": pack.get("level"),
        "audio": audio_path.name,
        "duration_ms": int(duration(track) * 1000),
        "pattern": full,
        "counts": counts,
        "items": manifest_items,
        "steps": timings,
    }
    (out_dir / f"{pack_id}.timings.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"{pack.get('title') or pack_id} - урок {tier} мин", ""]
    for it in manifest_items:
        if it["kind"] != "new":
            continue
        lines.append(f"{it['gr']}" + (f"  [{it['tr']}]" if it.get("tr") else ""))
        lines.append(f"   {it['ru']}")
        if it.get("phrase"):
            lines.append(f"   {it['phrase']}  -  {it.get('phrase_ru','')}")
    (out_dir / f"{pack_id}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    mins, secs = divmod(int(round(duration(track))), 60)
    print(f"  {audio_path.name}: {mins}:{secs:02d} | уникальных слов {counts['new']}"
          + (f", разогрев отмеченных {counts['marked']}" if counts["marked"] else "")
          + f", паузы растянуты на +{per_item:.2f}с")
    if short_by == 0 and slack > 5:
        print(f"    ! контента не хватило на {tier}:00 - в потоке всего {len(stream)} слов, "
              f"урок короче цели на {slack/60:.1f} мин. Нужны новые пакеты.")
    return {"pack": pack_id, "items": len(manifest_items), "audio": str(audio_path),
            "duration_ms": manifest["duration_ms"], "counts": counts,
            "slack": round(slack, 1)}


def build_pack(pack: dict, source: str, tiers: list, stream: list, marked: list,
               out_dir: Path, cache_dir: Path, fmt: str, dry_run: bool,
               fresh: bool = False) -> list:
    validate_pack(pack, source)
    return [build_tier(pack, source, t, stream, marked, out_dir, cache_dir, fmt, dry_run,
                       fresh) for t in tiers]


# --------------------------------------------------------------------------
# курс: единый поток уникальных слов
# --------------------------------------------------------------------------

def build_course(course_path: Path, tiers: list, out_dir: Path, cache_dir: Path,
                 fmt: str, dry_run: bool, only: str = "", marked: list = (),
                 fresh: bool = False, max_days: int = 99) -> list:
    course = json.loads(course_path.read_text(encoding="utf-8"))
    unknown = set(course) - COURSE_KEYS
    if unknown:
        raise SystemExit(f"{course_path}: неизвестные ключи курса: {', '.join(sorted(unknown))}")
    if not course.get("packs"):
        raise SystemExit(f"{course_path}: в курсе нет списка packs")

    base = course_path.parent
    packs = [json.loads((base / entry).read_text(encoding="utf-8"))
             for entry in course["packs"]]

    # Курс - это один поток уникальных слов. Урок любой длины берёт из него
    # столько, сколько влезает; следующий урок продолжает с того же места.
    stream, seen = [], set()
    for pack in packs:
        for item in pack.get("items") or []:
            key = item.get("id") or item["gr"]
            if key in seen:
                raise SystemExit(f"{course_path}: слово '{key}' встречается в курсе дважды - "
                                 "уроки должны быть из уникальных слов")
            seen.add(key)
            stream.append(item)

    # Дни нарезаются по длине урока, а НЕ по границам пакетов: иначе день 2
    # начинался бы с 51-го слова и перекрывал день 1. Каждый следующий день
    # продолжает поток с того места, где кончился предыдущий.
    meta = dict(packs[0])
    meta.pop("items", None)

    results = []
    for tier in tiers:
        offset, day = 0, 1
        while offset < len(stream) and day <= max_days:
            if only and only != f"day-{day:02d}":
                offset += WORDS_PER_TIER.get(tier, 1)
                day += 1
                continue
            chunk = stream[offset:]
            pack = dict(meta)
            pack["id"] = f"day-{day:02d}"
            pack["items"] = chunk
            # Тема дня - тема его первого слова: поток идёт по темам подряд.
            first_pack = next((p for p in packs
                               if any((i.get("id") or i["gr"]) == (chunk[0].get("id") or chunk[0]["gr"])
                                      for i in p.get("items") or [])), packs[0])
            pack["theme"] = first_pack.get("theme")
            pack["title"] = f"День {day}. {first_pack.get('theme', '')}".strip()

            print(f"\n=== день {day}, урок {tier} мин (поток с слова №{offset + 1}) ===")
            built = build_pack(pack, f"{course_path} день {day}", [tier], chunk, marked,
                               out_dir, cache_dir, fmt, dry_run, fresh)
            results += built
            used = built[0].get("counts", {}).get("new") if built and not dry_run else None
            if not used:
                used = WORDS_PER_TIER.get(tier, len(chunk))
            WORDS_PER_TIER[tier] = used
            offset += used
            day += 1
    return results


# Сколько слов влезает в урок каждой длины - уточняется по факту первой сборки,
# нужно чтобы в dry-run и при пропуске дней шаг был правильным.
WORDS_PER_TIER = {10: 41, 20: 82, 40: 163}


def load_marked(path: Path, stream: list) -> list:
    """
    Слова, отмеченные двойным касанием наушника в приложении (TAPS.md).
    Это единственный источник повторов: внутри урока слово звучит один раз,
    вернуться оно может только сюда, разогревом следующего урока.
    Формат: {"ids": ["psomi", "nero"]} или просто ["psomi", "nero"].
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = data.get("ids", []) if isinstance(data, dict) else data
    by_id = {(it.get("id") or it["gr"]): it for it in stream}
    marked, missing = [], []
    for key in ids:
        if key in by_id:
            item = by_id[key]
            marked.append({k: v for k, v in item.items()
                           if k in {"id", "gr", "ru", "tr", "audio"}})
        else:
            missing.append(key)
    if missing:
        print(f"  ! отмеченные слова не найдены в курсе: {', '.join(missing)}",
              file=sys.stderr)
    return marked


# --------------------------------------------------------------------------
# кодирование
# --------------------------------------------------------------------------

def with_ext(stem: Path, ext: str) -> Path:
    """Дописывает расширение, не срезая точки внутри id пакета."""
    return stem.parent / (stem.name + ext)


def encode(wav_path: Path, out_stem: Path, fmt: str) -> Path:
    if fmt == "mp3":
        mp3 = with_ext(out_stem, ".mp3")
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "libmp3lame",
                            "-b:a", "64k", str(mp3)], check=True, capture_output=True)
            return mp3
        if shutil.which("lame"):
            subprocess.run(["lame", "--quiet", "-b", "64", "-m", "m", str(wav_path), str(mp3)],
                           check=True, capture_output=True)
            return mp3
        if encode_lameenc(wav_path, mp3):
            return mp3
        print("  ! MP3-энкодера нет (ffmpeg / lame / lameenc) - собираю .m4a (AAC).\n"
              "    MP3 включится сам после:  python3 -m pip install --user lameenc",
              file=sys.stderr)
        fmt = "m4a"
    out = with_ext(out_stem, ".m4a")
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000",
                    str(wav_path), str(out)], check=True, capture_output=True)
    return out


def encode_lameenc(wav_path: Path, out_path: Path) -> bool:
    try:
        import lameenc  # type: ignore
    except ImportError:
        return False
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(SR)
    enc.set_channels(CHANNELS)
    enc.set_quality(2)
    with wave.open(str(wav_path), "rb") as w:
        pcm = w.readframes(w.getnframes())
    out_path.write_bytes(enc.encode(pcm) + enc.flush())
    return True


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка аудио-уроков курса греческого")
    ap.add_argument("packs", nargs="*", help="JSON-файлы пакетов (можно с маской)")
    ap.add_argument("--course", help="файл курса: уроки из общего потока уникальных слов")
    ap.add_argument("--only", default="", help="с --course: только этот день, напр. day-02")
    ap.add_argument("--days", type=int, default=99, help="сколько дней собрать")
    ap.add_argument("--marked", help="JSON со словами, отмеченными двойным касанием: "
                                    "они пойдут разогревом в начало урока")
    ap.add_argument("--tier", default="all",
                    help="длина урока: 10, 20, 40, all или через запятую")
    ap.add_argument("--out", default="build")
    ap.add_argument("--cache", default="build/.cache",
                    help="кэш клипов TTS; общий для всех --out")
    ap.add_argument("--format", choices=["mp3", "m4a", "wav"], default="mp3")
    ap.add_argument("--fresh", action="store_true",
                    help="пересинтезировать всё, игнорируя кэш: нужно после смены голоса, "
                         "если он установился под тем же именем")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.packs and not args.course:
        ap.error("укажи файлы пакетов или --course")

    if args.tier == "all":
        tiers = sorted(TIERS)
    else:
        tiers = []
        for part in args.tier.split(","):
            try:
                value = int(part)
            except ValueError:
                ap.error(f"--tier: '{part}' не число")
            if value not in TIERS:
                ap.error(f"--tier: {value} нет в наборе {sorted(TIERS)}")
            tiers.append(value)

    if not args.dry_run:
        for tool in ("say", "afconvert"):
            if not shutil.which(tool):
                print(f"Нужна утилита '{tool}' - скрипт рассчитан на macOS.", file=sys.stderr)
                return 1

    out_dir, cache_dir = Path(args.out), Path(args.cache)
    results = []

    paths = []
    for pattern in args.packs:
        matched = sorted(Path(p) for p in glob.glob(pattern))
        if not matched:
            print(f"! не найдено: {pattern}", file=sys.stderr)
        paths.extend(matched)

    if args.course:
        course_stream = []
        base = Path(args.course).parent
        for entry in json.loads(Path(args.course).read_text(encoding="utf-8"))["packs"]:
            course_stream += json.loads((base / entry).read_text(encoding="utf-8")).get("items") or []
        marked = load_marked(Path(args.marked), course_stream) if args.marked else []
        results += build_course(Path(args.course), tiers, out_dir, cache_dir,
                                args.format, args.dry_run, args.only, marked, args.fresh,
                                args.days)

    for path in paths:
        print(f"\n=== {path} ===")
        pack = json.loads(path.read_text(encoding="utf-8"))
        items = pack.get("items") or []
        marked = load_marked(Path(args.marked), items) if args.marked else []
        results += build_pack(pack, str(path), tiers, items, marked, out_dir, cache_dir,
                              args.format, args.dry_run, args.fresh)

    if not results:
        return 1
    if not args.dry_run:
        print(f"\nГотово: {len(results)} уроков в {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
