#!/usr/bin/env bash
# Record all per-source demo GIFs via VHS.
# Each tape invokes live_demo_single.py <suite> against `uvx ph-civic-data-mcp`
# from PyPI and records the Rich-rendered session.

set -euo pipefail
cd "$(dirname "$0")/.."

write_tape() {
  local suite="$1"
  local seconds="$2"
  local out="docs/demo_${suite}.gif"
  local tape="/tmp/demo_${suite}.tape"

  cat > "$tape" <<EOF
Output ${out}
Require uv
Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 680
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Hide
Type "cd $(pwd) && clear"
Enter
Sleep 500ms
Show

Type "uv run python docs/live_demo_single.py ${suite}"
Sleep 400ms
Enter
Sleep ${seconds}s
EOF
  echo ">> recording $out (${seconds}s)"
  vhs "$tape"
  gifsicle -O3 --lossy=80 --colors 128 "$out" -o "$out.opt"
  mv "$out.opt" "$out"
  ls -lh "$out"
}

write_tape phivolcs 18
write_tape pagasa 16
write_tape philgeps 14
write_tape psa 16
write_tape combined 14
