#!/bin/bash
# Zgrywka MXB - uruchamianie na macOS (dwuklik w Finderze).
# Za pierwszym razem przygotowuje środowisko w folderze .venv (kilka minut, ok. 700 MB).
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="$HERE/.venv"

find_python() {
    local candidate
    for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.1[0-4]/bin/python3 python3; do
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
        echo "Nie znaleziono Pythona 3.10–3.14."
        echo "Zainstaluj go ze strony https://www.python.org/downloads/macos/ i uruchom ten plik ponownie."
        open "https://www.python.org/downloads/macos/"
        read -r -p "Naciśnij Enter, aby zamknąć…"
        exit 1
    fi
    "$PY" -m venv "$VENV"
fi

if ! cmp -s "$HERE/requirements.txt" "$VENV/requirements.installed"; then
    echo "Instaluję biblioteki…"
    "$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$HERE/requirements.txt"
    cp "$HERE/requirements.txt" "$VENV/requirements.installed"
fi

exec "$VENV/bin/python" -m zgrywka "$@"
