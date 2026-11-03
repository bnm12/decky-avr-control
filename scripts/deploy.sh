#!/usr/bin/env bash
# Build AVR Control locally and install it on a Steam Deck / Steam Machine over
# SSH, without making a GitHub release. Runs from Linux, macOS, WSL or Git Bash.
#
#   bash scripts/deploy.sh deck@steam-machine        # build, copy, restart Decky
#   bash scripts/deploy.sh deck@steam-machine --no-build
#   AVR_DECK_HOST=deck@steam-machine bash scripts/deploy.sh
#
# The device needs SSH enabled and a sudo password on the deck user (`passwd`).
# Settings in ~/homebrew/settings/avr-control/ are left alone. The installed
# version shows as <version>-dev.<commit> in Decky so you can tell builds apart.
set -euo pipefail

host="${AVR_DECK_HOST:-}"
build=1
for arg in "$@"; do
  case "$arg" in
    --no-build) build=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) host="$arg" ;;
  esac
done
if [ -z "$host" ]; then
  echo "usage: bash scripts/deploy.sh [user@]host [--no-build]  (or set AVR_DECK_HOST)" >&2
  exit 2
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
if [ "$build" = 1 ]; then
  if corepack pnpm --version >/dev/null 2>&1; then
    corepack pnpm install --frozen-lockfile
    corepack pnpm build
  elif command -v cmd.exe >/dev/null 2>&1; then
    # WSL without its own Node: build with Windows' Node in the same folder.
    cmd.exe /c "corepack pnpm install --frozen-lockfile && corepack pnpm build"
  else
    echo "Node/corepack not found: install Node, or build elsewhere and pass --no-build" >&2
    exit 1
  fi
fi
[ -f dist/index.js ] || { echo "dist/index.js missing: run without --no-build" >&2; exit 1; }

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir "$stage/avr-control"
# Same contents as the release zip (.github/workflows/build.yml, Package step).
cp -r dist main.py py_modules plugin.json package.json LICENSE README.md "$stage/avr-control/"
find "$stage/avr-control" -name __pycache__ -type d -prune -exec rm -rf {} +

commit="$(git rev-parse --short HEAD)"
git diff --quiet HEAD -- . || commit="$commit.dirty"
# python3 on Linux/macOS/WSL; Git Bash on Windows often only has `python`.
py="python3"
"$py" -c "" 2>/dev/null || py="python"
"$py" - "$stage/avr-control/package.json" "$commit" <<'EOF'
import json, sys
path, commit = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["version"] = f"{data['version'].split('-')[0]}-dev.{commit}"
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
print(f"Packaged {data['version']}")
EOF

tar -C "$stage" -czf "$stage/avr-control.tgz" avr-control
scp -q "$stage/avr-control.tgz" "$host:/tmp/avr-control.tgz"
# -t so sudo can ask for the password. $HOME expands on the device as the
# login user before sudo runs, so the plugin lands in deck's homebrew folder.
ssh -t "$host" '
  set -e
  plugins="$HOME/homebrew/plugins"
  sudo rm -rf "$plugins/avr-control"
  sudo tar -xzf /tmp/avr-control.tgz -C "$plugins"
  rm -f /tmp/avr-control.tgz
  sudo systemctl restart plugin_loader
  echo "Installed $(grep -o "\"version\": *\"[^\"]*\"" "$plugins/avr-control/package.json") and restarted Decky."
'
