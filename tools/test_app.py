#!/usr/bin/env python3
"""
Проверка приложения: реально ли играет, синхронны ли такты, работают ли касания.

Запускает локальный сервер, гоняет страницу в Chromium и сверяет, что показано
на экране, с манифестом таймингов. Ошибка в синхронизации иначе видна только
на слух, а это долго.

    python3 tools/test_app.py
"""

import http.server
import io
import json
import re
import socketserver
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8913
ok, fail = [], []


def check(name: str, cond: bool, detail: str = ""):
    (ok if cond else fail).append(name)
    print(("  ok   " if cond else "  ПРОВАЛ ") + name + (f" - {detail}" if detail else ""))


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """
    SimpleHTTPRequestHandler не умеет Range-запросы, а без них Chromium не может
    перематывать аудио: seekable пустой, seek висит вечно. Для теста плеера это
    обязательно, и настоящий сервер тоже обязан отдавать 206.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, *a):
        pass

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()

        path = self.translate_path(self.path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None

        size = Path(path).stat().st_size
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        start = int(m.group(1)) if m and m.group(1) else 0
        end = int(m.group(2)) if m and m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416)
            f.close()
            return None

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        f.seek(start)
        return io.BytesIO(f.read(end - start + 1))


def serve():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), RangeHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    httpd = serve()
    manifests = sorted((ROOT / "build").glob("session-*.timings.json"))
    if not manifests:
        print("! нет собранных сессий - сначала python3 tools/build_session.py --minutes 10")
        return 1
    lesson = json.loads(manifests[0].read_text(encoding="utf-8"))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 480, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:{PORT}/app/index.html")
        page.wait_for_timeout(700)

        print("\nэкран 1: главный")
        check("данные загрузились", page.evaluate("window.LESSONS ? window.LESSONS.length : 0") >= 1)
        chips = page.locator("#tierRow .chip")
        n_lessons = page.evaluate("window.LESSONS.length")
        check("кнопка на каждую сессию", chips.count() == n_lessons,
              f"сессий {n_lessons}, кнопок {chips.count()}")
        label = chips.nth(0).inner_text()
        check("на кнопке длина сессии в минутах", "мин" in label and "0 мин" != label.strip(),
              label)
        check("выбрана первая сессия", "on" in (chips.nth(0).get_attribute("class") or ""))
        check("длина на главной совпадает с файлом",
              str(round(lesson["duration_ms"] / 60000)) in page.locator("#contLen").inner_text(),
              page.locator("#contLen").inner_text())

        print("\nэкран 2: плеер")
        page.locator("#continueCard").click()
        page.wait_for_timeout(400)
        check("плеер открыт", "on" in (page.locator("#s-player").get_attribute("class") or ""))
        check("аудио играет", page.evaluate("!document.getElementById('audio').paused"))

        # Проверяем синхронность: ставим плейхед в середину такта и сверяем показ.
        word = next(w for w in lesson["items"] if w["kind"] == "new" and w.get("phrase"))
        steps = [s for s in lesson["steps"] if s["item"] == word["index"]]

        # Перемотка асинхронная: без ожидания 'seeked' плейхед остаётся на интро,
        # и проверять будет нечего.
        SEEK = """async (sec) => {
            const a = document.getElementById('audio');
            a.pause();
            await new Promise(res => {
                if (Math.abs(a.currentTime - sec) < 0.02) return res();
                a.addEventListener('seeked', res, {once: true});
                a.currentTime = sec;
                setTimeout(res, 3000);
            });
            a.dispatchEvent(new Event('timeupdate'));
            return a.currentTime;
        }"""
        page.wait_for_function("document.getElementById('audio').readyState >= 1")

        def at(step, expect_text, expect_label, name):
            mid = (step["start_ms"] + step["end_ms"]) / 2 / 1000
            page.evaluate(SEEK, mid)
            page.wait_for_timeout(120)
            big = page.locator("#big").inner_text().strip()
            # inner_text отдаёт текст уже с CSS text-transform: uppercase,
            # поэтому подпись такта сверяем без учёта регистра.
            lab = page.locator("#blabel").inner_text().strip()
            check(name, big == expect_text and lab.casefold() == expect_label.casefold(),
                  f"показано '{big}' / '{lab}', ждали '{expect_text}' / '{expect_label}'")

        gr_steps = [s for s in steps if s["step"] == "gr"]
        pause_steps = [s for s in steps if s["step"] == "auto_pause"]
        at(gr_steps[0], word["gr"], "Греческий", "такт 1: греческое слово")
        at(pause_steps[0], word["gr"], "Вспомни", "такт 2: пауза показывает слово, не перевод")
        at([s for s in steps if s["step"] == "ru"][0], word["ru"], "Перевод", "такт 3: перевод")
        at([s for s in steps if s["step"] == "phrase"][0], word["phrase"], "В фразе", "такт 4: фраза")
        at(pause_steps[1], word["phrase"], "Вспомни", "такт 5: пауза перед переводом фразы")
        at([s for s in steps if s["step"] == "phrase_ru"][0], word["phrase_ru"],
           "Перевод фразы", "такт 6: перевод фразы")

        print("\nметр из пяти клеток")
        page.evaluate(SEEK, (gr_steps[0]["start_ms"] + 100) / 1000)
        page.wait_for_timeout(150)
        check("первая клетка активна",
              "on" in (page.locator("#meter .beat").nth(0).get_attribute("class") or ""))
        page.evaluate(SEEK, (gr_steps[1]["start_ms"] + 100) / 1000)
        page.wait_for_timeout(150)
        check("повтор слова светит третью клетку, а не первую",
              "on" in (page.locator("#meter .beat").nth(2).get_attribute("class") or ""))

        print("\nтранскрипция по слогам")
        page.evaluate(SEEK, (gr_steps[0]["start_ms"] + 100) / 1000)
        page.wait_for_timeout(200)
        syl = page.locator("#bsyl").inner_text().strip()
        check("транскрипция слова показана", syl == word["syl"],
              f"на экране '{syl}', в манифесте '{word['syl']}'")
        # У односложного слова ударение не размечается - проверяем на многосложных.
        multi = [w for w in lesson["items"]
                 if w["kind"] == "new" and w.get("syl") and "-" in w["syl"]]
        no_stress = [w["syl"] for w in multi if not any(c.isupper() for c in w["syl"])]
        check(f"ударный слог заглавными во всех {len(multi)} многосложных",
              not no_stress, f"без ударения: {no_stress[:4]}")
        phrase_step = [s for s in steps if s["step"] == "phrase"][0]
        page.evaluate(SEEK, (phrase_step["start_ms"] + phrase_step["end_ms"]) / 2000)
        page.wait_for_timeout(200)
        got = page.locator("#bsyl").inner_text().strip()
        check("на фразе - транскрипция фразы", got == word["syl_phrase"],
              f"на экране '{got}', в манифесте '{word['syl_phrase']}'")
        page.evaluate(SEEK, ([s for s in steps if s["step"] == "ru"][0]["start_ms"] + 100) / 1000)
        page.wait_for_timeout(200)
        check("на переводе транскрипции нет",
              page.locator("#bsyl").inner_text().strip() == "")

        print("\nнастройка жестов")
        page.locator('#nav button[data-go="s-me"]').click()
        page.wait_for_timeout(300)
        check("по умолчанию двойное = «не понял»",
              "on" in (page.locator('#setDouble .chip[data-act="not"]').get_attribute("class") or ""))
        page.locator('#setDouble .chip[data-act="fwd"]').click()
        page.wait_for_timeout(200)
        check("переназначение сохранилось",
              "fwd" in page.evaluate("localStorage.getItem('akou.taps')"))
        check("подпись на пульте обновилась",
              "+15" in page.locator("#remoteDouble").inner_text())
        page.locator('#nav button[data-go="s-player"]').click() if page.locator(
            '#nav button[data-go="s-player"]').count() else None
        page.evaluate(SEEK, (gr_steps[0]["start_ms"] + 100) / 1000)
        pos = page.evaluate("document.getElementById('audio').currentTime")
        page.keyboard.press("d")
        page.wait_for_timeout(400)
        moved = page.evaluate("document.getElementById('audio').currentTime") - pos
        check("двойное касание теперь мотает вперёд, а не отмечает", moved > 10,
              f"сдвиг {moved:.1f}с")
        page.locator('#nav button[data-go="s-me"]').click()
        page.wait_for_timeout(250)
        page.locator('#setDouble .chip[data-act="not"]').click()
        page.wait_for_timeout(200)

        print("\nкасания наушника")
        # Проверка промотки увела плейхед на другое слово - возвращаемся к эталонному,
        # иначе отмечено будет не то, что мы сверяем.
        page.locator('#nav button[data-go="s-home"]').click()
        page.locator("#continueCard").click()
        page.wait_for_timeout(300)
        page.evaluate(SEEK, (gr_steps[0]["start_ms"] + 100) / 1000)
        page.wait_for_timeout(200)
        page.keyboard.press("d")
        page.wait_for_timeout(200)
        check("двойное касание отметило слово",
              page.evaluate("document.getElementById('navCount').textContent").strip() == "· 1")
        check("кнопка подтвердила словом",
              word["gr"] in page.locator("#btnNot").inner_text())
        pos_before = page.evaluate("document.getElementById('audio').currentTime")
        page.wait_for_timeout(150)
        check("аудио не прыгнуло от «не понял»",
              abs(page.evaluate("document.getElementById('audio').currentTime") - pos_before) < 0.5)

        page.evaluate(SEEK, (gr_steps[1]["start_ms"] + 100) / 1000 + 1.5)
        page.wait_for_timeout(150)
        page.keyboard.press("r")
        page.wait_for_timeout(400)
        check("тройное касание вернуло к началу слова",
              abs(page.evaluate("document.getElementById('audio').currentTime")
                  - word["start_ms"] / 1000) < 0.2)

        print("\nэкран 5: повтор")
        page.locator('#nav button[data-go="s-review"]').click()
        page.wait_for_timeout(300)
        check("отмеченное слово в списке", page.locator(".marked").count() == 1)
        page.locator("#btnExport").click()
        page.wait_for_timeout(200)
        code = page.locator("#markedList .code").last.inner_text()
        check("выгрузка даёт id для генератора", word["id"] in code and "ids" in code)

        print("\nостальные экраны")
        page.locator('#nav button[data-go="s-topics"]').click()
        page.wait_for_timeout(200)
        check("темы открылись", page.locator(".topic").count() >= 5)
        page.locator('#nav button[data-go="s-me"]').click()
        page.wait_for_timeout(200)
        page.locator("#btnLock").click()
        page.wait_for_timeout(300)
        check("локскрин открыт", "on" in (page.locator("#s-lock").get_attribute("class") or ""))
        check("корпус потемнел", "dark" in (page.locator("#phone").get_attribute("class") or ""))
        check("на локскрине греческое слово",
              len(page.locator("#lockWord").inner_text().strip()) > 0)
        page.locator("#lockBack").click()
        page.wait_for_timeout(200)
        check("закрытие локскрина возвращает в плеер",
              "on" in (page.locator("#s-player").get_attribute("class") or ""))

        real = [e for e in errors if "favicon" not in e.lower()]
        check("нет ошибок в консоли", not real, "; ".join(real[:3]))

        page.screenshot(path=str(ROOT / "app" / "screenshot-player.png"))
        browser.close()

    httpd.shutdown()
    print(f"\nитого: {len(ok)} ок, {len(fail)} провалов")
    if fail:
        for f in fail:
            print(f"  ! {f}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
