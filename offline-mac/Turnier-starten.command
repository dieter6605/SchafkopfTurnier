#!/bin/zsh
set -eu
cd -P -- "${0:A:h}"
if [[ "$(/usr/bin/uname -sm)" != "Darwin arm64" ]]; then
    print -u2 'Dieses Paket benötigt einen Mac mit Apple-Chip und ein natives Terminal.'
    exit 1
fi

umask 077
pakete="$PWD/offline"
basis="$PWD/.turnier-python"
laufzeit="$basis/runtime"
export PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME
export DYLD_FALLBACK_LIBRARY_PATH="$laufzeit/lib"
export FONTCONFIG_FILE="$laufzeit/etc/fonts/fonts.conf"

if [[ ! -f "$laufzeit/.bereit" || "$(<"$laufzeit/.bereit")" != "$laufzeit" ]]; then
    zmodload zsh/system
    : >> "$PWD/.turnier-installation.lock"
    if ! zsystem flock -t 0 -f sperre "$PWD/.turnier-installation.lock"; then
        print -u2 'Die Einrichtung läuft bereits in einem anderen Fenster.'
        exit 1
    fi
    [[ -f "$pakete/laufzeit.sha256" ]] || { print -u2 'Bitte das vollständige Offline-ZIP entpacken.'; exit 1; }
    print 'Die lokale Turnierumgebung wird eingerichtet …'
    ( cd "$pakete" && /usr/bin/shasum -a 256 -c laufzeit.sha256 )
    if [[ -e "$basis" || -L "$basis" ]]; then
        if [[ -L "$basis" || ! -f "$basis/.schafkopf-laufzeit" ]]; then
            print -u2 "Der Ordner $basis ist bereits belegt. Bitte das ZIP in einen neuen Ordner entpacken."
            exit 1
        fi
        /bin/rm -rf -- "$laufzeit"
    fi
    /bin/mkdir -p "$laufzeit"
    : > "$basis/.schafkopf-laufzeit"
    /usr/bin/tar -xzf "$pakete/python-pango-macos-arm64.tar.gz" -C "$laufzeit"
    "$laufzeit/bin/python" -Es "$laufzeit/bin/conda-unpack"
    PIP_CONFIG_FILE=/dev/null "$laufzeit/bin/python" -I -m pip --isolated --disable-pip-version-check install \
        --no-index --no-cache-dir --only-binary=:all: --require-hashes \
        --find-links "$pakete/wheels" -r "$pakete/requirements-macos-arm64-py314.lock"
    "$laufzeit/bin/python" -I -m pip --disable-pip-version-check --no-cache-dir check
    "$laufzeit/bin/python" -I -c 'from weasyprint import HTML; assert HTML(string="<p>Druckprobe</p>").write_pdf().startswith(b"%PDF-")'
    print -r -- "$laufzeit" > "$laufzeit/.bereit"
    zsystem flock -u "$sperre"
fi

unset SKT_DB_PATH SKT_BACKUP_DIR
export HOST=127.0.0.1
export PORT="${PORT:-8051}"
print "Turnierverwaltung im Browser öffnen: http://$HOST:$PORT"
print 'Zum Beenden im Terminal Ctrl+C drücken.'
exec "$laufzeit/bin/python" run_waitress.py
