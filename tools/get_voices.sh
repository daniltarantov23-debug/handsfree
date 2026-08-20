#!/bin/bash
# Качает модели голосов Piper в voices/. Каждая ~63 МБ.
set -e
cd "$(dirname "$0")/.."
mkdir -p voices
B="https://huggingface.co/rhasspy/piper-voices/resolve/main"
get() {  # язык/локаль/имя/качество -> voices/<файл>.onnx
  local path="$1" file="$2"
  [ -f "voices/$file.onnx" ] && { echo "  $file уже есть"; return; }
  echo "  качаю $file"
  curl -sSfL -o "voices/$file.onnx" "$B/$path/$file.onnx"
  curl -sSfL -o "voices/$file.onnx.json" "$B/$path/$file.onnx.json"
}
get "el/el_GR/rapunzelina/medium" "el_GR-rapunzelina-medium"
get "el/el_GR/joy/medium"         "el_GR-joy-medium"
get "ru/ru_RU/irina/medium"       "ru_RU-irina-medium"
echo "готово: $(ls voices/*.onnx | wc -l | tr -d ' ') моделей"
