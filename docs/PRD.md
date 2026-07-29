# PRD — Hermes IDX Screener Agent

**Nama produk:** `hermes-idx` (Hermes Agent Skill)
**Versi dokumen:** 1.0
**Tanggal:** 29 Juli 2026
**Status:** Draft untuk review
**Platform target:** Termux (Android, arm64/aarch64) + Linux x86_64

---

## 0. Disclaimer

Produk ini adalah **alat bantu analisis teknikal**, bukan penasihat investasi. Seluruh output berupa sinyal, level SL/TP, dan skor probabilistik yang berbasis data historis. Data historis tidak menjamin hasil di masa depan. Keputusan transaksi sepenuhnya tanggung jawab pengguna. Dokumen ini tidak boleh dipasarkan sebagai "jaminan profit" atau "win rate X% dijamin".

---

## 1. Ringkasan Eksekutif

Hermes IDX Screener adalah agent CLI yang berjalan di Termux, melakukan screening seluruh saham IDX (~900+ emiten) setiap hari setelah market close, lalu menghasilkan:

- **Rekomendasi BELI** dengan entry price, stop loss, dan 2 level take profit
- **Rekomendasi JUAL** untuk posisi yang sudah dipegang (exit signal, trailing stop, cut loss)
- **Skor kualitas sinyal** berbasis backtest historis per-strategi, bukan klaim win rate arbitrer
- **Tracking portofolio** yang tersinkron dengan holding Stockbit (via import atau integrasi opsional)

Agent dijalankan melalui perintah natural language ke Hermes atau langsung via CLI.

---

## 2. Latar Belakang & Masalah

Trader ritel IDX menghadapi tiga masalah:

| Masalah | Dampak |
|---|---|
| Screening manual 900+ emiten tiap hari | Tidak realistis; trader hanya memantau 10–20 saham dan melewatkan setup terbaik |
| Rekomendasi grup/influencer tanpa level exit | Entry tahu, exit tidak tahu → floating loss jadi nyangkut |
| Tidak ada evaluasi objektif atas strategi sendiri | Tidak tahu strategi mana yang sebenarnya profitable pada dirinya |

Hermes IDX Screener menutup ketiganya dengan otomasi + backtest transparan + jurnal posisi.

---

## 3. Tujuan & Metrik Sukses

### 3.1 Tujuan Produk

| ID | Tujuan |
|---|---|
| G1 | Screening penuh IDX selesai < 5 menit di perangkat Android mid-range |
| G2 | Setiap sinyal BELI wajib punya entry, SL, TP1, TP2, position size, dan R:R |
| G3 | Setiap strategi punya laporan backtest 3 tahun yang bisa diaudit user |
| G4 | Portofolio user tercatat lengkap dengan realized/unrealized P/L dan evaluasi per-trade |
| G5 | Instalasi di Termux satu perintah, tanpa root, tanpa build tool berat |

### 3.2 Metrik Sukses

**Metrik produk:**
- Waktu screening penuh ≤ 300 detik pada Snapdragon 6-series
- Ukuran instalasi ≤ 200 MB termasuk cache data
- Crash rate < 1% dari total run

**Metrik kualitas sinyal (diukur via walk-forward, bukan in-sample):**

| Metrik | Target minimum | Catatan |
|---|---|---|
| **Expectancy per trade** | > +0.25R | Ini objective utama |
| **Profit factor** | > 1.5 | Gross profit / gross loss |
| Win rate | ≥ 45% | Metrik pendamping, bukan target optimasi |
| Max drawdown | < 20% | Pada equity curve simulasi |
| Sharpe ratio | > 1.0 | Annualized |
| Jumlah trade per tahun | > 60 | Sampel cukup untuk signifikansi |

> **Catatan penting soal "win rate tertinggi":**
> Win rate tinggi mudah dimanipulasi dengan TP sangat dekat dan SL sangat lebar — hasilnya win rate 85% tapi expectancy negatif. Karena itu sistem ini **mengoptimasi expectancy dan profit factor**, dan menampilkan win rate sebagai informasi pendamping. Strategi dengan win rate 80% tapi expectancy +0.05R akan diberi skor lebih rendah dari strategi win rate 48% dengan expectancy +0.6R.

### 3.3 Non-Goals (v1)

- Bukan bot auto-trading (tidak mengeksekusi order)
- Tidak menganalisis fundamental mendalam (hanya filter dasar: PER, PBV, ROE, DER)
- Tidak mendukung derivatif, waran terstruktur, atau obligasi
- Tidak real-time intraday tick (v1 = end-of-day; intraday di v2)
- Tidak menjanjikan angka win rate tertentu kepada pengguna

---

## 4. Persona Pengguna

**P1 — Swing Trader Ritel (primary)**
Modal Rp 10–200 juta, holding 3–20 hari, cek market malam hari via HP. Butuh shortlist 5–10 saham dengan level jelas. Sering nyangkut karena tidak disiplin cut loss.

**P2 — Trader Sistematis (secondary)**
Sudah punya strategi sendiri, ingin memvalidasi lewat backtest dan otomasi screening. Butuh konfigurasi strategi custom dan export data.

**P3 — Investor Part-time (tertiary)**
Hanya butuh monitoring portofolio dan alert kalau posisi mendekati SL/TP.

---

## 5. Arsitektur Sistem

```
┌─────────────────────────────────────────────────────────┐
│                    HERMES AGENT LAYER                    │
│   Natural language → intent → command dispatch           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                  hermes-idx CLI Core                     │
├──────────┬───────────┬───────────┬──────────┬───────────┤
│  Data    │ Indicator │ Strategy  │ Signal   │ Portfolio │
│  Layer   │  Engine   │  Engine   │ Builder  │  Tracker  │
├──────────┼───────────┼───────────┼──────────┼───────────┤
│ Backtest & Walk-Forward Engine                          │
├─────────────────────────────────────────────────────────┤
│ Storage: SQLite (OHLCV, signals, positions, backtests)  │
├─────────────────────────────────────────────────────────┤
│ Notifier: Termux-API / Telegram / stdout                │
└─────────────────────────────────────────────────────────┘
         │                          │
  ┌──────▼──────┐          ┌────────▼────────┐
  │ Data Sources│          │ Stockbit Bridge │
  │ Yahoo/IDX   │          │ CSV / manual    │
  └─────────────┘          └─────────────────┘
```

### 5.1 Stack Teknologi

| Komponen | Pilihan | Alasan |
|---|---|---|
| Bahasa | Python 3.11+ | Tersedia di Termux via pkg, ekosistem TA lengkap |
| Data | pandas, numpy | Standar |
| Indikator | `pandas-ta` (pure Python) | **Hindari TA-Lib** — butuh kompilasi C, sering gagal di Termux |
| Database | SQLite via `sqlite3` stdlib | Zero-config, file tunggal |
| HTTP | `httpx` | Async, HTTP/2 |
| Scheduler | `cron` (termux-services) atau `termux-job-scheduler` | Native Android |
| CLI | `typer` + `rich` | Output tabel rapi di terminal HP |
| Backtest | Custom vectorized engine | `backtrader`/`vectorbt` terlalu berat untuk Termux |

