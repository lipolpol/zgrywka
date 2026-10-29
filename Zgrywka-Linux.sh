#!/usr/bin/env bash
# Zgrywka MXB - uruchamianie na Linuksie.
# Za pierwszym razem przygotowuje środowisko w folderze .venv (kilka minut, ok. 700 MB)
# i dodaje program do menu aplikacji.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
HERE="$(pwd)"
VENV="$HERE/.venv"

find_python() {
    local candidate
    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.14; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)' 2>/dev/null; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$VENV/bin/python" ]; then
    echo "Pierwsze uruchomienie – przygotowuję program (to potrwa kilka minut)…"
    if ! PY="$(find_python)"; then
        echo "Nie znaleziono Pythona 3.10–3.14. Zainstaluj go, np.: sudo apt install python3 python3-venv"
        exit 1
    fi
    if ! "$PY" -m venv "$VENV"; then
        rm -rf "$VENV"
        echo "Nie udało się utworzyć środowiska. Zainstaluj moduł venv, np.: sudo apt install python3-venv"
        exit 1
    fi
fi

# Biblioteki instalujemy ponownie tylko wtedy, gdy zmieniła się lista w requirements.txt.
if ! cmp -s "$HERE/requirements.txt" "$VENV/requirements.installed"; then
    echo "Instaluję biblioteki…"
    "$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$HERE/requirements.txt"
    cp "$HERE/requirements.txt" "$VENV/requirements.installed"
fi

# Skrót w menu aplikacji (tylko raz, albo po przeniesieniu folderu programu).
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/zgrywka-mxb.desktop"
if [ ! -f "$DESKTOP_FILE" ] || ! grep -qF "$HERE/Zgrywka-Linux.sh" "$DESKTOP_FILE"; then
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Zgrywka MXB
Comment=Porządkowanie nagrań z mikroportów
Exec="$HERE/Zgrywka-Linux.sh"
Icon=$HERE/zgrywka/ikona.png
Terminal=false
Categories=AudioVideo;Audio;
EOF
fi

# Qt na X11 potrzebuje biblioteki libxcb-cursor0 (na Waylandzie nie).
if [ "${XDG_SESSION_TYPE:-}" = "x11" ] && command -v ldconfig >/dev/null 2>&1 &&
    ! ldconfig -p | grep -q "libxcb-cursor.so.0"; then
    echo "Uwaga: jeśli okno się nie otworzy, zainstaluj: sudo apt install libxcb-cursor0"
fi

exec "$VENV/bin/python" -m zgrywka "$@"
