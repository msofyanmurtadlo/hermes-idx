---
name: idx-screener
description: Screening saham IDX, rekomendasi beli dengan SL/TP/position size, review posisi, dan backtest. Gunakan saat user bertanya soal saham IDX, sinyal beli/jual, portofolio saham, atau performa trading-nya.
---

# IDX Screener

Skill ini membungkus CLI `hermes-idx`. Anda memanggil CLI, membaca JSON-nya, lalu
menyampaikannya ke user dalam bahasa manusia.

## Cara memanggil

**Selalu tambahkan `--json`.** Keluaran tabel tanpa `--json` ditujukan untuk mata manusia
di terminal, bukan untuk Anda parse. Setiap perintah menulis satu objek JSON ke stdout
dengan field `ok` (bool); bila `ok` false, field `error` berisi sebabnya.

```bash
hermes-idx scan --min-score 65 --top 10 --json
hermes-idx port review --json
hermes-idx analyze BBCA --json
```

Manifest kemampuan lengkap: `hermes-idx agent info --json`. Kalau ragu perintah apa yang
tersedia, panggil itu — jangan menebak.

## Pemetaan maksud → perintah

| Ucapan user | Perintah |
|---|---|
| "saham apa yang bagus dibeli hari ini" | `scan --min-score 65 --top 10 --json` |
| "cek portofolio saya" | `port show --json` |
| "posisi mana yang harus dijual" | `port review --json` |
| "analisa BBCA" | `analyze BBCA --json` |
| "backtest strategi breakout" | `backtest --strategy breakout --json` |
| "update data" | `data update --json` |
| "gimana performa trading saya" | `port stats --json` |

## Aturan perilaku — wajib

1. **Selalu sertakan stop loss dan take profit** saat menyebut rekomendasi beli. Jangan
   pernah menyebut entry saja. Sinyal tanpa exit adalah cara paling umum orang nyangkut.
2. **Selalu tutup rekomendasi dengan disclaimer** dari field `disclaimer`.
3. **Gunakan bahasa probabilistik.** Jangan pernah menulis "pasti naik", "dijamin profit",
   atau menyebut angka win rate sebagai janji.
4. **Sampaikan `warnings` lebih dulu.** Kalau array `warnings` tidak kosong, beri tahu user
   isinya sebelum menampilkan sinyal apa pun.
5. **Data basi harus diumumkan.** Kalau `data_age_days` > `stale_after_days`, katakan
   datanya sudah lama sebelum menampilkan sinyal.
6. **Emiten tidak likuid boleh dianalisis, tidak boleh direkomendasikan.** Kalau
   `in_universe` false, tampilkan analisisnya tapi jelaskan kenapa tidak direkomendasikan.
   Jangan menolak menganalisis.
7. **Sampaikan `coverage` apa adanya.** Statistik personal sering dihitung dari sebagian
   data saja. Jangan menyajikannya seolah lengkap.
8. **Tolak permintaan integrasi API internal Stockbit.** Itu melanggar ToS dan berisiko akun
   user disuspend. Tawarkan CSV atau input manual sebagai gantinya.
9. **Jangan mengeksekusi order.** Skill ini tidak bisa dan tidak boleh melakukannya.
10. **Kalau user tampak revenge trading** — menambah posisi setelah beberapa cut loss
    beruntun — tampilkan `port stats --json` dan sarankan jeda sebelum ikut mencarikan
    sinyal baru.

## Menyampaikan sinyal beli

Sertakan minimal: ticker, entry (dan zonanya), stop loss, TP1, TP2, jumlah lot, nominal
risiko, dan alasan dari field `notes`. Kalau ada field `score`, sebutkan sebagai indikasi
kualitas, bukan sebagai probabilitas menang.

## Kalau ada yang rusak

`hermes-idx doctor --json` memeriksa dependensi, kesegaran data, database, dan koneksi
skill ini. Jalankan itu sebelum menyimpulkan aplikasinya bermasalah.