> **Constraint Termux kritikal:** paket yang butuh kompilasi C/Fortran (scipy penuh, TA-Lib, numba) sering gagal build di Android. Semua indikator harus implementable dengan numpy/pandas murni. `numpy` dan `pandas` sudah tersedia sebagai prebuilt wheel di repo Termux.

---

## 6. Modul & Requirement Detail

### 6.1 Data Layer (`hermes_idx.data`)

**Fungsi:** mengambil, membersihkan, dan menyimpan OHLCV seluruh emiten IDX.

**Requirement:**

- **DL-1** Mengambil daftar emiten aktif IDX (kode, nama, sektor, papan pencatatan, jumlah saham beredar)
- **DL-2** Mengambil OHLCV harian minimal 5 tahun ke belakang untuk seluruh emiten
- **DL-3** Incremental update — hanya fetch bar baru sejak `last_updated`, bukan full refresh
- **DL-4** Rate limiting adaptif (default 5 req/detik, exponential backoff pada HTTP 429)
- **DL-5** Penyesuaian corporate action: stock split, reverse split, dividen, right issue. Simpan dua kolom: `close` (raw) dan `adj_close` (adjusted)
- **DL-6** Deteksi & tandai data anomali: gap harga > 35% tanpa corporate action, volume 0 beruntun > 3 hari, harga = 0
- **DL-7** Resume-safe: jika proses terputus (HP sleep / koneksi putus), lanjut dari checkpoint terakhir
- **DL-8** Mode offline: jika tidak ada koneksi, screening tetap jalan pakai data cache dengan warning eksplisit umur data

**Sumber data (prioritas berjenjang):**

| Prioritas | Sumber | Format | Catatan |
|---|---|---|---|
| 1 | Yahoo Finance (`{KODE}.JK`) | JSON | Gratis, historis panjang, kadang delay/gap |
| 2 | IDX Official (idx.co.id) | JSON/XLSX | Otoritatif untuk daftar emiten & corporate action |
| 3 | Import CSV manual | CSV | Fallback penuh, format terdokumentasi |

> Sumber gratis kadang berubah struktur tanpa pemberitahuan. Data Layer wajib punya adapter pattern — satu interface, banyak implementasi — supaya pergantian sumber tidak menyentuh modul lain.

**Filter Universe (dijalankan sebelum screening):**

```yaml
universe_filter:
  min_avg_value_20d: 2_000_000_000   # Rp 2 M/hari, likuiditas minimum
  min_price: 50                       # hindari saham gocap
  max_price: 50_000                   # opsional
  min_listing_days: 250               # min 1 tahun trading
  exclude_boards: [Pemantauan Khusus, Papan Pemantauan]
  exclude_suspended: true
  exclude_full_call_auction: true
```

Filter likuiditas ini **non-negotiable** — sinyal cantik di saham dengan value harian Rp 200 juta tidak bisa dieksekusi tanpa slippage besar.

---

### 6.2 Indicator Engine (`hermes_idx.indicators`)

**Requirement IE-1:** Indikator wajib tersedia

| Kategori | Indikator |
|---|---|
| Trend | SMA/EMA (5,10,20,50,100,200), MACD (12,26,9), ADX(14), Supertrend(10, 3) |
| Momentum | RSI(14), Stochastic(14,3,3), ROC(20), Williams %R |
| Volatilitas | ATR(14), Bollinger Bands(20,2), Keltner Channel |
| Volume | OBV, Volume MA(20), MFI(14), VWAP, Accumulation/Distribution |
| Struktur | Swing High/Low (fractal), Support/Resistance pivot, Higher-High/Higher-Low counter |
| Relatif | Relative Strength vs IHSG, Relative Strength vs sektor |

**IE-2:** Semua perhitungan vectorized (tanpa loop per baris). Target: 900 emiten × 1250 bar × 25 indikator < 60 detik.

**IE-3:** Hasil indikator di-cache per tanggal. Recompute hanya untuk bar baru.

---

### 6.3 Strategy Engine (`hermes_idx.strategies`)

Setiap strategi adalah kelas yang mengimplementasikan interface:

```python
class Strategy(Protocol):
    name: str
    timeframe: str

    def entry_signal(self, df: pd.DataFrame) -> pd.Series: ...   # bool
    def exit_signal(self, df: pd.DataFrame) -> pd.Series: ...    # bool
    def stop_loss(self, df: pd.DataFrame, entry: float) -> float: ...
    def take_profit(self, df, entry, sl) -> tuple[float, float]: ...
```

#### Strategi bawaan v1

**S1 — Breakout Konsolidasi + Volume Konfirmasi**
- Harga menembus resistance 20-hari
- Volume hari breakout > 2× MA volume 20-hari
- ADX(14) > 20 (ada tren, bukan sideways)
- Harga > EMA50 dan EMA50 > EMA200
- SL: low swing terakhir atau `entry − 2×ATR(14)`, pilih yang lebih dekat
- TP1: `entry + 2R` (jual 50%), TP2: `entry + 4R` atau trailing Supertrend

**S2 — Pullback ke MA dalam Uptrend**
- EMA20 > EMA50 > EMA200 (tren naik terkonfirmasi)
- Harga koreksi menyentuh EMA20 lalu ditutup naik (bullish reversal candle)
- RSI(14) turun ke 40–55 lalu berbalik naik
- Volume koreksi menurun (koreksi sehat, bukan distribusi)
- SL: `low candle sinyal − 0.5×ATR`
- TP1: `entry + 1.5R`, TP2: swing high sebelumnya

**S3 — Momentum Relative Strength**
- Relative Strength vs IHSG di persentil 90 atas (3 bulan)
- Harga di atas EMA50
- MACD histogram positif dan meningkat
- Tidak overbought ekstrem (RSI < 78)
- SL: `entry − 2.5×ATR`
- TP: trailing stop Chandelier Exit

**S4 — Mean Reversion Oversold (kondisional)**
- Hanya aktif bila IHSG di atas MA200 (jangan catch falling knife di bear market)
- RSI(2) < 10 pada saham yang harga > MA200
- Harga menyentuh lower Bollinger Band
- SL: `entry − 2×ATR`
- TP: kembali ke MA20 (target dekat, holding period pendek)

**Requirement SE-1:** User dapat menambah strategi sendiri via file YAML deklaratif tanpa menulis Python:

```yaml
name: my_breakout
entry:
  all:
    - close > highest(high, 20).shift(1)
    - volume > sma(volume, 20) * 2
    - rsi(14) between [50, 75]
    - close > ema(200)
exit:
  any:
    - close < ema(20)
    - rsi(14) > 80
risk:
  stop_loss: {type: atr, multiplier: 2.0}
  take_profit: [{r_multiple: 2.0, size_pct: 50}, {r_multiple: 4.0, size_pct: 50}]
```

