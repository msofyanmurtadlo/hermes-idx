# hermes-idx

**Screener saham IDX berbasis CLI yang jalan di HP.** Screening ~900 emiten setelah market
close, keluarkan sinyal yang lengkap dengan stop loss, take profit, dan position size —
plus backtest out-of-sample yang bisa Anda audit sendiri.

> ⚠️ **STATUS: v0.2 — jalan penuh, tapi belum ada strategi yang terbukti untung.**
> Pipeline lengkap berfungsi end-to-end. Yang belum terbukti adalah **edge**-nya: pada
> data uji (51 bluechip IDX, 2021–2026) **tidak ada satu pun strategi dengan expectancy
> positif setelah biaya**. Angka lengkapnya di [`docs/BUKTI-01.md`](docs/BUKTI-01.md).
> Repo ini tidak akan mengklaim akurat sebelum `compare` membuktikannya.

## Pasang

```bash
git clone https://github.com/msofyanmurtadlo/hermes-idx.git
cd hermes-idx && bash install.sh
```

Installer mendeteksi Termux vs Linux, memakai `python-numpy`/`python-pandas` dari `pkg`
di Android (bukan kompilasi pip), mengisi universe bluechip, dan memasang skill Hermes
kalau `~/.hermes` ada.

```bash
hermes-idx data update      # ambil OHLCV (10–30 menit pertama kali)
hermes-idx compare          # adu strategi — LAKUKAN INI DULU
hermes-idx scan             # sinyal hari ini
```

---

## Baru pertama kali? Mulai dari sini

Lima perintah, urut. Jangan dilompati — masing-masing menyiapkan yang berikutnya.

| # | Perintah | Yang terjadi | Lama |
|---|---|---|---|
| 1 | `hermes-idx init` | Membuat file konfigurasi & database di `~/.hermes-idx/` | seketika |
| 2 | `hermes-idx data seed-bluechip` | Mengisi daftar 51 emiten bluechip pilihan | seketika |
| 3 | `hermes-idx data update` | Mengunduh harga harian 5 tahun ke belakang | 10–30 menit |
| 4 | `hermes-idx compare` | Menguji semua strategi pada data itu, lalu memberi vonis | 5–15 menit |
| 5 | `hermes-idx daily --morning` | Laporan: porto, aksi, peringkat harian | ~1 menit |

Sebelum memakai uang sungguhan, atur modal Anda dulu:

```bash
hermes-idx config akun.modal 10000000          # modal Anda, dalam rupiah
hermes-idx config akun.risk_per_trade_pct 1    # rugi maksimum per transaksi (% modal)
hermes-idx config akun.max_open_positions 4    # berapa saham dipegang bersamaan
```

### Membaca laporannya

Laporan pagi punya dua bagian yang sering tertukar. Bedanya penting:

- **🔍 SINYAL BELI** — daftar yang *lolos semua ambang*. Sering kosong, dan itu normal:
  pada 120 hari bursa terakhir hanya 27% hari yang punya isi. Kosong berarti sistem
  menahan diri, bukan rusak.
- **📋 PERINGKAT HARI INI** — *selalu* berisi 5 teratas, diurutkan menurut kesiapan.
  Ini menjawab "kalau harus memilih, mana yang paling siap", bukan "mana yang boleh
  dibeli". Tiap baris diberi label:

  | Label | Arti |
  |---|---|
  | 🟢 **PELUANG** | Trigger nyala, rezim pasar mendukung, skor lolos, slot porto ada |
  | 🟡 **AMATI** | Setup terbentuk tapi ada yang mengganjal (alasannya ditulis) |
  | ⚪ **PANTAU** | Belum ada trigger; levelnya sekadar ancang-ancang |

### Istilah yang dipakai

| Istilah | Artinya dalam bahasa sehari-hari |
|---|---|
| **SL** (stop loss) | Harga jual paksa kalau rugi. Batas kerugian yang Anda terima di muka. |
| **TP** (take profit) | Harga jual saat untung. TP1 sebagian, TP2 sisanya. |
| **Lot** | Satuan beli di IDX. 1 lot = 100 lembar. |
| **R** | Satu satuan risiko = jarak entry ke SL. Untung "+2R" = dua kali risiko awal. |
| **Expectancy** | Rata-rata untung/rugi per transaksi, dalam R. **Negatif = strategi merugi.** |
| **Win rate** | Persentase transaksi yang untung. Bisa tinggi tapi tetap rugi — lihat bawah. |
| **Rezim pasar** | Bullish kalau IHSG di atas rata-rata 200 hari; bearish kalau di bawah. |
| **Slippage** | Selisih harga yang Anda niatkan dengan yang benar-benar terjadi. |

### Laporan otomatis tiap hari

Di Android, dua laporan bisa jalan sendiri tanpa Anda buka HP:

```cron
30 7  * * 1-5 ~/hermes-idx/scripts/hermes-idx-report.sh pagi   # sebelum bursa buka
30 16 * * 1-5 ~/hermes-idx/scripts/hermes-idx-report.sh sore   # setelah bursa tutup
```

