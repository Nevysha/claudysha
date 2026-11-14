#!/usr/bin/env bash
# Render the README diagrams (each *.html here) to light and dark PNGs with headless Chrome.
# Usage: ./render.sh            (all diagrams)
# Set CHROME to the browser binary if it isn't found. Under WSL, a Windows chrome.exe works too.
set -euo pipefail
cd "$(dirname "$0")"
CHROME=${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}
[ -n "$CHROME" ] || { echo "Chrome not found: set CHROME" >&2; exit 1; }
declare -A SIZE=([architecture]=760,720 [memory]=1100,300)
dir=$PWD; url="file://$PWD"
if [[ $CHROME == *.exe ]]; then dir=$(wslpath -w "$PWD"); url="file:///${dir//\\//}"; fi
sep=/; [[ $CHROME == *.exe ]] && sep='\'
for name in "${!SIZE[@]}"; do
  for theme in light dark; do
    "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
      --default-background-color=00000000 --virtual-time-budget=4000 --window-size="${SIZE[$name]}" \
      --screenshot="$dir$sep$name-$theme.png" "$url/$name.html#$theme" >/dev/null 2>&1
    echo "$name-$theme.png"
  done
done
