# hermes-idx

**Screener saham IDX berbasis CLI yang jalan di HP.** Screening ~900 emiten setelah market
close, keluarkan sinyal yang lengkap dengan stop loss, take profit, dan position size —
plus backtest walk-forward yang bisa Anda audit sendiri.

> ⚠️ **STATUS: SPESIFIKASI. Belum ada kode.**
> Repo ini saat ini berisi PRD dan review kritisnya. Dipublikasikan lebih awal supaya
> keputusan desainnya bisa dikoreksi orang lain **sebelum** ditulis jadi kode, bukan sesudah.

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

---

## Ikut bangun

Repo ini butuh orang yang paham **mikrostruktur pasar IDX**, bukan cuma Python.

Yang paling dibutuhkan sekarang — semua ada di [Issues](../../issues):

- **Auto Rejection (ARA/ARB)** belum dimodelkan sama sekali. Ini bias struktural terbesar di
  backtest IDX dan langsung memukul strategi breakout/momentum yang jadi andalan PRD.
- **Metodologi walk-forward** saat ini tidak melatih apa pun — perlu diputuskan apakah jadi
  rolling OOS biasa, atau ada yang benar-benar di-fit.
- **Sumber data nilai transaksi harian (Rp).** Yahoo Finance hanya memberi volume lembar,
  padahal filter likuiditas seluruh sistem bertumpu pada nilai rupiah.
- **Fraksi harga & aturan ARA/ARB per rezim tanggal** — dibutuhkan agar backtest historis
  tidak memakai aturan hari ini untuk periode lampau.

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

## Rencana stack

Python 3.11+ · pandas · numpy · `pandas-ta` (pure Python, bukan TA-Lib) · SQLite · `httpx` ·
`typer` + `rich` · backtest engine vectorized sendiri (backtrader/vectorbt terlalu berat
untuk Termux).

---

## Lisensi

[MIT](LICENSE).
