#!/data/data/com.termux/files/usr/bin/bash
# Laporan sore: update data lalu laporan lengkap. Dipanggil cron Hermes 16:30 Sen-Jum.
# Wakelock dilepas lewat trap supaya tidak menggantung kalau script gagal di tengah.
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT
exec hermes-idx daily --update