**SE-2:** Setiap strategi punya flag `market_regime_filter` — strategi long-only otomatis nonaktif bila IHSG di bawah MA200 selama > 10 hari (opsi bisa dimatikan user).

---

### 6.4 Signal Builder (`hermes_idx.signals`)

Modul ini mengubah sinyal mentah menjadi rekomendasi actionable.

#### 6.4.1 Rekomendasi BELI

**Requirement SB-1:** Setiap sinyal beli wajib berisi field lengkap:

| Field | Deskripsi |
|---|---|
| `ticker` | Kode emiten |
| `signal_date` | Tanggal sinyal terbentuk |
| `strategy` | Strategi yang men-trigger |
| `entry_type` | `market_open` \| `limit` \| `buy_stop` |
| `entry_price` | Harga entry, dibulatkan ke tick size IDX |
| `entry_zone` | Rentang harga entry yang masih valid |
| `stop_loss` | Level cut loss |
| `sl_pct` | Jarak SL dalam % |
| `tp1`, `tp2` | Dua level target |
| `tp1_size`, `tp2_size` | Porsi jual di tiap level (default 50/50) |
| `risk_reward` | R:R ke TP1 dan TP2 |
| `position_size` | Jumlah lot berdasarkan risk per trade |
| `max_risk_rp` | Nominal rupiah yang berisiko |
| `score` | 0–100, skor kualitas sinyal |
| `confidence` | Confidence interval dari backtest strategi ini |
| `valid_until` | Sinyal kadaluarsa (default 3 hari bursa) |
| `notes` | Alasan sinyal dalam bahasa manusia |

**SB-2 — Tick size IDX.** Semua harga dibulatkan sesuai fraksi harga BEI:

| Rentang harga | Fraksi |
|---|---|
| < Rp 200 | Rp 1 |
| Rp 200 – < Rp 500 | Rp 2 |
| Rp 500 – < Rp 2.000 | Rp 5 |
| Rp 2.000 – < Rp 5.000 | Rp 10 |
| ≥ Rp 5.000 | Rp 25 |

Pembulatan: entry & TP dibulatkan **ke bawah** (konservatif), SL dibulatkan **ke bawah** juga (memberi ruang sedikit lebih).

**SB-3 — Position sizing.** Rumus:

```
risk_per_trade   = modal × risk_pct          (default 1%)
risk_per_share   = entry_price − stop_loss
shares           = risk_per_trade / risk_per_share
lots             = floor(shares / 100)
```

Dengan guard tambahan:
- `lots` dibatasi maksimum 5% dari rata-rata volume harian 20-hari emiten
- Nilai posisi maksimum 20% dari modal (batas konsentrasi)
- Jika `lots < 1`, sinyal ditandai `SKIP: modal tidak cukup untuk risk management yang benar`

**SB-4 — Biaya transaksi.** Semua kalkulasi P/L dan backtest memasukkan:
- Fee beli: 0.15% (default, configurable)
- Fee jual: 0.25% (termasuk PPh final 0.1%)
- Slippage asumsi: 1 tick untuk saham likuid, 2 tick untuk kurang likuid

Tanpa ini, backtest akan overestimate performa secara sistematis — strategi scalping dengan target 1% akan tampak profitable padahal net negatif.

**SB-5 — Skor sinyal (0–100).** Bobot:

| Komponen | Bobot | Isi |
|---|---|---|
| Historical edge strategi | 35% | Expectancy & profit factor strategi ini dari walk-forward |
| Kekuatan setup | 25% | Konfluensi indikator, kualitas volume, kejelasan struktur |
| Likuiditas | 15% | Value harian, spread bid-ask, kedalaman |
| Market regime | 15% | Posisi IHSG vs MA200, breadth market, kondisi sektor |
| Risk/Reward | 10% | R:R ke TP1, jarak SL wajar (tidak terlalu lebar/sempit) |

Sinyal dengan skor < 55 tidak ditampilkan di rekomendasi utama (masuk watchlist saja).

#### 6.4.2 Rekomendasi JUAL

**SB-6** Sistem mengevaluasi setiap posisi terbuka di portofolio setiap hari dan mengeluarkan salah satu aksi:

| Aksi | Trigger |
|---|---|
| `HOLD` | Belum ada trigger apa pun, tren masih intact |
| `TAKE_PROFIT_1` | Harga menyentuh TP1 → jual sesuai porsi, geser SL ke breakeven |
| `TAKE_PROFIT_2` | Harga menyentuh TP2 → jual sisa |
| `TRAILING_STOP` | Harga sudah > 1.5R, aktifkan trailing (Chandelier / Supertrend) |
| `CUT_LOSS` | Harga close di bawah SL |
| `EXIT_SIGNAL` | Exit rule strategi terpenuhi (mis. close < EMA20, MACD cross down) |
| `TIME_STOP` | Posisi > N hari (default 20) tanpa mencapai 1R → modal tidak produktif |
| `DETERIORATION` | Struktur rusak: lower-low terbentuk, volume distribusi, RS jatuh |

**SB-7** Setiap rekomendasi jual menyertakan: alasan, harga limit yang disarankan, urgensi (`SEGERA` / `HARI INI` / `AMATI`), dan estimasi P/L bila dieksekusi.

---

### 6.5 Backtest & Walk-Forward Engine (`hermes_idx.backtest`)

Ini modul yang membuat klaim "win rate tinggi" bisa dipertanggungjawabkan.

**Requirement BT-1 — Metodologi wajib:**

- **Walk-forward analysis**, bukan sekadar in-sample backtest. Skema: train 2 tahun → test 6 bulan → geser maju, ulangi. Yang dilaporkan hanya hasil periode *test*.
- **Point-in-time universe** — hindari survivorship bias. Emiten yang delisting harus tetap ada dalam universe pada periode ia masih listing.
- **No look-ahead bias** — sinyal terbentuk pada close hari T hanya boleh dieksekusi pada open hari T+1.
- Eksekusi realistis: entry pada open T+1 + slippage; jika gap melewati SL, eksekusi di open (bukan di harga SL).
- Biaya transaksi penuh (SB-4).

**BT-2 — Output laporan backtest per strategi:**

```
STRATEGI: S1 Breakout Konsolidasi
Periode walk-forward: 2021-07 s/d 2026-06 (5 tahun, 10 fold)

  Total trade          : 412
  Win rate             : 47.3%    (195 W / 217 L)
  Avg win              : +8.4%    (+1.92R)
  Avg loss             : -4.1%    (-0.95R)
  Expectancy           : +0.42R per trade
  Profit factor        : 1.73
  Max drawdown         : -14.2%
  Sharpe (annualized)  : 1.31
  Avg holding period   : 11.3 hari
  Consecutive loss max : 7
  Return (compounded)  : +18.7%/tahun

  Konsistensi per fold : 8 dari 10 fold profitable
  Performa per regime  : Bull +26.1%/th | Sideways +4.2%/th | Bear -8.9%/th
```

