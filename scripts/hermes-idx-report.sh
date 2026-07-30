#!/data/data/com.termux/files/usr/bin/bash
# Laporan terjadwal untuk Termux. Dipanggil cron:
#   hermes-idx-report.sh pagi   → sebelum bursa buka, porto + kandidat beli
#   hermes-idx-report.sh sore   → setelah bursa tutup, review porto saja
#
# cron di Termux mewarisi environment yang nyaris kosong: PATH, PREFIX, dan
# LD_LIBRARY_PATH harus diisi sendiri, kalau tidak `hermes-idx` tidak ketemu dan
# job-nya gagal diam-diam. Itu penyebab paling umum "cron saya tidak jalan".
#
# ANDROID_ROOT & ANDROID_DATA tampak sepele tapi WAJIB: cronie mengosongkan
# environment, dan tanpa keduanya bionic libc Android gagal membuka tzdata lalu
# python mati dengan Segmentation fault — bukan pesan error yang bisa ditebak.
# Diuji langsung dengan `env -i`, dan memang segfault sebelum baris ini ada.
set -uo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PREFIX
export ANDROID_ROOT="${ANDROID_ROOT:-/system}"
export ANDROID_DATA="${ANDROID_DATA:-/data}"
export HOME="${HOME:-/data/data/com.termux/files/home}"
export PATH="$PREFIX/bin:$PATH"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-$PREFIX/lib}"
export TZ="${TZ:-Asia/Jakarta}"

MODE="${1:-sore}"
LOGDIR="$HOME/.hermes-idx/laporan"
mkdir -p "$LOGDIR"
STAMP="$(date +%Y-%m-%d)"
OUT="$LOGDIR/$STAMP-$MODE.txt"
RUNLOG="$LOGDIR/cron.log"

# HP tidur di tengah fetch = laporan separuh jadi. Wakelock dilepas lewat trap
# supaya tidak menggantung walau script mati di tengah jalan.
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

case "$MODE" in
  pagi)
    # --update juga di pagi hari: kalau job sore gagal (HP mati/sinyal hilang),
    # tanpa ini laporan pagi memakai data dua hari lalu tanpa sadar.
    ARGS=(daily --update --morning) ;;
  sore)
    ARGS=(daily --update --afternoon) ;;
  *)
    echo "mode tidak dikenal: $MODE (pakai 'pagi' atau 'sore')" >&2; exit 2 ;;
esac

echo "=== $(date '+%F %T %Z') mulai $MODE ===" >> "$RUNLOG"
if hermes-idx "${ARGS[@]}" > "$OUT" 2>>"$RUNLOG"; then rc=0; else rc=$?; fi
if [ "$rc" -eq 0 ]; then
  cp "$OUT" "$LOGDIR/terbaru-$MODE.txt"
  echo "$(date '+%F %T') OK → $OUT" >> "$RUNLOG"
  # Notifikasi hanya kalau aplikasi Termux:API terpasang; tanpa itu perintahnya
  # menggantung, jadi dijaga timeout dan kegagalannya diabaikan.
  if command -v termux-notification >/dev/null 2>&1; then
    timeout 20 termux-notification --title "Laporan IDX $MODE" \
      --content "$(head -c 400 "$OUT")" --id "hermes-idx-$MODE" 2>/dev/null || true
  fi
else
  echo "$(date '+%F %T') GAGAL (exit $rc) — lihat pesan di atas" >> "$RUNLOG"
  exit "$rc"
fi
