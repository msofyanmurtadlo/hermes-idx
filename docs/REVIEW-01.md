# Review Kritis PRD v1.0 — Temuan & Lubang Terbuka

**Tanggal:** 29 Juli 2026
**Terhadap:** [`docs/PRD.md`](PRD.md) v1.0
**Status:** belum ada yang diperbaiki di PRD — dokumen ini adalah daftar utang desain.

Dokumen ini sengaja dipublikasikan bersama PRD. Kalau Anda mempertimbangkan berkontribusi,
baca ini dulu — di sinilah pekerjaan yang menarik berada.

---

## Blocker — harus diselesaikan sebelum coding

### B1. Auto Rejection (ARA/ARB) tidak disebut sama sekali

Ini bias struktural terbesar di backtest IDX. Strategi S1 (breakout + volume) dan S3
(momentum RS) justru yang paling sering memicu ARA. BT-1 mengasumsikan "entry pada open
T+1 + slippage" — padahal saat ARA tidak ada offer untuk dibeli. Backtest akan mencatat
entry yang di dunia nyata tidak pernah terisi, lalu menghitung profitnya. Overestimasi-nya
sistematis dan besar, persis di strategi yang PRD andalkan.

**Usulan:** skip/tandai trade bila open T+1 menyentuh batas ARA, atau bila tidak ada volume
di sisi offer. Sama untuk exit saat ARB. Tambahkan penanganan suspensi & UMA.

### B2. "Walk-forward" tanpa ada yang dilatih

S1–S4 adalah rule-based dengan parameter tetap (20-hari, 2× volume, ADX>20). Skema
"train 2 tahun → test 6 bulan" hanya bermakna kalau periode train memfit sesuatu. Di sini
tidak ada. Yang sebenarnya dilakukan adalah rolling out-of-sample backtest — dan 2 tahun
train terbuang percuma. BT-4 (peringatan overfitting saat optimasi parameter) makin
membingungkan karena tidak ada mekanisme optimasi yang dispesifikasikan di mana pun.

**Usulan:** pilih satu — (a) sebut jujur "rolling OOS backtest" dan hapus split train, atau
(b) definisikan apa yang di-fit di train (seleksi parameter, atau bobot skor SB-5), dan
barulah walk-forward punya arti.

### B3. Skor sinyal SB-5 sirkular dengan backtest, dan berisiko look-ahead

Komponen "Historical edge strategi" (35%) berasal dari hasil walk-forward. Tapi backtest
harus menghasilkan trade, sementara produksi memfilter skor < 55 — jadi backtest perlu
mereplikasi filter itu, yang perlu skor, yang perlu backtest. Kalau expectancy periode-penuh
dipakai untuk menilai sinyal historis, itu look-ahead — persis yang dilarang BT-1.

**Usulan:** nyatakan urutannya eksplisit — backtest dijalankan tanpa filter skor, dan skor
point-in-time hanya boleh memakai fold yang sudah selesai per tanggal itu.

### B4. Filter likuiditas bergantung pada field yang sumber prioritas-1 tidak sediakan

`min_avg_value_20d` butuh nilai transaksi rupiah. Yahoo Finance memberi volume (lembar),
bukan `value`. Kolom `frequency` di schema `ohlcv` tidak ada di Yahoo sama sekali.
Aproksimasi `volume × close` meleset karena volume Yahoo untuk ticker `.JK` sendiri sering
tidak reliabel di luar large cap.

**Usulan:** turunkan `value` dari sumber IDX (bukan Yahoo), atau akui aproksimasinya dan
hapus `frequency` dari schema. Jangan biarkan requirement "non-negotiable" bertumpu pada
data yang tidak ada.

### B5. Point-in-time universe (BT-1) tidak bisa dipenuhi dengan stack yang dipilih

Data emiten delisted IDX praktis tidak tersedia gratis — Yahoo tidak menyimpannya. Selain
itu filter universe (`min_listing_days`, `is_active`, papan pencatatan) dihitung dari kondisi
hari ini; menerapkannya ke backtest 5 tahun ke belakang justru *memasukkan* look-ahead.

**Usulan:** turunkan dari requirement menjadi limitasi terdokumentasi, dan nyatakan arah
biasnya (survivorship → optimis).

---

## Serius — akan menggigit implementor

### S1. Contoh output di §7.1 angkanya tidak konsisten dengan spec sendiri

- **REVIEW POSISI:** total unrealized tertulis **+Rp 1.284.000**, hitungan dari baris-barisnya
  **+Rp 729.000** (BBCA +795k, ADRO −240k, TLKM +150k, MDKA +24k).
