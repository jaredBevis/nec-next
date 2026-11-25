#!/bin/sh
# Removes what install_linux.sh added outside this folder.
# Usage: ./uninstall_linux.sh            remove menu entry + settings, then ask about apt packages
#        ./uninstall_linux.sh --all      also remove the apt packages without asking
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

rm -f "$HOME/.local/share/applications/necnext.desktop" && echo "Removed menu entry"
rm -rf "$HOME/.config/NecNext" && echo "Removed settings (~/.config/NecNext)"

PKGS="nec2c python3-pyqt5 python3-pyqtgraph python3-opengl"
if [ "$1" = "--all" ]; then
  ans=y
else
  echo
  echo "install_linux.sh also installed these apt packages: $PKGS"
  echo "(python3-numpy is left alone; many other programs use it.)"
  printf "Remove them too? Other programs may use them. [y/N] "
  read ans
fi
case "$ans" in
  y|Y|yes) sudo apt remove -y $PKGS && sudo apt autoremove -y ;;
  *) echo "Kept apt packages." ;;
esac

echo
echo "Done. To finish, delete the app folder:"
echo "  rm -rf \"$DIR\""