**BT-3 — Statistical significance.** Tampilkan p-value dari uji apakah expectancy > 0 (bootstrap resampling, 10.000 iterasi). Strategi dengan p > 0.05 diberi label `TIDAK SIGNIFIKAN — jumlah sampel tidak cukup untuk menyimpulkan ada edge`.

**BT-4 — Peringatan overfitting.** Jika user menjalankan optimasi parameter, sistem wajib menampilkan:
- Jumlah kombinasi parameter yang diuji
- Degradasi performa in-sample vs out-of-sample
- Warning eksplisit bila degradasi > 40%

**BT-5 — Strategy ranking.** Ranking strategi menggunakan skor komposit, **bukan win rate**:

```
score = 0.40 × norm(expectancy)
      + 0.25 × norm(profit_factor)
      + 0.15 × norm(1 / max_drawdown)
      + 0.10 × norm(consistency_across_folds)
      + 0.10 × norm(sharpe)
```

---

### 6.6 Portfolio Tracker + Integrasi Stockbit (`hermes_idx.portfolio`)

#### 6.6.1 Jalur input: Screenshot (utama)

Stockbit **tidak menyediakan API publik resmi** untuk pihak ketiga. Reverse-engineering endpoint internal melanggar ToS dan berisiko akun disuspend. Karena itu jalur input utama adalah **screenshot layar portofolio** yang di-share user ke Hermes.

| Jalur | Cara | Risiko ToS | Status |
|---|---|---|---|
| **A. Screenshot** | User screenshot layar Portfolio → share ke Hermes → parsing otomatis | Tidak ada | **Jalur utama v1** |
| **B. Input manual** | `hermes-idx port add/sell` via CLI | Tidak ada | **Wajib ada** (koreksi & fallback) |
| **C. CSV** | Import file CSV berformat | Tidak ada | Fallback |
| **D. API internal** | Reverse-engineer endpoint | Melanggar ToS, akun bisa disuspend | **Tidak diimplementasikan** |

**Requirement PT-0:** Jalur D tidak diimplementasikan di versi mana pun. Jika user memintanya, agent menolak dan menjelaskan risikonya.

#### 6.6.2 Keterbatasan fundamental screenshot — dan cara mengatasinya

Ini masalah desain terpenting di modul ini, harus dipahami sebelum implementasi.

**Screenshot adalah snapshot state, bukan transaction log.** Dari layar portofolio, sistem hanya tahu: *saat ini* user pegang N lot di harga rata-rata X. Yang **tidak** tersedia:

| Data hilang | Fitur yang terdampak |
|---|---|
| Tanggal entry | Time stop (SB-6), holding period, umur posisi |
| Riwayat averaging | Analisis apakah user sering average down |
| Transaksi yang sudah tertutup | Realized P/L, win rate personal, jurnal disiplin (PT-7) |
| Harga eksekusi per transaksi | Evaluasi slippage, kepatuhan pada entry zone |

**Solusi: snapshot diffing.** Sistem menyimpan setiap hasil parsing sebagai snapshot bertanggal, lalu menyimpulkan transaksi dari selisih antar-snapshot berurutan:

```
Snapshot 25 Jul     Snapshot 29 Jul      Inferensi
─────────────────   ─────────────────    ──────────────────────────────
BBCA 15 @ 9.250     BBCA 15 @ 9.250      tidak ada perubahan
ADRO 20 @ 2.410     —                    JUAL 20 lot ADRO (harga?)
TLKM 30 @ 3.100     TLKM 45 @ 3.180      BELI 15 lot @ ~3.340 (dihitung
                                          dari perubahan avg price)
—                   MDKA 12 @ 2.850      BELI BARU 12 lot
```

**PT-DIFF-1** Harga beli tambahan dihitung dari perubahan weighted average:
```
harga_beli_baru = (lot_baru × avg_baru − lot_lama × avg_lama) / (lot_baru − lot_lama)
```
Hasilnya eksak untuk penambahan posisi. Untuk **penjualan**, harga jual tidak bisa dihitung (avg price tidak berubah saat jual) — sistem wajib **menanyakan harga jual ke user**, atau mengestimasi dari rentang harga hari itu dengan penanda `estimated`.

**PT-DIFF-2** Setiap inferensi transaksi ditandai `confidence`:
- `exact` — dihitung dari perubahan avg price
- `estimated` — diperkirakan dari OHLC hari transaksi
- `unknown` — butuh input user

**PT-DIFF-3** Semua transaksi hasil inferensi **wajib dikonfirmasi user** sebelum masuk database. Jangan pernah tulis diam-diam.

**PT-DIFF-4** Semakin sering user screenshot, semakin akurat inferensinya. Agent mengingatkan user untuk screenshot **setiap selesai transaksi**, bukan mingguan. Kalau ada 3 transaksi di antara dua snapshot, inferensinya jadi ambigu dan sistem menandainya `AMBIGUOUS — mohon input manual`.

**PT-DIFF-5** Idealnya user juga screenshot **tab Riwayat/Order** Stockbit, bukan hanya tab Portfolio. Tab riwayat berisi tanggal, harga, dan lot per transaksi — itu menutup seluruh gap di atas. Parser wajib mendukung kedua layout dan mendeteksi otomatis mana yang di-input.

#### 6.6.3 Pipeline parsing screenshot (`hermes_idx.vision`)

```
Screenshot(s) → Deteksi layout → Ekstraksi teks → Normalisasi angka
    → Validasi → Tabel konfirmasi ke user → Diff vs snapshot terakhir
    → Inferensi transaksi → Konfirmasi → Commit ke DB
```

**PT-V1 — Dua mode ekstraksi:**

| Mode | Cara | Kapan dipakai |
|---|---|---|
| **Vision model** (default) | Hermes membaca gambar langsung, mengembalikan JSON terstruktur | Online; akurasi jauh lebih tinggi pada layout kompleks |
| **OCR lokal** | Tesseract (`pkg install tesseract`) + parser regex | Offline, atau user tidak ingin gambar diproses di luar perangkat |

Mode OCR lokal wajib ada agar produk tetap berfungsi tanpa koneksi dan bagi user yang keberatan datanya keluar perangkat.

**PT-V2 — Layout yang wajib didukung:**
1. Tab **Portfolio** — daftar holding (ticker, lot, avg price, last price, unrealized P/L, market value)
2. Tab **Riwayat / Order History** — daftar transaksi (tanggal, ticker, BUY/SELL, lot, harga, status)
3. Ringkasan atas — total nilai portofolio, total P/L, cash balance

Parser mendeteksi layout dari kata kunci header, bukan dari posisi piksel — supaya tidak pecah saat Stockbit update UI.

**PT-V3 — Normalisasi angka (sumber bug terbanyak):**