- **REKOMENDASI BELI:** dengan rumus SB-3 (modal 50jt, risk 1%, risk/share 110) hasilnya
  **45 lot**, bukan 28 lot. 45 lot = Rp 7,875jt = 15,75% modal, masih di bawah cap 20%;
  cap volume 5% juga tidak memotong. Tidak ada aturan di PRD yang menghasilkan 28.

Sisanya konsisten (tick size, R:R 2.0/4.0, persentase SL/TP semua cek). Tapi contoh output
selalu jadi test fixture pertama implementor.

### S2. Aturan pembulatan SB-2 bertentangan dengan alasannya sendiri

"SL dibulatkan ke bawah (memberi ruang sedikit lebih)" — untuk posisi long, SL lebih rendah
= risiko per lembar lebih besar = loss lebih besar. Itu bukan konservatif. Untuk
`entry_type: buy_stop`, membulatkan trigger ke bawah = entry lebih awal = juga kurang
konservatif. Aturan pembulatan harus per-field dan sadar arah posisi.

### S3. `buy_stop` kemungkinan tidak bisa dieksekusi user

Broker ritel IDX umumnya tidak menyediakan stop-buy order standar. Sinyal dengan
`entry_type: buy_stop` jadi tidak actionable — bertentangan dengan G2. Perlu verifikasi ke
broker target dan fallback "amati, entry manual bila tembus X".

### S4. `50_000_000` di YAML adalah jebakan portabilitas

PyYAML (YAML 1.1) menerima underscore sebagai pemisah digit. Parser YAML 1.2 (ruamel mode
1.2, dan sebagian besar parser non-Python) memparse itu sebagai **string**, bukan int. PRD
tidak menyebut library YAML mana yang dipakai. Config `modal` yang diam-diam jadi string
akan meledak di tempat lain.

### S5. Bootstrap i.i.d. di BT-3 menghasilkan p-value terlalu optimis

Trade tidak independen: banyak posisi dibuka di hari yang sama, di pasar yang bergerak
bersama. Resampling per-trade mengabaikan korelasi silang itu dan menyempitkan confidence
interval secara artifisial. Pakai block bootstrap berbasis tanggal.

### S6. SE-2 bertentangan dengan contoh laporan BT-2

Kalau `market_regime_filter` menonaktifkan strategi long-only saat IHSG < MA200 selama
>10 hari, seharusnya nyaris tidak ada trade di bear market. Tapi BT-2 melaporkan
"Bear −8.9%/th" seolah ada sampel yang cukup.

### S7. Rumus PT-DIFF-1 punya masalah presisi dan kasus pecah senyap

`(lot_baru × avg_baru − lot_lama × avg_lama) / (lot_baru − lot_lama)` adalah selisih dua
bilangan besar dibagi bilangan kecil — error dari avg price yang sudah dibulatkan di layar
akan teramplifikasi, makin parah saat penambahan lot-nya kecil. Rumus ini juga pecah
diam-diam bila di antara dua snapshot ada BELI *dan* JUAL pada ticker yang sama: hasilnya
terlihat masuk akal padahal salah. PT-DIFF-4 hanya menandai ambiguitas untuk "3 transaksi".

**Usulan:** propagasikan error dan turunkan confidence ke `estimated` bila error > 1 tick.
Perlakukan kombinasi beli+jual pada ticker sama sebagai `AMBIGUOUS` walau hanya 2 transaksi.
Verifikasi juga: apakah avg price Stockbit sudah termasuk fee beli? Kalau ya, rumus ini
menghasilkan harga+fee, bukan harga eksekusi.

### S8. Klaim "direktori lokal terenkripsi" (PT-V7) adalah enkripsi teater

Di Termux tanpa akses keystore, kunci akan tersimpan di disk sebelah datanya. Itu tidak
melindungi dari skenario ancaman realistis mana pun. Entah pakai passphrase user (dan terima
konsekuensi UX-nya), atau jangan klaim terenkripsi.

Terkait: default `ocr_engine: vision` berarti **default-nya screenshot portofolio dikirim
keluar perangkat**. Kalimat "tidak ada upload ke server pihak ketiga selain vision model yang
dipilih user" secara teknis benar tapi menguburkan fakta itu. Untuk data finansial, jalur
keluar data harus dinyatakan gamblang di kepala §PT-V7, plus consent sekali di awal.

---

## Menengah

