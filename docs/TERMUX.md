# Pemasangan di Termux + Migrasi dari Script v3

Panduan ini untuk pengguna yang sudah menjalankan `idx-bluechip-report.py` v3 lewat cron
Hermes dan ingin pindah ke `hermes-idx`.

---

## 1. Pasang

```bash
pkg update && pkg upgrade -y
pkg install -y git python python-numpy python-pandas

git clone https://github.com/msofyanmurtadlo/hermes-idx.git
cd hermes-idx && bash install.sh
```

`install.sh` mendeteksi Termux dan memakai `numpy`/`pandas` dari `pkg` — bukan
mengkompilasi lewat pip, yang di Android bisa berjam-jam atau gagal total.

## 2. Pindahkan data lama

```bash
hermes-idx port import-legacy
```

Membaca `~/.hermes/scripts/portfolio.json` dan `idx-signal-history.json`, lalu
memindahkannya ke SQLite. Posisi tanpa stop loss akan ditandai — isi dengan:

```bash
hermes-idx port plan BBCA --sl 8900 --tp1 9800
```

Akurasi versi lama **tidak dilebur** ke statistik baru. Itu diukur sebagai arah harga
esok hari, tanpa stop loss dan tanpa biaya, jadi tidak sebanding dengan expectancy.
Disimpan sebagai catatan saja.

## 3. Ambil data & buktikan dulu

```bash
termux-wake-lock                 # cegah Android mematikan proses
hermes-idx data update           # 10–30 menit pertama kali
hermes-idx compare               # WAJIB sebelum memakai sinyal
termux-wake-unlock
```

`compare` memberi tahu apakah ada strategi yang benar-benar punya edge **pada data
Anda**. Kalau tidak ada yang expectancy positif dengan p ≤ 0,05, jangan pakai sinyalnya
untuk uang sungguhan. Hasil uji kami ada di [`BUKTI-01.md`](BUKTI-01.md).

## 4. Sambungkan ke Hermes

```bash
hermes-idx agent install
hermes-idx agent verify          # menguji koneksi dengan benar-benar memanggil CLI
```

`verify` memeriksa empat hal: CLI ada di PATH, skill terpasang, `SKILL.md` terbaca, dan
kontrak JSON menghasilkan keluaran valid.

---

## 5. Ganti cron job v3

### Hentikan job lama

Dua job memanggil `idx-bluechip-report.py`:

| ID | Nama | Jadwal |
|---|---|---|
| `941408f618a8` | Laporan Harian Saham Bluechip IDX | `30 16 * * 1-5` |
| `a25caf6dd4e9` | Reminder Pagi Trading | `45 8 * * 1-5` |

```
cronjob action=update job_id=941408f618a8 enabled=false
cronjob action=update job_id=a25caf6dd4e9 enabled=false
```

Jangan dihapus dulu — kalau ada yang tidak beres, tinggal `enabled=true` lagi.

### Pasang job baru

**Laporan sore** — pipeline lengkap setelah market close:

```
cronjob action=create \
  name="Laporan IDX Sore" \
  schedule="30 16 * * 1-5" \
  script="hermes-idx-daily.sh" \
  provider=custom model=qwen3.8-max-preview
```

**Pengingat pagi** — porto & aksi saja, tanpa data update:

```
cronjob action=create \
  name="Reminder Pagi IDX" \
  schedule="45 8 * * 1-5" \
  script="hermes-idx-morning.sh" \
  provider=custom model=qwen3.8-max-preview
```

Isi `~/.hermes/scripts/hermes-idx-daily.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock 2>/dev/null || true
hermes-idx daily --update
termux-wake-unlock 2>/dev/null || true
```

Isi `~/.hermes/scripts/hermes-idx-morning.sh`:

```bash
#!/data/data/com.termux/files/usr/bin/bash
hermes-idx daily
```

Keduanya perlu `chmod +x`.

### Pinning model

Job v3 gagal dengan pesan ini:

```
Skipped to prevent unintended spend: global inference config drifted
  provider 'nous' -> 'custom'; model 'tencent/hy3:free' -> 'qwen3.8-max-preview'
  and this job is unpinned
```

Hermes menolak menjalankan job yang tidak di-pin ketika config global berubah — itu
perilaku yang benar, mencegah tagihan tak terduga. **Selalu sebutkan `provider` dan
`model` saat membuat job**, seperti contoh di atas.

Catatan: `tencent/hy3:free` mengembalikan HTTP 404 (`not found in our configuration or
OpenRouter catalog`). Terlepas dari apakah model itu seharusnya gratis, id tersebut
ditolak endpoint — jangan dipakai sebagai fallback sampai ada id yang terbukti valid.

---

## 6. Scheduling yang tidak jalan

Gejalanya: job interval 10 menit tapi eksekusi terakhir 50 menit lalu, dan heartbeat baru
bergerak saat HP disentuh.

**Penyebab: Android mematikan proses latar.** Tiga lapis, semuanya perlu diberesi:

1. **Doze whitelist** (bisa lewat adb dari komputer):
   ```bash
   adb shell dumpsys deviceidle whitelist +com.termux
   ```
   Verifikasi: `adb shell dumpsys deviceidle whitelist | grep termux`

2. **Battery optimization pabrikan** — ini yang paling sering terlewat, dan **tidak bisa
   lewat adb**. Di ColorOS (OPPO):
   `Settings → Battery → Termux → izinkan berjalan di latar belakang`
   Lalu kunci Termux di daftar recent apps (tarik ke bawah pada kartunya).

3. **Wakelock** saat operasi panjang — sudah ada di script di atas.

Tanpa langkah 2, langkah 1 saja tidak cukup di HP OPPO/Xiaomi/Vivo/Huawei.

---

## 7. Verifikasi

```bash
hermes-idx doctor
```

Memeriksa Python, dependensi, database, kesegaran data, universe, dan koneksi agent.
Semua harus hijau sebelum Anda mengandalkan laporan hariannya.

---

## Perbedaan perilaku dari v3

| Hal | v3 | hermes-idx |
|---|---|---|
| Sinyal beli tiap hari | hampir selalu ada | **sering nihil** — dan itu benar |
| Stop loss | tetap 2% (ATR-nya kode mati) | ATR × 3, minimum 2% |
| Biaya transaksi | tidak dihitung di P/L | fee beli + jual + PPh + slippage |
| Auto rejection | tidak dimodelkan | sinyal kena ARA dibuang dari hasil |
| Klaim akurasi | "akurasi kumulatif 70%" | expectancy + p-value, atau "belum ada edge" |
| Batas posisi | max 4 / max 2 per sektor | sama (diambil dari v3) |
| Filter IHSG | fail-safe tahan BELI | sama (diambil dari v3) |

**Yang paling terasa: laporan akan sering bilang "tidak ada sinyal hari ini".** Itu bukan
kerusakan. Di pasar datar, tidak bertransaksi adalah keputusan yang benar — backtest
menunjukkan strategi yang sering bertransaksi justru rugi karena biaya.