| Kasus | Contoh input | Hasil |
|---|---|---|
| Separator ribuan Indonesia | `9.250` | `9250` |
| Desimal koma | `1.234,50` | `1234.50` |
| Singkatan | `1,2 jt` / `84 M` / `500 rb` | `1200000` / `84000000` / `500000` |
| Negatif | `-5,0%` / `(5,0%)` / merah | `-0.05` |
| Persen | `+5,7%` | `0.057` |
| Lot vs lembar | `15 lot` / `1.500` | simpan **lot**, konversi ×100 bila perlu |

Ambiguitas `9.250` — sembilan ribu dua ratus lima puluh, atau sembilan koma dua lima? Diselesaikan dengan **validasi silang ke data harga**: sistem cek apakah nilai masuk akal terhadap rentang harga historis ticker tersebut. Kalau `9.250` untuk BBCA, jelas Rp 9.250.

**PT-V4 — Validasi wajib sebelum commit:**
- Ticker ada di tabel `emiten` dan aktif → kalau tidak, kemungkinan salah baca (mis. `BBCA` terbaca `8BCA`), coba fuzzy match dan minta konfirmasi
- `avg_price` masuk rentang [low 3 tahun × 0.5, high 3 tahun × 1.5] ticker tersebut
- `lot` bilangan bulat positif
- `market_value ≈ lot × 100 × last_price` (toleransi 2%) — cek konsistensi internal
- Jumlah baris hasil parsing = jumlah baris yang terlihat di gambar (deteksi baris terpotong)

Baris yang gagal validasi **tidak dibuang diam-diam** — ditampilkan ke user dengan penanda dan alasannya.

**PT-V5 — Multi-screenshot & scrolling.** Portofolio panjang butuh beberapa screenshot. Sistem wajib:
- Menerima banyak gambar dalam satu perintah
- Deduplikasi baris yang muncul di dua gambar karena overlap scroll
- Mendeteksi baris yang terpotong di tepi atas/bawah gambar dan tidak memasukkannya
- Memperingatkan bila total nilai hasil penjumlahan ≠ total di ringkasan atas → indikasi ada baris terlewat

**PT-V6 — Konfirmasi wajib.** Hasil parsing selalu ditampilkan sebagai tabel untuk dikoreksi user sebelum disimpan:

```
HASIL BACA SCREENSHOT — 29 Jul 2026, 2 gambar

  #   TICKER  LOT   AVG      LAST    NILAI         STATUS
  1   BBCA     15   9.250    9.780   14.670.000    ✓
  2   ADRO     20   2.410    2.290    4.580.000    ✓
  3   TLKM     45   3.180    3.150   14.175.000    ✓
  4   MDKA     12   2.850    2.870    3.444.000    ✓
  5   ?RTO      8   1.120    1.095      876.000    ⚠ ticker tidak dikenal
                                                     maksud Anda BRTO?

  Total terbaca : Rp 37.745.000
  Total di layar: Rp 38.621.000        ⚠ selisih Rp 876.000
                                        → cocok dengan baris #5

  [K]onfirmasi  [E]dit baris  [U]lang  [B]atal
```

**PT-V7 — Privasi.** Screenshot portofolio adalah data finansial sensitif.
- Gambar mentah **tidak disimpan permanen** secara default; dihapus setelah parsing selesai
- Opsi `keep_screenshots: true` bila user ingin arsip, disimpan di direktori lokal terenkripsi
- Tidak ada telemetri, tidak ada upload ke server pihak ketiga selain vision model yang dipilih user
- Mode OCR lokal tersedia bagi user yang tidak ingin gambar diproses di luar perangkat sama sekali
- Agent tidak pernah menampilkan ulang isi portofolio di log atau riwayat chat yang persisten tanpa diminta

#### 6.6.4 Requirement Portfolio Tracker

- **PT-1** Pencatatan transaksi: ticker, tanggal, tipe (BUY/SELL), lot, harga, fee, catatan
- **PT-2** Perhitungan average cost menggunakan metode **weighted average** (konsisten dengan tampilan Stockbit) dan opsi FIFO untuk keperluan pajak
- **PT-3** Dashboard posisi terbuka: ticker, lot, avg price, harga terkini, unrealized P/L (Rp & %), % dari total modal, hari holding, jarak ke SL, jarak ke TP
- **PT-4** Realized P/L: per trade, per bulan, per strategi, per sektor
- **PT-5** Metrik portofolio: total return, win rate personal, expectancy personal, max drawdown, average holding period, exposure per sektor
- **PT-6** **Rekonsiliasi Stockbit** — bandingkan holding di Hermes vs snapshot screenshot terbaru, tampilkan selisih (lot berbeda, posisi hilang, posisi baru belum tercatat), tampilkan kemungkinan penyebabnya, dan tawarkan sinkronisasi. Stockbit selalu diperlakukan sebagai **sumber kebenaran** untuk posisi; database Hermes adalah sumber kebenaran untuk rencana (SL/TP/strategi) yang tidak ada di Stockbit.
- **PT-7** **Jurnal trading otomatis** — setiap posisi tertutup dicatat dengan: apakah entry sesuai sinyal sistem? apakah SL dipatuhi? apakah exit sesuai rencana atau panic sell? Ini menghasilkan laporan disiplin:

```
EVALUASI DISIPLIN — Juli 2026
  Trade sesuai rencana penuh   : 12 (63%)
  SL dilanggar (tidak cut)     : 4  (21%)  ← biaya: -Rp 3.2jt vs jika disiplin
  Exit terlalu cepat (< TP1)   : 3  (16%)  ← potensi hilang: Rp 1.8jt
  Entry di luar sinyal sistem  : 5
    → win rate entry sistem    : 52%
    → win rate entry sendiri   : 28%
```

Fitur PT-7 ini seringkali lebih berdampak ke profitabilitas user daripada peningkatan strategi apa pun.

- **PT-8** Format input yang didukung, berurutan prioritas: **screenshot** (PNG/JPG/WebP, satu atau banyak), CSV custom (schema terdokumentasi), input manual CLI, dan clipboard paste.

- **PT-10** **Pelengkapan data posisi.** Karena screenshot tidak memuat SL/TP/strategi, setiap posisi baru yang terdeteksi akan ditanyakan ke user: apakah berasal dari sinyal sistem (auto-link ke `signal_id`, SL/TP terisi otomatis) atau entry mandiri (user diminta menetapkan SL/TP, atau sistem menyarankan berbasis ATR). Posisi tanpa SL ditandai `⚠ TANPA STOP LOSS` di setiap tampilan portofolio sampai diisi.

- **PT-11** **Degradasi anggun.** Fitur yang bergantung pada riwayat (jurnal disiplin, realized P/L, win rate personal) menampilkan **cakupan data** secara eksplisit, misalnya `Berdasarkan 12 dari 19 trade — 7 trade tidak punya data entry lengkap`. Jangan menghitung statistik seolah datanya lengkap.
- **PT-9** Alert harian untuk posisi yang: mendekati SL (< 2%), menyentuh TP, atau melewati time stop.

