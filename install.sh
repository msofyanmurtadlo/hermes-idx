#!/usr/bin/env bash
# Installer hermes-idx. Jalan di Termux (Android) dan Linux.
#
#   git clone https://github.com/msofyanmurtadlo/hermes-idx.git
#   cd hermes-idx && bash install.sh
#
# Aman dijalankan berulang.

set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '%s⚠%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '%s✗%s %s\n' "$RED" "$OFF" "$*" >&2; exit 1; }

IS_TERMUX=0
[ -n "${PREFIX:-}" ] && [ -d "${PREFIX}/bin" ] && case "$PREFIX" in *com.termux*) IS_TERMUX=1;; esac

say "${BOLD}hermes-idx — pemasangan${OFF}"
[ "$IS_TERMUX" = 1 ] && say "Platform: Termux (Android)" || say "Platform: Linux"

# --- 1. Dependensi sistem -----------------------------------------------------
if [ "$IS_TERMUX" = 1 ]; then
    # numpy & pandas WAJIB dari pkg. Build dari source via pip sangat lama / gagal.
    need=""
    for p in python python-numpy python-pandas; do
        pkg list-installed 2>/dev/null | grep -q "^$p/" || need="$need $p"
    done
    if [ -n "$need" ]; then
        say "Memasang paket Termux:$need"
        pkg install -y $need || die "gagal memasang paket. Coba 'pkg update' dulu."
    fi
    ok "paket Termux siap"
fi

command -v python3 >/dev/null || die "python3 tidak ditemukan."
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
    || die "butuh Python >= 3.11, terpasang $PYV"
ok "python $PYV"

python3 - <<'PY' || die "numpy/pandas tidak tersedia. Di Termux: pkg install python-numpy python-pandas"
import numpy, pandas
print(f"  numpy {numpy.__version__} · pandas {pandas.__version__}")
PY
ok "numpy & pandas"

# --- 2. Pasang paket ----------------------------------------------------------
# --no-build-isolation supaya pip TIDAK mengunduh & mengkompilasi ulang numpy/pandas
# di dalam venv sementara — di Android itu bisa berjam-jam atau gagal total.
PIP_ARGS="--no-build-isolation"
if [ "$IS_TERMUX" = 1 ]; then
    say "Memasang hermes-idx (memakai numpy/pandas sistem)…"
    pip install $PIP_ARGS -e . || die "pip install gagal"
else
    if [ ! -d .venv ]; then python3 -m venv .venv; fi
    say "Memasang hermes-idx ke .venv…"
    .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
    .venv/bin/pip install -q -e . || die "pip install gagal"
    warn "Aktifkan dengan: source .venv/bin/activate"
fi
ok "paket terpasang"

BIN=$(command -v hermes-idx 2>/dev/null || echo "./.venv/bin/hermes-idx")
[ -x "$BIN" ] || die "hermes-idx tidak ditemukan setelah instalasi"

# --- 3. Inisialisasi ----------------------------------------------------------
"$BIN" init >/dev/null && ok "config & database dibuat"
"$BIN" data seed-bluechip >/dev/null && ok "universe bluechip terisi"

# --- 4. Skill Hermes (opsional) ----------------------------------------------
if [ -d "${HOME}/.hermes" ]; then
    "$BIN" agent install --force >/dev/null 2>&1 \
        && ok "skill Hermes terpasang" \
        || warn "gagal memasang skill Hermes — jalankan manual: hermes-idx agent install"
else
    say "  (Hermes tidak terdeteksi — lewati pemasangan skill)"
fi

# --- 5. Termux: wakelock & baterai -------------------------------------------
if [ "$IS_TERMUX" = 1 ]; then
    command -v termux-wake-lock >/dev/null && ok "termux-wake-lock tersedia"
    warn "Android agresif mematikan proses latar. WAJIB dilakukan manual:"
    say  "   Settings → Battery → Termux → izinkan berjalan di latar"
    say  "   lalu kunci Termux di daftar recent apps."
fi

cat <<EOF

${BOLD}Selesai.${OFF} Langkah berikutnya:

  hermes-idx data update      # ambil OHLCV (10–30 menit pertama kali)
  hermes-idx compare          # adu strategi — LAKUKAN INI SEBELUM PAKAI SINYAL
  hermes-idx scan             # cari sinyal hari ini
  hermes-idx doctor           # diagnostik

${YELLOW}Baca dulu:${OFF} 'hermes-idx compare' memberi tahu apakah ada strategi yang
benar-benar punya edge pada data Anda. Pada data uji kami (bluechip IDX 2021–2026)
TIDAK ADA yang lolos — periode itu pasarnya datar. Jangan pakai sinyal untuk uang
sungguhan sebelum compare menunjukkan expectancy positif dan p <= 0.05.
EOF
