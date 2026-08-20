#!/bin/bash
# Публикация site/ на GitHub Pages одной веткой без истории.
#
# Аудио тяжёлое: каждая пересборка - это новые 30-60 МБ, и обычная ветка
# копила бы их в истории навсегда (git хранит все версии). Поэтому site/
# уезжает в ОТДЕЛЬНУЮ ветку gh-pages, которая каждый раз перезаписывается
# одним коммитом. Репозиторий остаётся размером с текущее аудио, не больше.
set -e
cd "$(dirname "$0")/.."

[ -d site ] || { echo "нет site/ - сначала python3 tools/build_site.py"; exit 1; }
git remote get-url origin >/dev/null 2>&1 || { echo "нет remote origin - см. DEPLOY.md"; exit 1; }

SIZE=$(du -sh site | cut -f1)
echo "публикую site/ ($SIZE) в ветку gh-pages"

TMP=$(mktemp -d)
cp -R site/. "$TMP/"
git checkout --orphan gh-pages-tmp -q
git rm -rq --cached . 2>/dev/null || true
find . -maxdepth 1 ! -name . ! -name .git ! -name site -exec rm -rf {} + 2>/dev/null || true
cp -R "$TMP/." .
rm -rf "$TMP"
git add -A .
git -c user.name="${GIT_NAME:-Danil}" -c user.email="${GIT_EMAIL:-daniltarantov23@gmail.com}" \
    commit -qm "site $(date +%Y-%m-%d)"
git push -f origin gh-pages-tmp:gh-pages
git checkout -q main
git branch -D gh-pages-tmp -q
echo "готово. Settings -> Pages -> Source: gh-pages, папка /(root)"
