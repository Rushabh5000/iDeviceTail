#!/usr/bin/env bash
# iDeviceTail desktop setup (macOS / Linux)
set -euo pipefail
cd "$(dirname "$0")/.."

command -v python3 >/dev/null || { echo "python3 (3.10+) required"; exit 1; }

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[device,dev]"
python -m idevicetail doctor

case "$(uname -s)" in
  Linux)
    echo
    echo "Linux: ensure usbmuxd + libimobiledevice are installed for Engine A:"
    echo "  sudo apt install -y usbmuxd libimobiledevice6 libimobiledevice-utils"
    ;;
esac

echo
echo "Then:  python -m idevicetail serve"