| # | Temuan |
|---|---|
| M1 | Schema DB tidak menampung SB-1: tabel `signal` tidak punya `entry_zone`, `tp1_size`, `tp2_size`, `confidence`, `entry_type`. Tambahan: `transaksi` tanpa UNIQUE constraint → import CSV dua kali menduplikasi senyap; `ohlcv` tanpa `source`/`fetched_at` padahal desainnya multi-adapter; tidak ada `schema_version` padahal roadmap v1.0→v1.2 pasti butuh migrasi. |
| M2 | Odd lot: `market_value ≈ lot × 100 × last_price` (PT-V4) pecah untuk odd lot dari right issue/waran. Stockbit menampilkannya. |
| M3 | G1/IE-2 over-spec sekaligus belum diuji. Setelah filter likuiditas Rp 2M/hari, universe efektif tinggal ~200–400 emiten, bukan 900. Sebaliknya 1,1 juta baris × 25 indikator float64 ≈ 225 MB hanya untuk indikator, di HP RAM 3GB. Turunkan target ke universe pasca-filter, pakai float32, proses per-ticker streaming, dan jadikan benchmark ini spike minggu pertama. |
| M4 | Kriteria akurasi parsing ≥98% tidak terdefinisi — per baris, per field, atau per screenshot? 98% per-field atas 50 screenshot ≈ 30 field salah. Juga: korpus 50 screenshot itu data finansial pribadi, perlu rencana korpus sintetis/anonim. |
| M5 | Mode OCR lokal kemungkinan besar tidak akan mendekati mode vision. Tesseract pada tabel padat di layar HP tanpa deskew/threshold/deteksi struktur tabel akurasinya jatuh drastis. Preprocessing yang layak menyeret OpenCV, yang berat di Termux. Tetapkan ambang akurasi terpisah dan posisikan sebagai fallback degraded. |
| M6 | Slippage 1 tick terlalu optimis di batas bawah universe. Saham dengan value Rp 2 miliar/hari punya spread beberapa tick. Model slippage sebagai fungsi ukuran order relatif volume, minimal setengah spread. Tambahkan juga fee minimum per transaksi (banyak broker menerapkan) — signifikan untuk posisi kecil. |
| M7 | Tick size perlu punya `effective_date`. BEI pernah mengubah fraksi harga; backtest 5 tahun melewati lebih dari satu rezim. Verifikasi juga tabel di SB-2 ke peraturan BEI terkini. |
| M8 | PT-6 "Stockbit selalu sumber kebenaran" perlu diperjelas — yang jadi sumber kebenaran sebenarnya adalah *hasil parsing yang sudah dikonfirmasi user*, bukan output OCR mentah. Dengan `auto_reconcile: true`, kalimat sekarang bisa dibaca sebagai izin menimpa data benar dengan hasil salah baca. |
| M9 | Contoh output PT-7 melanggar PT-11: laporan disiplin menampilkan persentase tanpa cakupan data. Orang menyalin contoh, bukan requirement. |
| M10 | §8.3 berisiko memblokir `analyze`. Aturan "jangan beri rekomendasi untuk emiten yang tidak lolos filter likuiditas" benar, tapi bisa dibaca sebagai larangan menganalisis. Pisahkan tegas: analisis boleh untuk emiten apa pun, rekomendasi tidak. |
| M11 | Target §3.2 bukan bagian dari kriteria penerimaan §14 — artinya v1.0 bisa lolos acceptance dengan keempat strategi ber-expectancy negatif. Nyatakan eksplisit apakah §3.2 gate rilis atau aspirasi. |
| M12 | Timeline 8 minggu untuk v1.0 tidak realistis. Cakupannya: data layer multi-adapter + corporate action, 4 strategi, engine walk-forward + bootstrap, vision parsing + OCR lokal + dedup multi-screenshot + snapshot diffing + rekonsiliasi, CLI penuh, installer Termux, integrasi agent. Untuk satu orang itu 4–6 bulan. Pindahkan seluruh modul screenshot ke v1.1 — itu bagian paling berisiko sekaligus paling tidak esensial untuk nilai inti. |

---

## Yang sudah bagus dan sebaiknya dipertahankan

- Menolak reverse-engineer API Stockbit (PT-0) — keputusan benar, dan alasannya ditulis,
  bukan cuma dilarang.
- Mengoptimasi expectancy/profit factor alih-alih win rate, lengkap dengan penjelasan kenapa
  win rate mudah dimanipulasi. Ini bagian terkuat dokumen.
- PT-11 (degradasi anggun dengan cakupan data eksplisit) dan PT-DIFF-3 (tidak pernah commit
  tanpa konfirmasi) — dua requirement yang biasanya baru disadari orang setelah kena masalah.
- Menyadari keterbatasan fundamental screenshot sebagai snapshot-bukan-log, dan
  menuliskannya sebelum implementasi.
- PT-7 (jurnal disiplin). Setuju dengan catatan PRD sendiri: ini kemungkinan besar berdampak
  lebih besar ke profitabilitas user daripada perbaikan strategi apa pun.

---

## Prioritas

Kalau waktu terbatas: **B1, B2, B3, dan M12.** Empat ini mengubah arsitektur dan rencana
rilis. Sisanya bisa diperbaiki sambil jalan.
