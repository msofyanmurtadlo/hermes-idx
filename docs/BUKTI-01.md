# Bukti Uji — Adu Strategi, Juli 2026

**Pertanyaan:** dari script v3 dan strategi hermes-idx, mana yang paling menguntungkan?
**Jawaban singkat:** tidak satu pun. Semuanya rugi setelah biaya pada periode uji.

Dokumen ini memuat angkanya supaya bisa diperiksa dan dibantah.

---

## Cara uji

Semua kandidat dijalankan pada kondisi **identik**: universe, periode, dan biaya yang
sama, lewat engine yang sama.

| Parameter | Nilai |
|---|---|
| Universe | 51 emiten bluechip IDX + IHSG |
| Periode | 2021-06 s/d 2026-07 (~5,1 tahun, 60.598 bar) |
| Sumber | Yahoo Finance (`{KODE}.JK`) |
| Eksekusi | Sinyal close T → entry open T+1 + slippage 1 tick |
| Biaya | Beli 0,15% · Jual 0,25% (termasuk PPh final) |
| Auto rejection | Sinyal yang open T+1-nya kena ARA dihitung `unfilled`, tidak dihitung untung |
| Metode | Rolling out-of-sample (bukan walk-forward — tidak ada parameter yang di-fit) |
| Peringkat | Expectancy + profit factor. **Win rate tidak diberi bobot** |

Reproduksi: `hermes-idx data seed-bluechip && hermes-idx data update && hermes-idx compare`

---

## Hasil

| Strategi | Trade | Win% | Expectancy | PF | Max DD | Vonis |
|---|---:|---:|---:|---:|---:|---|
| mean_reversion | 282 | 58% | **−0,082R** | 0,79 | −22,5% | rugi setelah biaya |
| v3score *(stop diperbaiki)* | 755 | 39% | −0,143R | 0,71 | −72,6% | rugi setelah biaya |
| breakout | 176 | 22% | −0,213R | 0,73 | −38,7% | rugi setelah biaya |
| momentum_rs | 380 | 28% | −0,311R | 0,46 | −69,9% | rugi setelah biaya |
| pullback | 735 | 20% | −0,377R | 0,47 | −94,0% | rugi setelah biaya |

Target PRD §3.2 adalah expectancy > +0,25R dan profit factor > 1,5. **Tidak ada yang
mendekati.** Tidak ada pula yang signifikan secara statistik.

Perhatikan `mean_reversion`: win rate 58% — tertinggi — tapi tetap rugi. Persis jebakan
yang diperingatkan PRD sejak awal, dan alasan sistem ini tidak memakai win rate sebagai
dasar peringkat.

---

## Kontrol: apakah engine-nya yang rusak?

Kalau semua strategi rugi, kecurigaan pertama harus ke engine-nya, bukan ke strateginya.
Jadi diuji pembanding paling sederhana — beli lalu tahan:

```
51 emiten bluechip, ~5,1 tahun
  return total  : median +1,8%   rata-rata +67,3%
  return/tahun  : median +0,3%   rata-rata +1,4%
  yang naik     : 26 dari 51 emiten
  IHSG          : +1,4% total  (+0,3%/tahun)
```

**IHSG hanya naik 1,4% dalam 5 tahun.** Pasarnya datar, bukan bull. Rata-rata +67% itu
menyesatkan — ditarik dua outlier (DSSA +2.130%, ADMR +967%); median +1,8% jauh lebih
mewakili. Sisi lain sama ekstremnya: GOTO −87%, SMGR −84%, EMTK −80%.

Jadi engine-nya wajar. Di pasar datar, strategi long-only yang sering bertransaksi memang
kalah oleh biaya — itu aritmetika, bukan bug.

---

## Bug yang ditemukan di script v3

Ini temuan paling berharga dari latihan ini.