---

### 6.7 Notifier (`hermes_idx.notify`)

- **NT-1** Output ke stdout dengan tabel `rich` (default)
- **NT-2** Notifikasi Android via `termux-notification` (butuh Termux:API)
- **NT-3** Telegram bot (opsional, config token sendiri)
- **NT-4** Export laporan harian ke Markdown / CSV / HTML
- **NT-5** Alert level: `CRITICAL` (SL tersentuh), `HIGH` (TP tersentuh, sinyal skor > 80), `NORMAL` (ringkasan harian)

---

## 7. Antarmuka CLI

```bash
# Setup
hermes-idx init                      # buat config & database
hermes-idx config set modal 50000000
hermes-idx config set risk_pct 1.0

# Data
hermes-idx data update               # incremental update OHLCV
hermes-idx data update --full        # full refresh
hermes-idx data status               # umur data, jumlah emiten, gap

# Screening
hermes-idx scan                                  # semua strategi aktif
hermes-idx scan --strategy breakout,pullback
hermes-idx scan --min-score 70 --top 10
hermes-idx scan --sector "Basic Materials"
hermes-idx scan --watchlist my_list

# Analisis single ticker
hermes-idx analyze BBCA
hermes-idx analyze BBCA --explain      # narasi lengkap alasan sinyal

# Backtest
hermes-idx backtest --strategy breakout --from 2021-01-01
hermes-idx backtest --all --walk-forward
hermes-idx backtest report --strategy breakout

# Portofolio
hermes-idx port show
hermes-idx port add BBCA --lot 10 --price 9250 --date 2026-07-20
hermes-idx port sell BBCA --lot 5 --price 9800
hermes-idx port review                 # rekomendasi jual untuk semua posisi
hermes-idx port snap ~/storage/pictures/Screenshot_1.png
hermes-idx port snap shot1.png shot2.png shot3.png    # portofolio panjang
hermes-idx port snap --ocr-local                      # tanpa vision model
hermes-idx port snap --dry-run                        # baca saja, jangan simpan
hermes-idx port snap --type history                   # paksa layout tab Riwayat
hermes-idx port snaps                                 # daftar snapshot tersimpan
hermes-idx port diff --from 2026-07-25 --to 2026-07-29
hermes-idx port import stockbit.csv
hermes-idx port reconcile              # bandingkan dengan snapshot terbaru
hermes-idx port journal --month 2026-07
hermes-idx port stats

# Otomasi
hermes-idx daily                       # pipeline lengkap: update → scan → review → notify
hermes-idx schedule enable --at 17:30  # pasang cron
```

### 7.1 Contoh Output

```
╭─ REKOMENDASI BELI — 29 Jul 2026 ─────────────────────────────────────╮

  ANTM  · Aneka Tambang · Basic Materials              SKOR 82/100
  Strategi: Breakout Konsolidasi + Volume

  Entry     Rp 1.750  (zona 1.735–1.775, limit)
  Stop Loss Rp 1.640  (-6.3%)
  TP 1      Rp 1.970  (+12.6%, R:R 2.0)  → jual 50%
  TP 2      Rp 2.190  (+25.1%, R:R 4.0)  → jual 50%

  Size      28 lot  ·  Rp 4.900.000  ·  risiko Rp 308.000 (0.6% modal)
  Valid s/d 1 Agu 2026

  Alasan  Tembus resistance 20-hari di 1.720 dengan volume 2.4× rata-rata.
          EMA50 > EMA200, ADX 27 (tren menguat). RS vs IHSG persentil 88.
          Value harian 20-hari Rp 84 M — likuiditas memadai.

  Edge    Strategi ini: expectancy +0.42R, PF 1.73, win rate 47%
          (412 trade, walk-forward 5 th, p=0.003)

╰──────────────────────────────────────────────────────────────────────╯

╭─ REVIEW POSISI ──────────────────────────────────────────────────────╮

  TICKER  LOT   AVG     LAST    P/L       AKSI            CATATAN
  BBCA     15   9.250   9.780   +5.7%     TAKE_PROFIT_1   TP1 tersentuh
  ADRO     20   2.410   2.290   -5.0%     ⚠ CUT_LOSS      Close < SL 2.320
  TLKM     30   3.100   3.150   +1.6%     HOLD            Struktur intact
  MDKA     12   2.850   2.870   +0.7%     TIME_STOP       22 hari, belum 1R

  Total unrealized: +Rp 1.284.000 (+2.1%)   Exposure: 61% modal

╰──────────────────────────────────────────────────────────────────────╯
```

---

## 8. Integrasi Hermes Agent

### 8.1 Struktur Skill

```
~/.hermes/skills/idx-screener/
├── SKILL.md                 # manifest & instruksi untuk agent
├── manifest.yaml            # metadata, versi, dependencies
├── install.sh               # installer Termux
├── commands/                # mapping intent → CLI command
│   ├── scan.yaml
│   ├── portfolio.yaml
│   └── backtest.yaml
├── src/hermes_idx/          # package Python
└── config/
    ├── default.yaml
    └── strategies/*.yaml
```

### 8.2 Intent Mapping

| Ucapan user | Command |
|---|---|
| "saham apa yang bagus dibeli hari ini" | `scan --min-score 65 --top 10` |
| "cek portofolio saya" | `port show` |
| *[user share screenshot]* + "ini portofolio saya" | `port snap <gambar>` |
| *[user share screenshot]* tanpa teks | deteksi layout → `port snap`, konfirmasi maksud |
| "update portofolio, ini screenshotnya" | `port snap` → diff → konfirmasi transaksi |
| "posisi mana yang harus dijual" | `port review` |
| "analisa BBCA" | `analyze BBCA --explain` |
| "backtest strategi breakout" | `backtest --strategy breakout --walk-forward` |
| "update data" | `data update` |
| "gimana performa trading saya bulan ini" | `port journal --month current` |

### 8.3 Aturan Perilaku Agent

Ditulis di `SKILL.md` agar Hermes berperilaku benar:

- **Selalu** sertakan SL dan TP saat menyebut rekomendasi beli. Jangan pernah menyebut entry saja.
- **Selalu** sertakan disclaimer singkat di akhir rekomendasi.
- **Jangan** menyatakan kepastian ("pasti naik", "dijamin profit"). Gunakan bahasa probabilistik.
- **Jangan** memberi rekomendasi untuk emiten yang tidak lolos filter likuiditas, meskipun user memintanya — jelaskan alasannya.
- Jika data lebih tua dari 2 hari bursa, **beri tahu user** sebelum menampilkan sinyal.
- Jika user tampak melakukan revenge trading (menambah posisi setelah cut loss beruntun), **tampilkan statistik disiplinnya** dan sarankan jeda.

---

## 9. Instalasi di Termux

### 9.1 Persyaratan

