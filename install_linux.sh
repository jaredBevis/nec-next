#!/bin/sh
# Debian-family Linux (Raspberry Pi OS Bookworm or later, Debian 12+, Ubuntu 22.04+, Mint):
# everything comes from apt. For other distributions see README.md ("Other Linux / macOS").
set -e
if ! command -v apt >/dev/null 2>&1; then
  echo "This script needs apt (Raspberry Pi OS, Debian, Ubuntu, Mint)."
  echo "On other systems: install nec2c with your package manager, then"
  echo "  pip install -r requirements.txt && python3 -m necnext"
  exit 1
fi
sudo apt update
sudo apt install -y nec2c python3-numpy python3-pyqtgraph python3-pyqt5 python3-opengl
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/necnext.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=NEC Next
Comment=Antenna simulator (nec2c front end)
Exec=$DIR/run_necnext.sh %f
Terminal=false
Categories=HamRadio;Science;Engineering;
MimeType=text/plain;
EOD
echo "Installed. Start it from the menu (Other / Ham Radio) or run ./run_necnext.sh"