```python
# idx-bluechip-report.py v3, baris 563-565
sl_atr = entry - 1.5 * atr
sl_pct = entry * 0.98
sl = round_tick(max(sl_atr, sl_pct))     # <-- max() memilih stop yang lebih RAPAT
```

ATR harian bluechip IDX biasanya 2–3%, jadi `1,5 × ATR` hampir selalu **lebih lebar**
dari 2%. Akibatnya cabang ATR tidak pernah menang. Diukur pada data:

```
lebar stop v3: median 2,00% | p10 2,00% | p90 2,00%
```

Ketiganya persis sama. Fitur andalan v3 — dokumentasinya sendiri menulis *"ATR untuk
SL/TP dinamis (bukan % tetap)"* — **tidak pernah berjalan**. Yang aktif adalah stop tetap
2%.

Dampaknya besar, karena stop rapat memperbesar porsi biaya:

```
biaya round-trip 0,40% ÷ stop 2,00% = 0,20R per trade, hanya untuk fee
```

Dekomposisi kerugian v3 (sebelum perbaikan):

| Skenario | Trade | Win% | Expectancy | Avg loss |
|---|---:|---:|---:|---:|
| apa adanya | 1.746 | 14% | −0,765R | −1,41R |
| tanpa slippage | 1.676 | 17% | −0,444R | −1,24R |
| tanpa fee | 1.746 | 14% | −0,584R | −1,24R |
| tanpa fee & slippage | 1.676 | 17% | −0,262R | −1,06R |

Bahkan tanpa biaya sama sekali, sinyalnya masih rugi. Biaya memperparah, bukan penyebab
tunggal. `avg_loss` −1,41R (seharusnya ≈ −1R) menunjukkan stop 2% ditembus gap terus.

**Perbaikan:** `min()` agar ATR yang memimpin dan 2% jadi lantai, bukan langit-langit.

| Stop | Trade | Win% | Expectancy | Avg loss | Holding |
|---|---:|---:|---:|---:|---:|
| 2% tetap *(v3 asli)* | 1.746 | 14% | −0,765R | −1,41R | 3,0 hari |
| ATR 1,5× | 884 | 23% | −0,371R | −1,14R | 16,3 hari |
| ATR 2,0× | 745 | 32% | −0,197R | −1,02R | 23,0 hari |
| ATR 2,5× | 709 | 35% | −0,123R | −0,90R | 25,9 hari |
| ATR 3,0× | 677 | 38% | −0,100R | −0,80R | 28,5 hari |

Membaik 7×. Tetap negatif.

> **Peringatan overfitting (BT-4).** Tabel di atas adalah eksplorasi **in-sample** — empat
> nilai diuji pada data yang sama lalu dipilih yang terbaik. Angka 3,0× tidak boleh
> diperlakukan sebagai temuan yang tervalidasi. Kalau nanti dilakukan optimasi parameter
> sungguhan, wajib pakai walk-forward yang benar dan laporkan degradasi in-sample vs OOS.

---

## Apa artinya untuk pemakai

1. **Jangan pakai sinyal apa pun dari repo ini untuk uang sungguhan sekarang.** Jalankan
   `hermes-idx compare` pada data Anda sendiri. Kalau tidak ada yang expectancy positif
   dengan p ≤ 0,05, tidak ada yang layak dipakai.
2. **Tidak menghasilkan sinyal adalah hasil yang benar** di pasar datar. Filter rezim
   IHSG, ambang skor, dan batas konsentrasi memang dirancang untuk menahan diri.
3. **Angka "akurasi" v3 yang lama tidak sebanding** dengan expectancy. Itu mengukur arah
   harga esok hari — tanpa stop loss, tanpa biaya. Sinyal BELI yang naik +0,05% dihitung
   "benar", padahal setelah fee 0,40% itu rugi.

---

## Yang belum terjawab

- Periode ujinya satu rezim pasar datar. Belum diketahui perilakunya di bull market —
  butuh data lebih panjang atau periode lain.