- Android 7.0+ , arm64-v8a
- Termux dari **F-Droid atau GitHub** (versi Play Store sudah usang dan bermasalah)
- Termux:API (opsional, untuk notifikasi)
- Storage bebas ≥ 500 MB
- RAM ≥ 3 GB direkomendasikan

### 9.2 Prosedur

```bash
# 1. Paket dasar
pkg update && pkg upgrade -y
pkg install -y python python-numpy python-pandas git sqlite libexpat rust binutils

# 2. Storage permission (untuk import/export file)
termux-setup-storage

# 3. Install
git clone https://github.com/msofyanmurtadlo/hermes-idx.git
cd hermes-idx
bash install.sh

# 4. Inisialisasi
hermes-idx init
hermes-idx config set modal 50000000
hermes-idx data update --full     # 15–40 menit pertama kali

# 5. Jalankan
hermes-idx scan
```

### 9.3 Catatan Teknis Termux

- **`python-numpy` dan `python-pandas` harus diinstall via `pkg`, bukan `pip`.** Build dari source akan memakan waktu sangat lama atau gagal.
- `pandas-ta` dipilih karena pure Python — TA-Lib butuh kompilasi C library terpisah yang tidak reliabel di Termux.
- Aktifkan wakelock (`termux-wake-lock`) sebelum operasi panjang agar Android tidak mematikan proses.
- Android agresif membunuh background process. Gunakan `termux-job-scheduler` untuk penjadwalan, bukan cron murni, dan pastikan Termux di-exclude dari battery optimization.
- Jika `pip install` gagal karena rust, set `CARGO_BUILD_TARGET=aarch64-linux-android`.

### 9.4 Verifikasi

```bash
hermes-idx doctor
```
Memeriksa: versi Python, ketersediaan dependency, koneksi ke sumber data, integritas database, permission storage, dan ketersediaan Termux:API.

---

## 10. Model Data

```sql
CREATE TABLE emiten (
    ticker TEXT PRIMARY KEY, nama TEXT, sektor TEXT, sub_sektor TEXT,
    papan TEXT, listing_date DATE, shares_outstanding INTEGER,
    is_active INTEGER DEFAULT 1, delisting_date DATE
);

CREATE TABLE ohlcv (
    ticker TEXT, date DATE, open REAL, high REAL, low REAL,
    close REAL, adj_close REAL, volume INTEGER, value REAL,
    frequency INTEGER, is_anomaly INTEGER DEFAULT 0,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX idx_ohlcv_date ON ohlcv(date);

CREATE TABLE corporate_action (
    ticker TEXT, ex_date DATE, type TEXT, ratio REAL, value REAL
);

CREATE TABLE signal (
    id INTEGER PRIMARY KEY, ticker TEXT, signal_date DATE, strategy TEXT,
    action TEXT, entry_price REAL, stop_loss REAL, tp1 REAL, tp2 REAL,
    position_lot INTEGER, risk_rp REAL, rr_tp1 REAL, score REAL,
    valid_until DATE, notes TEXT, status TEXT DEFAULT 'ACTIVE'
);

CREATE TABLE transaksi (
    id INTEGER PRIMARY KEY, ticker TEXT, date DATE, type TEXT,
    lot INTEGER, price REAL, fee REAL, signal_id INTEGER,
    source TEXT,          -- 'manual' | 'stockbit_import' | 'signal'
    notes TEXT
);

CREATE TABLE snapshot (
    id INTEGER PRIMARY KEY, taken_at TIMESTAMP, source TEXT,   -- 'screenshot'|'csv'|'manual'
    layout TEXT,                    -- 'portfolio' | 'history'
    engine TEXT,                    -- 'vision' | 'tesseract'
    image_count INTEGER,
    total_value_parsed REAL, total_value_reported REAL,
    cash_balance REAL, confirmed INTEGER DEFAULT 0
);

CREATE TABLE snapshot_row (
    id INTEGER PRIMARY KEY, snapshot_id INTEGER, ticker TEXT,
    lot INTEGER, avg_price REAL, last_price REAL, market_value REAL,
    unrealized_pnl REAL, confidence TEXT,      -- 'exact'|'estimated'|'unknown'
    validation_flag TEXT, raw_text TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshot(id)
);

CREATE TABLE inferred_transaksi (
    id INTEGER PRIMARY KEY, from_snapshot_id INTEGER, to_snapshot_id INTEGER,
    ticker TEXT, type TEXT, lot INTEGER, price REAL,
    confidence TEXT, status TEXT DEFAULT 'PENDING',  -- PENDING|CONFIRMED|REJECTED
    resolved_transaksi_id INTEGER
);

CREATE TABLE posisi (
    ticker TEXT PRIMARY KEY, lot INTEGER, avg_price REAL,
    stop_loss REAL, tp1 REAL, tp2 REAL, entry_date DATE,
    signal_id INTEGER, strategy TEXT
);

CREATE TABLE trade_closed (
    id INTEGER PRIMARY KEY, ticker TEXT, entry_date DATE, exit_date DATE,
    entry_price REAL, exit_price REAL, lot INTEGER, pnl_rp REAL,
    pnl_pct REAL, r_multiple REAL, strategy TEXT, exit_reason TEXT,
    followed_plan INTEGER, sl_respected INTEGER
);

CREATE TABLE backtest_result (
    id INTEGER PRIMARY KEY, strategy TEXT, run_date DATE,
    period_start DATE, period_end DATE, method TEXT,
    total_trades INTEGER, win_rate REAL, expectancy REAL,
    profit_factor REAL, max_dd REAL, sharpe REAL, p_value REAL,
    params_json TEXT
);
```

---

## 11. Konfigurasi

```yaml
# ~/.hermes/skills/idx-screener/config/user.yaml

akun:
  modal: 50_000_000
  risk_per_trade_pct: 1.0
  max_position_pct: 20.0
  max_open_positions: 8
  max_sector_exposure_pct: 40.0

biaya:
  fee_beli_pct: 0.15
  fee_jual_pct: 0.25
  slippage_tick: 1

screening:
  strategies: [breakout, pullback, momentum_rs]
  min_score: 65
  max_results: 15
  market_regime_filter: true

universe:
  min_avg_value_20d: 2_000_000_000
  min_price: 50
  min_listing_days: 250
  exclude_boards: ["Pemantauan Khusus"]

exit:
  time_stop_days: 20
  breakeven_at_r: 1.0
  trailing_activate_at_r: 1.5
  trailing_method: chandelier

notifikasi:
  channel: [stdout, termux]
  daily_time: "17:30"
  alert_near_sl_pct: 2.0

stockbit:
  input_mode: screenshot      # screenshot | csv | manual
  ocr_engine: vision          # vision | tesseract
  keep_screenshots: false     # true = arsipkan gambar mentah (terenkripsi)
  auto_reconcile: true
  require_confirmation: true  # WAJIB true; parsing tidak pernah auto-commit
  reminder_after_signal: true # ingatkan screenshot setelah eksekusi sinyal
  price_validation: true      # validasi silang angka ke data harga historis
```

