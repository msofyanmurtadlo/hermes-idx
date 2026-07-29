# hermes-idx

**Screener saham IDX berbasis CLI yang jalan di HP.** Screening ~900 emiten setelah market
close, keluarkan sinyal yang lengkap dengan stop loss, take profit, dan position size —
plus backtest out-of-sample yang bisa Anda audit sendiri.

> ⚠️ **STATUS: v0.1 — inti sudah jalan, belum siap dipakai untuk uang sungguhan.**
> Pipeline lengkap sudah berfungsi end-to-end: ambil data → indikator → 4 strategi →
> sinyal ber-SL/TP/sizing → backtest → portofolio → CLI → jembatan Hermes agent.
> Yang **belum** ada: daftar emiten IDX otomatis (masih perlu seed CSV), penyesuaian
> corporate action, modul screenshot, dan notifikasi. Aturan bursa historis (fraksi harga
> & batas auto rejection sebelum 2023) belum diverifikasi — lihat [issues](../../issues).

## Coba

```bash
git clone https://github.com/msofyanmurtadlo/hermes-idx.git && cd hermes-idx
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/hermes-idx init
.venv/bin/hermes-idx data update --tickers BBCA,BBRI,TLKM,ANTM,ADRO
.venv/bin/hermes-idx backtest --strategy breakout
.venv/bin/hermes-idx scan
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
- **Sumber nilai transaksi harian (Rp)** (#4). Yahoo hanya memberi volume lembar; sekarang
  `value` diestimasi `volume × close` dan ditandai ke user. Filter likuiditas seluruh sistem
  bertumpu pada angka estimasi ini.
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
