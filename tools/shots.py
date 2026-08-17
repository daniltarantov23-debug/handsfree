#!/usr/bin/env python3
"""
Снимает экраны приложения в app/shots/ - чтобы показать, как оно выглядит,
не заставляя открывать файл.

    python3 tools/shots.py
"""

import json
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

from test_app import RangeHandler  # тот же сервер с Range: без него нет перемотки

import socketserver

ROOT = Path(__file__).resolve().parent.parent
PORT = 8916
OUT = ROOT / "app" / "shots"


def main() -> int:
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), RangeHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    lesson = json.loads((ROOT / "build" / "base-01-20.timings.json").read_text(encoding="utf-8"))
    word = next(w for w in lesson["items"] if w["kind"] == "new" and w.get("phrase"))
    steps = [s for s in lesson["steps"] if s["item"] == word["index"]]
    pause = [s for s in steps if s["step"] == "auto_pause"][0]
    phrase = [s for s in steps if s["step"] == "phrase"][0]

    SEEK = """async (sec) => {
        const a = document.getElementById('audio');
        a.pause();
        await new Promise(res => {
            a.addEventListener('seeked', res, {once: true});
            a.currentTime = sec; setTimeout(res, 3000);
        });
        a.dispatchEvent(new Event('timeupdate'));
    }"""

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 380, "height": 760},
                                device_scale_factor=2)
        page.goto(f"http://127.0.0.1:{PORT}/app/index.html")
        page.wait_for_timeout(900)
        # Снимаем только корпус телефона, без обвязки страницы.
        phone = page.locator("#phone")

        def shot(name: str):
            phone.screenshot(path=str(OUT / f"{name}.png"))
            print(f"  {name}.png")

        shot("1-home")

        page.locator("#continueCard").click()
        page.wait_for_timeout(500)
        page.wait_for_function("document.getElementById('audio').readyState >= 1")
        page.evaluate(SEEK, (pause["start_ms"] + pause["end_ms"]) / 2000)
        page.wait_for_timeout(250)
        shot("2-player-pause")          # главный такт метода: пауза на припоминание

        page.evaluate(SEEK, (phrase["start_ms"] + phrase["end_ms"]) / 2000)
        page.wait_for_timeout(250)
        shot("3-player-phrase")

        page.keyboard.press("d")        # «не понял»
        page.wait_for_timeout(300)
        shot("4-player-marked")

        page.locator('#nav button[data-go="s-review"]').click()
        page.wait_for_timeout(400)
        shot("5-review")

        page.locator('#nav button[data-go="s-topics"]').click()
        page.wait_for_timeout(300)
        shot("6-topics")

        page.locator('#nav button[data-go="s-me"]').click()
        page.wait_for_timeout(300)
        page.locator("#btnLock").click()
        page.wait_for_timeout(500)
        shot("7-lock")

        browser.close()
    httpd.shutdown()
    print(f"\nснимки в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