Pagi memberi porto + peringkat + saran beli; sore hanya review posisi, tanpa rekomendasi
beli — supaya tidak memancing transaksi di jam yang salah. Hasilnya ditulis ke
`~/.hermes-idx/laporan/terbaru-pagi.txt` dan `terbaru-sore.txt`.

Langkah lengkap termasuk cara mengujinya ada di [`docs/TERMUX.md`](docs/TERMUX.md).

### Kalau ada yang tidak beres

```bash
hermes-idx doctor              # periksa semuanya: modul, data, database, koneksi agent
cat ~/.hermes-idx/laporan/cron.log   # kenapa laporan terjadwal gagal
```

Sambungkan ke Hermes agent:

```bash
hermes-idx agent install    # tautkan skill/ ke ~/.hermes/skills/idx-screener
hermes-idx agent verify     # uji koneksi: PATH, skill, dan kontrak JSON
hermes-idx doctor           # diagnostik lengkap
```

Agent memanggil CLI dengan `--json` dan membaca satu objek JSON per perintah. Kontrak dan
aturan perilakunya ada di [`skill/SKILL.md`](skill/SKILL.md); manifest kemampuan yang
dibaca agent: `hermes-idx agent info --json`.

---

## Disclaimer

Ini **alat bantu analisis teknikal, bukan penasihat investasi.** Seluruh output berupa
sinyal, level SL/TP, dan skor probabilistik berbasis data historis. Data historis tidak
menjamin hasil di masa depan. Keputusan transaksi sepenuhnya tanggung jawab pengguna.

Proyek ini tidak menjanjikan win rate tertentu, dan tidak akan pernah dipasarkan begitu.

---

## Masalah yang diselesaikan

| Masalah | Kenapa menyakitkan |
|---|---|
| Screening manual 900+ emiten tiap hari | Tidak realistis. Trader hanya memantau 10–20 saham dan melewatkan setup terbaik. |
| Rekomendasi grup/influencer tanpa level exit | Entry tahu, exit tidak tahu → floating loss jadi nyangkut. |
| Tidak ada evaluasi objektif atas strategi sendiri | Tidak tahu strategi mana yang sebenarnya profitable *pada dirinya*. |

---

## Jujur soal akurasi

Repo ini **tidak menjanjikan akurasi tertentu**, dan tidak akan pernah. Bukan karena
merendah — karena begitu sebuah alat mengklaim "akurat", ia berhenti mengukur dirinya.

Yang diberikan sebagai gantinya adalah `hermes-idx compare`: adu semua strategi pada
universe, periode, dan biaya yang sama, lalu vonis apa adanya.

```
strategi         trade  win%   expect    PF    maxDD  vonis
mean_reversion     282    58   -0.082  0.79   -22.5%  EXPECTANCY NEGATIF
v3score            755    39   -0.143  0.71   -72.6%  EXPECTANCY NEGATIF
breakout           176    22   -0.213  0.73   -38.7%  EXPECTANCY NEGATIF
momentum_rs        380    28   -0.311  0.46   -69.9%  EXPECTANCY NEGATIF
pullback           735    20   -0.377  0.47   -94.0%  EXPECTANCY NEGATIF

Tidak ada strategi yang lolos ambang minimum. Jangan pakai sinyal apa pun
dari sini untuk uang sungguhan sampai ada yang lolos.
```

Kenapa semua negatif? **IHSG hanya naik 1,4% dalam 5,1 tahun** pada periode uji. Pasar
datar + biaya transaksi 0,40% round-trip = strategi long-only yang sering bertransaksi
pasti kalah. Itu aritmetika, bukan kegagalan kode — beli-dan-tahan pun cuma median +1,8%.

Perhatikan `mean_reversion`: win rate 58%, tertinggi, tapi tetap rugi. Itu tepat kenapa
sistem ini memeringkat dengan expectancy, bukan win rate.

**Alat yang jujur bilang "belum ada edge" lebih berguna daripada alat yang mengklaim 90%
akurat.** Yang pertama menyelamatkan modal Anda; yang kedua menghabiskannya.

## Yang bikin beda

**Mengoptimasi expectancy, bukan win rate.** Win rate tinggi gampang dipalsukan: pasang TP
sangat dekat dan SL sangat lebar, dapat 85% win rate dengan expectancy negatif. Sistem ini
me-ranking strategi pakai expectancy + profit factor, dan menampilkan win rate hanya sebagai
informasi pendamping. Strategi win rate 80% dengan expectancy +0.05R akan diberi skor lebih
rendah daripada win rate 48% dengan expectancy +0.6R.

**Tidak ada sinyal tanpa exit.** Setiap rekomendasi beli wajib punya entry, SL, TP1, TP2,
position size, dan R:R. Kalau modal tidak cukup untuk risk management yang benar, sinyalnya
di-*skip*, bukan dipaksakan.

**Biaya transaksi dihitung, selalu.** Fee beli, fee jual + PPh final, dan slippage masuk ke
semua kalkulasi P/L dan backtest. Tanpa ini backtest overestimate secara sistematis.