---

## 12. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Sumber data gratis berubah/mati | Screening tidak jalan | Adapter pattern multi-sumber, fallback CSV, alert ke user |
| Overfitting strategi | Backtest bagus, live rugi | Walk-forward wajib, uji signifikansi, warning degradasi OOS |
| User menganggap ini jaminan profit | Kerugian finansial + reputasi | Disclaimer di setiap output, tampilkan drawdown & losing streak, bahasa probabilistik |
| OCR salah baca angka | Posisi & P/L salah, sinyal jual keliru | Validasi silang ke data harga, cek konsistensi `nilai = lot × harga`, konfirmasi user wajib sebelum commit |
| Stockbit ubah tampilan UI | Parser pecah | Deteksi layout via kata kunci header (bukan koordinat piksel), fallback ke OCR + input manual, uji regresi dengan korpus screenshot |
| Baris terlewat saat screenshot panjang | Posisi hilang dari tracking | Cross-check total nilai vs ringkasan atas, deteksi baris terpotong di tepi gambar |
| User jarang screenshot | Inferensi transaksi ambigu | Pengingat otomatis setelah eksekusi sinyal, tandai `AMBIGUOUS` dan minta input manual |
| Screenshot = data finansial sensitif | Kebocoran privasi | Gambar tidak disimpan permanen, mode OCR lokal offline, tanpa telemetri |
| Riwayat transaksi tidak lengkap | Statistik personal menyesatkan | Tampilkan cakupan data eksplisit (PT-11), jangan hitung seolah data lengkap |
| Android kill background process | Scheduled run gagal | termux-job-scheduler, wakelock, panduan battery optimization |
| Sinyal di saham tidak likuid | Tidak bisa exit, slippage besar | Filter likuiditas non-negotiable, cap size vs volume harian |
| Performa lambat di HP low-end | User frustrasi | Vectorized compute, cache indikator, mode `--quick` untuk universe terbatas |
| Corporate action tidak ter-handle | Sinyal palsu dari gap harga | Adjusted price, deteksi anomali, cross-check ke data IDX |

---

## 13. Roadmap

**v1.0 — MVP (8 minggu)**
Data layer, 4 strategi bawaan, signal builder lengkap dengan SL/TP/sizing, backtest walk-forward, **portfolio tracker berbasis screenshot** (parsing, validasi, konfirmasi, snapshot diffing) + input manual + CSV, CLI, installer Termux, integrasi Hermes.

**v1.1 — Kualitas (4 minggu)**
Custom strategy YAML, trading journal & evaluasi disiplin, notifikasi Telegram, dashboard HTML export, `doctor` diagnostic.

**v1.2 — Data (4 minggu)**
Filter fundamental (PER, PBV, ROE, DER, pertumbuhan laba), data broker summary (bandarmology dasar), foreign flow.

**v2.0 — Lanjutan**
Intraday timeframe, portfolio optimizer (korelasi & alokasi), Monte Carlo simulation untuk risk of ruin, multi-akun, sinkronisasi opsional dengan broker lain.

---

## 14. Kriteria Penerimaan v1.0

- [ ] `hermes-idx scan` selesai < 300 detik di Snapdragon 6-series
- [ ] 100% sinyal BELI memiliki entry, SL, TP1, TP2, size, dan R:R terisi
- [ ] Semua harga output valid terhadap tick size IDX
- [ ] Backtest 4 strategi bawaan berjalan walk-forward dengan laporan lengkap dan p-value
- [ ] Biaya transaksi terhitung di semua kalkulasi P/L dan backtest
- [ ] Portfolio tracker: add, sell, show, review, snap, diff, import CSV, reconcile berfungsi
- [ ] Parsing screenshot: akurasi ≥ 98% pada korpus uji 50 screenshot (ticker, lot, avg price)
- [ ] Multi-screenshot: dedup overlap dan deteksi baris terpotong berfungsi
- [ ] Snapshot diffing menghasilkan inferensi transaksi yang benar pada 20 skenario uji (tambah posisi, jual sebagian, jual penuh, posisi baru, tanpa perubahan)
- [ ] Tidak ada hasil parsing yang masuk database tanpa konfirmasi user
- [ ] Mode OCR lokal berfungsi tanpa koneksi internet
- [ ] Gambar mentah terhapus setelah parsing bila `keep_screenshots: false`
- [ ] Instalasi bersih di Termux fresh install berhasil dengan satu `install.sh`
- [ ] `hermes-idx doctor` mendeteksi semua kondisi error yang terdokumentasi
- [ ] Tidak ada kredensial yang tertulis di log atau terkirim keluar perangkat
- [ ] Disclaimer muncul di setiap output rekomendasi
- [ ] Dokumentasi: README, panduan instalasi, penjelasan tiap strategi, format CSV import

---

## 15. Lampiran — Format CSV Import Portofolio

```csv
ticker,date,type,lot,price,fee,notes
BBCA,2026-07-20,BUY,15,9250,20812,entry sesuai sinyal breakout
ADRO,2026-07-22,BUY,20,2410,7230,
BBCA,2026-07-28,SELL,5,9780,12225,TP1
```

Kolom `fee` opsional — bila kosong, dihitung otomatis dari konfigurasi. Kolom `notes` opsional.

---

## 16. Lampiran — Panduan Screenshot untuk User

Kualitas parsing sangat bergantung pada kualitas screenshot. Panduan ini ditampilkan saat `hermes-idx port snap --help` dan saat parsing gagal.

**Yang perlu dilakukan:**
- Screenshot **tab Portfolio** dalam mode terang (light mode lebih akurat untuk OCR)
- Pastikan **kolom lot, avg price, dan last price terlihat** — geser horizontal bila terpotong
- Untuk portofolio panjang, screenshot bertahap dengan **overlap 1–2 baris** antar gambar
- Sertakan **ringkasan total di bagian atas** pada gambar pertama — dipakai untuk validasi silang
- Screenshot **setiap kali selesai transaksi**, bukan mingguan — makin sering, makin akurat inferensi transaksinya
- Bila memungkinkan, sertakan juga screenshot **tab Riwayat/Order** — ini memberi tanggal dan harga eksekusi yang tidak ada di tab Portfolio

**Yang perlu dihindari:**
- Screenshot yang di-crop terlalu ketat sampai header kolom hilang
- Foto layar HP pakai kamera HP lain (blur, moiré, perspektif miring)
- Resolusi diturunkan atau dikompres berat sebelum dikirim
- Mode gelap dengan kontras rendah

**Bila parsing salah:** koreksi lewat opsi `[E]dit baris` di tabel konfirmasi, atau perbaiki manual dengan `hermes-idx port add/sell`. Sistem tidak akan pernah menimpa data yang sudah Anda konfirmasi tanpa persetujuan baru.

---

*Dokumen ini adalah spesifikasi teknis produk perangkat lunak, bukan nasihat investasi.*