- Data delisting tidak tersedia (issue #5), jadi ada survivorship bias yang arahnya
  **optimis**. Hasil sebenarnya kemungkinan lebih buruk dari tabel di atas.
- Konfirmasi 1 jam milik v3 (bobot sampai +3) tidak ikut diuji — tidak ada data historis
  intraday. Mungkin itu bagian yang membuat v3 berguna di produksi; belum terbukti.
- Nilai transaksi harian masih estimasi `volume × close` (issue #4), jadi filter
  likuiditasnya aproksimasi.

---

## BUKTI-02 — Panjang MA rezim pasar (2026-07-31)

**Pertanyaan:** MA200 dipakai sebagai filter rezim. Apakah itu terlalu panjang untuk
horizon perdagangan harian?

**Sumber di luar repo.** [Fidelity](https://www.fidelity.com/viewpoints/active-investor/moving-averages)
menyebut MA200 sebagai *"a valuable smoothing device when you are trying to assess
**long-term** trends"*, sementara MA50 *"will more closely follow the recent price
action"*. Praktik yang lazim dikutip: day trader memakai EMA 9/20 pada bar intraday,
swing trader 21/50 hari, dan 50/200 hari untuk investor jangka panjang. Jadi secara
konseptual MA200 pada bar harian memang menilai tren sekuler.

### Temuan 1 — memperpendek MA tidak monoton menambah sinyal

120 hari bursa terakhir, 51 bluechip, 4 strategi:

| MA rezim | Hari bullish | Hari ada sinyal | Total sinyal |
|---|---|---|---|
| 200 | 31/120 | 31/120 | 170 |
| 100 | 9/120 | 9/120 | 60 |
| 50 | 18/120 | 17/120 | 79 |
| 20 | 79/120 | 74/120 | 273 |

MA100 justru menghasilkan sinyal **paling sedikit**. Aturan "bearish setelah >10 hari
beruntun di bawah MA" berinteraksi dengan jalur harga: MA200 sesekali ditembus ke atas
sehingga streak-nya ter-reset, sedangkan di bawah MA100 IHSG bertahan tanpa putus.
Intuisi "makin pendek makin banyak sinyal" salah di sini.

### Temuan 2 — backtest tidak pernah menerapkan filter rezim

`screen.scan()` menolak entry saat rezim bearish; `backtest.simulate()` mengambil
semuanya. Jadi seluruh angka expectancy di BUKTI-01 mengukur strategi yang **tidak pernah
benar-benar dijalankan**, dan filter rezimnya sendiri tidak pernah teruji — padahal ia
yang menahan 73% hari bursa. Sudah diperbaiki; angka di bawah memakai filter yang sama
dengan screening live.

### Temuan 3 — periode terbaik bergantung strategi

| MA rezim | breakout | v3score | mean_reversion |
|---|---|---|---|
| 200 | −0,224 | −0,194 | **−0,082** |
| 50 | −0,219 | **−0,124** | −0,182 |
| 20 | −0,235 | −0,139 | −0,218 |

- **v3score** — logika dari script harian v3 — membaik jelas di MA50 (−0,194 → −0,124),
  dengan trade bertambah 523 → 651.
- **mean_reversion** justru **memburuk 2,2×** saat MA diperpendek. Masuk akal: ia membeli
  penurunan ekstrem dan bergantung pada tren besar yang masih utuh sebagai jaring pengaman.
- **breakout** hampir tidak terpengaruh.

**Keputusan:** default `screening.regime_ma_period` diubah 200 → **50**. Menang untuk dua
dari tiga strategi dan cocok dengan horizon perdagangan harian. Kalau `mean_reversion`
yang dipakai, kembalikan ke 200.

**Yang tidak berubah:** seluruh angka tetap negatif. Memperpendek MA memperbanyak
kesempatan bertransaksi, ia tidak menciptakan edge. Idealnya periode ini disetel
per-strategi, bukan global — belum diimplementasikan.