**Data intraday tersedia, dengan batasnya diumumkan.** `hermes-idx data intraday
--interval 60m` mengambil ~3 tahun bar 1 jam (249.720 bar untuk 52 emiten). Interval
lebih kecil juga bisa (`5m`, `15m`, `30m`) tapi Yahoo hanya menyimpan **60 hari bursa**
untuk itu — satu rezim pasar saja, terlalu pendek untuk menyimpulkan edge. Perintahnya
mengatakan itu setiap kali dipakai, bukan menyembunyikannya di dokumentasi.

**Jalan di HP.** Target utamanya Termux di Android — tanpa root, tanpa build tool berat,
tanpa TA-Lib. Semua indikator harus bisa ditulis dengan numpy/pandas murni.

**Jurnal disiplin.** Melacak apakah Anda benar-benar mematuhi SL, apakah exit sesuai rencana
atau panic sell, dan membandingkan win rate entry-sistem vs entry-sendiri. Sering kali ini
lebih berdampak ke profitabilitas daripada perbaikan strategi apa pun.

---

## Dokumen

| Dokumen | Isi |
|---|---|
| [`docs/PRD.md`](docs/PRD.md) | Spesifikasi lengkap: arsitektur, 4 strategi bawaan, model data, metodologi backtest, CLI, integrasi Termux |
| [`docs/REVIEW-01.md`](docs/REVIEW-01.md) | Review kritis PRD — 5 blocker, 8 temuan serius, 12 menengah. **Baca ini kalau mau berkontribusi.** |
| [`docs/TERMUX.md`](docs/TERMUX.md) | Pasang di Android, migrasi dari script v3, dan cara membereskan scheduling yang mati |
| [`docs/BUKTI-01.md`](docs/BUKTI-01.md) | **Hasil adu strategi + bug yang ditemukan di script v3.** Angka mentah, bisa direproduksi |
| [`skill/SKILL.md`](skill/SKILL.md) | Kontrak & aturan perilaku untuk Hermes agent |

## Rencana stack → yang benar-benar dipakai

Python 3.11+ · pandas · numpy · SQLite · httpx · typer + rich · backtest engine sendiri.

Indikator **ditulis dengan pandas murni**, menyimpang dari PRD yang menyebut `pandas-ta`:
TA-Lib butuh kompilasi C yang rapuh di Termux, dan `pandas-ta` rusak pada numpy >= 2.0
(memakai `numpy.NaN` yang sudah dihapus). Semua indikator di sini one-liner sampai
belasan baris, jadi menulis sendiri lebih murah daripada menanggung dependensi rapuh di
platform target.

---

## Ikut bangun

Repo ini butuh orang yang paham **mikrostruktur pasar IDX**, bukan cuma Python.

Yang paling dibutuhkan sekarang — semua ada di [Issues](../../issues):

- **Aturan bursa historis** (#1, #6). Mekanismenya sudah ada — fraksi harga dan batas auto
  rejection disimpan ber-`effective_from`, dan backtest sudah membuang sinyal yang open
  T+1-nya kena ARA. Yang belum ada: **isi tabel rezim sebelum 2023**, termasuk periode ARB
  asimetris. Tanpa itu backtest periode lama memakai aturan hari ini. Ini riset peraturan,
  bukan coding — cocok untuk kontributor pertama.
- **Sumber nilai transaksi harian (Rp)** (#4). Yahoo hanya memberi volume lembar, jadi
  `value` diestimasi `volume × harga tipikal (H+L+C)/3`. Dua jalan buntu yang sudah
  dicoba, supaya tidak diulang: **API resmi IDX** diblokir Cloudflare (HTTP 403), dan
  kolom **`Value.Traded` TradingView ternyata bukan data independen** — pada BBCA nilainya
  persis `6.450 × 152.663.200`, sama sampai digit terakhir dengan estimasi close × volume.
  Peringatan estimasi sekarang hanya muncul untuk emiten yang duduk dalam ±25% dari ambang
  likuiditas, karena hanya di situ selisihnya bisa membalik keputusan.
- **Daftar emiten IDX otomatis.** Sekarang masih perlu `data seed` dari CSV manual.
- **Penyesuaian corporate action.** Split/dividen belum ditangani — gap harganya bisa
  memicu sinyal palsu.

Cara paling berguna untuk mulai:

1. Baca [`docs/REVIEW-01.md`](docs/REVIEW-01.md).
2. Buka issue kalau menemukan asumsi yang salah — terutama soal aturan bursa, perilaku
   broker, atau kualitas sumber data. **Koreksi asumsi lebih bernilai daripada kode**
   di tahap ini.
3. Kalau mau ambil satu item, komentar di issue-nya dulu supaya tidak dobel.

Diskusi dalam Bahasa Indonesia atau Inggris sama-sama diterima.

### Yang secara eksplisit di luar cakupan

- Bukan bot auto-trading. Tidak akan pernah mengeksekusi order.
- Tidak akan mereverse-engineer API internal Stockbit — melanggar ToS dan berisiko akun
  pengguna disuspend. Integrasi portofolio lewat screenshot, CSV, atau input manual.
- Tidak menjanjikan angka win rate kepada pengguna.

---

## Lisensi

[MIT](LICENSE).
