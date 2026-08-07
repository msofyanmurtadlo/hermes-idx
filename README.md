# hermes-idx

**Screener saham IDX berbasis CLI yang jalan di HP maupun VPS.** Screening emiten
bluechip setelah market close, keluarkan sinyal yang lengkap dengan stop loss, take
profit, dan position size — plus backtest out-of-sample yang bisa Anda audit sendiri.

> ⚠️ **STATUS: v0.5 — screening bluechip-only + MCP TradingView + sinyal realtime.**
>
> **Belum ada strategi yang terbukti positif** pada data terbaru (Juli 2026). Semua 6
> strategi masih expectancy negatif setelah biaya. Trio paling dekat: -0.001R, PF 1.00.
> Sinyal beli tetap ditahan (fail-closed) sampai ada yang lolos p < 0.05.
>
> Yang baru di v0.5:
> - **Screening bluechip-only (default)** — kandidat sinyal beli hanya dari emiten
>   kurasi bluechip (`universe.bluechip_only`); posisi porto non-bluechip tetap terpantau
> - **MCP TradingView sebagai sumber harga cadangan** (`src/hermes_idx/mcp.py`): bila
>   TradingView Scanner gagal, harga porto + IHSG diisi via server `tradingview-mcp`
>   (JSON-RPC langsung, tanpa dependensi mcp, ~2 detik untuk seluruh porto)
> - **Sinyal beli realtime** — watcher intraday tiap 10 menit jam bursa, langsung
>   push WA begitu ada sinyal (state file mencegah alert dobel)
> - **Cek MCP di doctor** — ketersediaan server MCP diverifikasi bila dikonfigurasi
>
> Dari v0.4 (tetap ada):
> - **Harga real-time** via TradingView Scanner API (bukan Yahoo yang stale)
> - **Backtest otomatis** tiap hari 17:00 WIB (data update → compare → simpan edge)
> - **Alert SL/TP** tiap 5 menit jam bursa — push notifikasi kalau kena/mendekati
> - **Saran reposisi SL/TP** termasuk trailing stop recommendation
> - **Rekomendasi tambah posisi** dengan position sizing dari saldo tersedia
> - **Section Backtest Edge** di laporan — transparansi expectancy per strategi
> - **P/L fee-inclusive** — konsisten dengan review() dan Stockbit
> - **Validasi data** — harga 0/negatif ditolak, live fetch gagal = warning eksplisit
>
> **Ini tetap bukan izin untuk mempertaruhkan uang.** Data historis tidak menjamin
> hasil di masa depan. Keputusan transaksi sepenuhnya tanggung jawab pengguna.

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
| 2 | `hermes-idx data seed-idx` | Mengisi **seluruh 843 emiten IDX** (atau `seed-bluechip` untuk 51 pilihan saja) | seketika |
| 3 | `hermes-idx data update` | Mengunduh harga harian 5 tahun ke belakang | 10–30 menit (843 emiten) |
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

### Laporan & monitoring otomatis

Di VPS (atau HP Termux) dengan Hermes Agent, semua jalan sendiri tanpa disentuh:

```
*/10   Sinyal Beli Realtime — 08-16 WIB Sen-Jum, scan intraday; push WA hanya
                                  kalau ada sinyal baru (state file anti-dobel)
*/5    Alert SL/TP          — 09-16 WIB Sen-Jum, push kalau kena/mendekati (1.5%)
17:00  Backtest Auto        — data update → compare → simpan edge ke DB
08:45  Laporan Pagi         — porto + rekomendasi + peringkat + reposisi (opsional)
16:30  Laporan Sore         — review porto saja, tanpa rekomendasi beli (opsional)
```

Laporan pagi/sore bisa dipause bila hanya mau monitoring realtime; sinyal beli dan
alert SL/TP tetap jalan. Watcher memakai harga intraday TradingView Scanner, dan bila
sumber itu gagal laporan harian otomatis jatuh ke **MCP TradingView** sebagai cadangan.

Backtest otomatis memastikan edge data selalu fresh: setiap sore setelah bursa tutup,
sistem update data Yahoo, jalankan compare semua strategi, dan simpan hasilnya ke DB.
Laporan besok pagi langsung pakai edge terbaru.

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
strategi         trade  win%   expect    PF    maxDD   p       vonis
breakout           350  39.4   +0.317  1.50   -25.7%  0.0022  ADA EDGE
trio               161  52.8   +0.076  1.23   -13.6%  0.157   TIDAK SIGNIFIKAN
momentum_rs        325  39.4   +0.100  1.22   -29.7%  0.091   TIDAK SIGNIFIKAN
v3score           1281  42.5   +0.024  1.06   -57.5%  0.257   TIDAK SIGNIFIKAN
mean_reversion     363  60.3   -0.060  0.79   -28.4%  1.0     EXPECTANCY NEGATIF
pullback           898  25.7   -0.265  0.60   -94.0%  1.0     EXPECTANCY NEGATIF
```

Universe seluruh IDX (843 emiten → 79 lolos likuiditas Rp20 M/hari), 2022–2026, horizon
swing 12 hari bursa, biaya 0,40% round-trip + slippage 1 tick.

Dua baris yang paling penting dibaca berpasangan:

**`mean_reversion`: win rate 60,3% — tertinggi — tapi tetap RUGI.** Ini bukan anomali,
ini justru pelajaran utamanya. Win rate tinggi mudah dibuat: lebarkan stop loss dan
dekatkan target. Setiap trade jadi lebih mungkin ditutup untung, dan setiap kekalahan
jadi jauh lebih mahal. Karena itu sistem ini memeringkat dengan **expectancy**, dan
menampilkan win rate hanya sebagai informasi.

**`breakout`: win rate 39,4% — hampir dua dari tiga trade rugi — tapi satu-satunya yang
lolos uji signifikansi.** Kemenangannya cukup besar untuk menutup semua kekalahan itu.

Kenapa dulu semuanya negatif? **IHSG hanya naik 1,4% dalam 5,1 tahun** pada periode uji,
dan biaya 0,40% round-trip memakan strategi long-only yang sering bertransaksi. Yang
berubah bukan pasarnya, tapi tiga cacat pengukuran yang diperbaiki: universe diperluas
dari 51 bluechip ke seluruh IDX, horizon dibatasi 12 hari (dulu posisi ditahan rata-rata
42 hari tanpa batas), dan backtest sekarang menyimulasikan rencana exit yang benar-benar
dipakai — dulu `tp1`, `tp1_size`, dan `breakeven_at_r` diabaikan mesin backtest.

Yang terakhir itu memberi hasil yang membantah dugaan umum: mengaktifkan partial TP +
breakeven **menaikkan win rate 34,5% → 46,5% tapi menjatuhkan expectancy jadi negatif**.
Karena itu keduanya dimatikan secara default.

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
| [`docs/PRD.md`](docs/PRD.md) | Spesifikasi lengkap: arsitektur, 6 strategi bawaan (S1–S4 + v3score + trio), model data, metodologi backtest, CLI, integrasi Termux |
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
