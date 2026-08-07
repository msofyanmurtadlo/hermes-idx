"""Laporan harian — pipeline lengkap dalam satu perintah.

Dirancang untuk dipanggil cron/Hermes. Prinsipnya: **laporan tetap berguna walau tidak
ada sinyal beli sama sekali.** Di pasar datar itu justru keadaan normal, dan laporan yang
memaksakan rekomendasi setiap hari akan mendorong overtrading — persis yang membuat
backtest rugi (lihat docs/BUKTI-01.md).

Urutan isi disusun dari yang paling mendesak: posisi yang perlu di-cut lebih penting
daripada sinyal beli baru.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess

from . import data as datamod, market, mcp, portfolio, pricing, screen, universe

URGENCY_ORDER = {"SEGERA": 0, "HARI INI": 1, "AMATI": 2}

BADGE = {"PELUANG": "🟢", "AMATI": "🟡", "PANTAU": "⚪"}
"""Label peringkat harian — lihat `screen.rank()` untuk definisi tiap tingkat."""


def fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Ambil harga real-time dari TradingView Scanner API (1 request, batch).

    Return {ticker: {"price": float, "chg_pct": float, "source": "TV"}}.
    Gagal → dict kosong (laporan fallback ke DB).
    """
    if not tickers:
        return {}
    symbols = [f"IDX:{t}" for t in tickers]
    payload = json.dumps({
        "symbols": {"tickers": symbols, "query": {"types": []}},
        "columns": ["close", "change", "volume", "high", "low", "open"],
    })
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "10",
             "https://scanner.tradingview.com/indonesia/scan",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15,
        )
        j = json.loads(r.stdout)
        result = {}
        for item in j.get("data", []):
            ticker = item["s"].split(":")[-1]
            d = item["d"]
            price = d[0]
            if price is not None and float(price) > 0:
                result[ticker] = {"price": float(price), "chg_pct": float(d[1] or 0), "source": "TV"}
        return result
    except Exception:
        return {}


def build(conn, cfg, as_of: dt.datetime | None = None) -> dict:
    """Kumpulkan seluruh isi laporan harian sebagai satu dict siap-render."""
    now = as_of or dt.datetime.now()
    report: dict = {
        "tanggal": now.date().isoformat(),
        "jam": now.strftime("%H:%M"),
        "peringatan": [],
        "sesi": None,
        "pasar": {},
        "posisi": [],
        "aksi": [],
        "saran_rencana": [],
        "sinyal": [],
        "peringkat": [],
        "edge": {},
    }

    session = universe.session_note(now.hour * 60 + now.minute)
    if session:
        report["sesi"] = {"level": session[0], "catatan": session[1]}

    # --- kesegaran data -----------------------------------------------------
    age = datamod.data_age_days(conn)
    report["umur_data_hari"] = age
    stale = cfg.data["data"]["stale_after_days"]
    if age is None:
        report["peringatan"].append("Database kosong — jalankan `hermes-idx data update`.")
    elif age > stale:
        report["peringatan"].append(
            f"Data terakhir berumur {age} hari (batas {stale}). Angka di bawah ini basi."
        )

    # --- rezim pasar --------------------------------------------------------
    ctx = screen.context(conn, cfg)
    benchmark = datamod.load_ohlcv(conn, cfg.data["data"]["benchmark"])

    # Ambil harga live untuk semua ticker porto + IHSG (1 request batch)
    positions = portfolio.positions(conn)
    live_tickers = [pos.ticker for pos in positions]
    # TradingView pakai simbol "COMPOSITE" untuk IHSG. Satu request untuk porto + IHSG
    # sekaligus — API-nya batch, dulu dipanggil dua kali tanpa alasan.
    live_all = fetch_live_prices([*live_tickers, "COMPOSITE"])

    # CADANGAN: bila scanner gagal (parsial/total), isi dari MCP TradingView.
    # Jalur data independen (Yahoo via server MCP), bukan endpoint yang sama.
    mcp_cmd = cfg.get("mcp.command", "")
    if mcp_cmd:
        missing = [t for t in live_tickers if t not in live_all]
        if "COMPOSITE" not in live_all:
            missing.append("COMPOSITE")
        if missing:
            quotes = mcp.fetch_quotes(missing, mcp_cmd, int(cfg.get("mcp.timeout", 60)))
            live_all.update(quotes)

    all_live = {t: v for t, v in live_all.items() if t != "COMPOSITE"}
    bench_live = {"COMPOSITE": live_all["COMPOSITE"]} if "COMPOSITE" in live_all else {}
    live_bench_price = bench_live.get("COMPOSITE", {}).get("price")
    live_bench_chg = bench_live.get("COMPOSITE", {}).get("chg_pct")

    # Peringatkan kalau live fetch gagal total — laporan pakai harga DB basi
    if live_tickers and not all_live:
        report["peringatan"].append(
            "⚠️ Harga live TradingView GAGAL diambil — laporan memakai harga DB "
            "(mungkin basi). Cek koneksi internet."
        )

    if benchmark.empty and not live_bench_price:
        report["pasar"] = {"tersedia": False}
        report["peringatan"].append(
            "Data IHSG tidak ada — rezim pasar tidak bisa dinilai, semua sinyal beli "
            "ditahan (fail-closed)."
        )
    else:
        # Harga IHSG: live dulu, fallback DB
        if live_bench_price:
            last = live_bench_price
            chg = live_bench_chg or 0.0
        else:
            last = float(benchmark["close"].iloc[-1])
            prev = float(benchmark["close"].iloc[-2]) if len(benchmark) > 1 else last
            chg = round((last / prev - 1) * 100, 2) if prev else 0.0
        bullish = bool(ctx.bullish_regime.iloc[-1]) if ctx.bullish_regime is not None else False
        # Periode MA disebut apa adanya, tidak dipatok "200": sejak periodenya bisa
        # diatur, label yang salah menyesatkan justru soal dasar keputusannya.
        ma = int(cfg.data["screening"].get("regime_ma_period", 200))
        report["pasar"] = {
            "tersedia": True,
            "ihsg": round(last, 2),
            "perubahan_pct": round(chg, 2),
            "bullish": bullish,
            "live": bool(live_bench_price),
            "regime_ma_period": ma,
            "catatan": f"IHSG di atas MA{ma}" if bullish else
                       f"IHSG di bawah MA{ma} — strategi long-only ditahan",
        }

    # --- posisi & aksi (paling mendesak duluan) -----------------------------
    total_pnl = 0.0
    total_value = 0.0
    live_count = 0
    for pos in positions:
        frame = datamod.load_ohlcv(conn, pos.ticker)
        # Harga: live TradingView dulu, fallback DB
        live = all_live.get(pos.ticker)
        if live:
            last = live["price"]
            live_count += 1
        elif not frame.empty:
            last = float(frame["close"].iloc[-1])
        else:
            # Rencana (SL/TP) tetap disertakan walau harga tidak ada — tanpa ini posisi
            # tak terpantau juga lolos dari pemeriksaan kewajaran TP di bawah.
            report["posisi"].append({
                "ticker": pos.ticker, "lot": pos.lot, "avg_price": pos.avg_price,
                "stop_loss": pos.stop_loss, "tp1": pos.tp1, "tp2": pos.tp2,
                "tanpa_sl": pos.stop_loss is None,
                "sektor": universe.sector_of(pos.ticker),
                "error": "tidak ada data harga",
            })
            continue
        # P/L dihitung dari harga live, termasuk fee (konsisten dengan review())
        pnl_rp = market.net_sell_value(last, pos.lot, cfg.fees) - market.net_buy_value(pos.avg_price, pos.lot, cfg.fees)
        pnl_pct = pnl_rp / market.net_buy_value(pos.avg_price, pos.lot, cfg.fees) * 100 if pos.avg_price else 0.0
        total_pnl += pnl_rp
        total_value += last * pos.lot * 100
        # Jarak ke SL/TP — info paling penting untuk monitoring harian.
        sl = pos.stop_loss
        tp1 = getattr(pos, "tp1", None)
        tp2 = getattr(pos, "tp2", None)
        sl_dist = round((last - sl) / last * 100, 1) if sl else None
        tp1_dist = round((tp1 - last) / last * 100, 1) if tp1 else None
        tp2_dist = round((tp2 - last) / last * 100, 1) if tp2 else None
        # Review aksi tetap pakai frame DB (butuh history indikator)
        if not frame.empty:
            saran = portfolio.plan_advice(pos, frame, cfg, last if live else None)
            if saran:
                report["saran_rencana"].append(saran)
            action = portfolio.review(pos, frame, cfg, now.date(),
                                      last_price=last if live else None)
            if action.action != "HOLD":
                report["aksi"].append({
                    "ticker": action.ticker, "aksi": action.action, "urgensi": action.urgency,
                    "alasan": action.reason, "harga_limit": action.limit_price,
                    "lot": action.lot, "estimasi_terima": action.est_proceeds,
                })
        report["posisi"].append({
            "ticker": pos.ticker, "lot": pos.lot, "avg_price": pos.avg_price,
            "last": last, "pnl_rp": pnl_rp, "pnl_pct": pnl_pct,
            "stop_loss": sl, "tp1": tp1, "tp2": tp2,
            "sl_dist_pct": sl_dist, "tp1_dist_pct": tp1_dist, "tp2_dist_pct": tp2_dist,
            "tanpa_sl": sl is None, "live": bool(live),
            "sektor": universe.sector_of(pos.ticker),
        })

    # Sanity porto — angka mustahil harus diteriakkan, bukan dirender rapi. Impor legacy
    # sempat menaruh WIDI dengan avg_price 1,1 (di bawah harga minimum bursa 50) sehingga
    # laporan memamerkan "P/L +2.263%", dan BMTR dengan TP1 di BAWAH harga beli.
    for pos_row in report["posisi"]:
        avg = pos_row.get("avg_price") or 0
        if 0 < avg < market.HARGA_MIN:
            report["peringatan"].append(
                f"{pos_row['ticker']}: harga beli tercatat {avg:,.2f}, di bawah harga "
                f"minimum bursa Rp{market.HARGA_MIN}. Hampir pasti salah impor — P/L-nya "
                f"tidak bisa dipercaya. Perbaiki dengan `port add`/`port plan`."
            )
        tp1_row = pos_row.get("tp1")
        if tp1_row and avg and tp1_row <= avg:
            report["peringatan"].append(
                f"{pos_row['ticker']}: TP1 {tp1_row:,.0f} berada di bawah harga beli "
                f"{avg:,.0f} — kena target justru rugi. Setel ulang lewat `port plan`."
            )

    report["aksi"].sort(key=lambda a: URGENCY_ORDER.get(a["urgensi"], 9))
    max_open = int(cfg.data["akun"]["max_open_positions"])
    if len(positions) > max_open:
        report["peringatan"].append(
            f"Posisi terbuka {len(positions)} melebihi batas {max_open}. Tidak akan ada "
            f"sinyal beli baru sampai jumlahnya turun — kurangi dulu, atau naikkan "
            f"`akun.max_open_positions` kalau memang disengaja."
        )
    from .db import get_meta
    balance_raw = get_meta(conn, "trading_balance")
    trading_balance = float(balance_raw) if balance_raw else None
    total_equity = round(trading_balance + total_value, 0) if trading_balance is not None else None
    report["ringkasan_porto"] = {
        "jumlah_posisi": len(positions),
        "maks_posisi": cfg.data["akun"]["max_open_positions"],
        "nilai_pasar": round(total_value, 0),
        "unrealized_pnl": round(total_pnl, 0),
        "exposure_pct": round(total_value / total_equity * 100, 1) if total_equity else 0.0,
        "tanpa_sl": [p["ticker"] for p in report["posisi"] if p.get("tanpa_sl")],
        "trading_balance": trading_balance,
        "total_equity": total_equity,
        "live_count": live_count,
        "total_posisi": len(positions),
    }

    # --- edge: apakah ada strategi yang layak dipakai? ----------------------
    # --- rekomendasi tambah posisi -------------------------------------------
    max_open = int(cfg.data["akun"]["max_open_positions"])
    slots_free = max_open - len(positions)
    report["slot_tersedia"] = slots_free
    report["saldo_tersedia"] = trading_balance
    if slots_free > 0 and trading_balance and trading_balance > 0:
        # Estimasi: pakai peringkat teratas sebagai kandidat
        try:
            peringkat, _ = screen.rank(conn, cfg, top=slots_free + len(positions))
            kandidat = []
            held_tickers = {p.ticker for p in positions}
            for row in peringkat:
                if row["ticker"] in held_tickers:
                    continue  # sudah punya
                entry = row.get("entry_price", 0)
                if entry and entry > 0:
                    lot_est = int(trading_balance / (entry * 100))
                    if lot_est >= 1:
                        kandidat.append({
                            "ticker": row["ticker"], "entry": entry,
                            "lot_est": lot_est, "skor": row.get("score", 0),
                            "label": row.get("label", ""),
                            "sl": row.get("stop_loss"), "tp1": row.get("tp1"),
                            "rr": row.get("rr_tp1", 0),
                        })
            report["rekomendasi_tambah"] = kandidat[:slots_free]
        except Exception:  # noqa: BLE001
            report["rekomendasi_tambah"] = []
    else:
        report["rekomendasi_tambah"] = []

    # --- saran reposisi SL/TP + trailing stop per posisi ----------------------
    reposisi = []
    for pos in positions:
        live = all_live.get(pos.ticker)
        last = live["price"] if live else None
        if not last or not pos.avg_price or pos.avg_price <= 0:
            continue
        pnl_pct = (last / pos.avg_price - 1) * 100
        sl = pos.stop_loss
        tp1 = getattr(pos, "tp1", None)
        saran_list = []

        # --- Trailing stop logic ---
        # Kapan trailing cocok:
        #   a) Profit > 5% → kunci profit, biar harga yang tentuin exit
        #   b) Dekat TP → jual sebagian, sisanya trailing
        #   c) SL udah di atas avg tapi jauh dari harga → trailing lebih efisien
        # Trail %: saham besar (BBCA/BBRI) 1-2%, mid/small 2-3%
        trail_pct = 1.5 if pos.avg_price >= 1000 else 2.5
        # Bulatkan ke fraksi harga yang valid — Stockbit menolak order yang
        # bukan kelipatan fraksi ("Fraksi harga harus dalam kelipatan 25").
        trail_stop = pricing.snap_price(last * (1 - trail_pct / 100), "floor")

        # 1) Profit > 5% tapi SL masih di bawah avg → trailing / breakeven
        if pnl_pct >= 5 and sl and sl < pos.avg_price:
            be_price = pricing.snap_price(pos.avg_price, "ceil")
            saran_list.append(
                f"🔒 Profit +{pnl_pct:.1f}% tapi SL {sl:,.0f} masih di bawah avg — "
                f"naikkan minimal ke breakeven {be_price:,.0f}"
            )
            if trail_stop > pos.avg_price:
                saran_list.append(
                    f"📈 Atau pakai *Trailing Stop*: trail {trail_pct:.1f}%, "
                    f"stop ~{trail_stop:,.0f}, {pos.lot} lot, GTC"
                )
        # 1b) Profit > 5%, SL udah di atas avg tapi jauh → trailing lebih ketat
        elif pnl_pct >= 5 and sl and sl >= pos.avg_price and last > 0:
            sl_dist = (last - sl) / last * 100
            if sl_dist > 3 and trail_stop > sl:
                saran_list.append(
                    f"📈 Profit +{pnl_pct:.1f}% — SL {sl:,.0f} terlalu jauh ({sl_dist:.1f}%). "
                    f"Ganti *Trailing Stop*: trail {trail_pct:.1f}%, stop ~{trail_stop:,.0f}, "
                    f"{pos.lot} lot, GTC"
                )
        # 2) Dekat TP1 (< 5%) → jual sebagian + trailing sisanya
        if tp1 and last > 0:
            tp1_dist = (tp1 - last) / last * 100
            if 0 < tp1_dist <= 5:
                half_lot = max(1, pos.lot // 2)
                sisa_lot = pos.lot - half_lot
                saran_list.append(
                    f"🎯 Tinggal {tp1_dist:.1f}% dari TP1 {tp1:,.0f} — "
                    f"jual {half_lot} lot di TP1, sisanya {sisa_lot} lot "
                    f"*Trailing Stop* trail {trail_pct:.1f}% stop ~{trail_stop:,.0f}"
                )
        # 3) Loss > 5% dan SL masih jauh (> 8%) → perketat
        if pnl_pct <= -5 and sl and last > 0:
            sl_dist = (last - sl) / last * 100
            if sl_dist > 8:
                saran_list.append(
                    f"⚠️ Loss {pnl_pct:.1f}% tapi SL masih {sl_dist:.1f}% jauh — "
                    f"pertimbangkan perketat SL atau cut loss sebagian"
                )
        # 4) Tanpa SL → wajib pasang
        if not sl:
            saran_list.append("🚨 TANPA SL — pasang segera, posisi tidak terproteksi")
        # 5) Tanpa TP → pasang target
        if not tp1 and pos.avg_price >= 50:
            saran_list.append("📌 Tanpa TP — pasang target profit biar ada rencana exit")

        if saran_list:
            reposisi.append({"ticker": pos.ticker, "saran": saran_list})
    report["saran_reposisi"] = reposisi

    # --- edge: apakah ada strategi yang layak dipakai? ----------------------
    known = screen.edges(conn)
    usable = []
    for name, node in known.items():
        exp = node.get("expectancy")
        pval = node.get("p_value")
        report["edge"][name] = {
            "expectancy": exp, "profit_factor": node.get("profit_factor"),
            "trade": node.get("total_trades"), "p_value": pval,
        }
        if exp is not None and exp > 0 and pval is not None and pval <= 0.05:
            usable.append(name)
    report["strategi_terbukti"] = usable

    if not known:
        report["peringatan"].append(
            "Belum ada hasil backtest. Jalankan `hermes-idx compare` untuk tahu apakah ada "
            "strategi yang punya edge sebelum memakai sinyal beli."
        )
    elif not usable:
        report["peringatan"].append(
            "TIDAK ADA strategi dengan expectancy positif yang signifikan pada data ini. "
            "Sinyal beli di bawah ini belum terbukti menguntungkan — perlakukan sebagai "
            "bahan pengamatan, bukan rekomendasi."
        )

    # --- sinyal beli --------------------------------------------------------
    try:
        signals, warns = screen.scan(conn, cfg, top=cfg.data["screening"]["max_results"])
        report["peringatan"].extend(w for w in warns if w not in report["peringatan"])
        report["sinyal"] = [s.as_row() for s in signals]
    except Exception as exc:  # noqa: BLE001 — laporan harus tetap terbit
        report["peringatan"].append(f"Screening gagal: {exc}")

    # --- peringkat: selalu ada isi, walau tidak ada yang layak dibeli ---------
    try:
        report["peringkat"], _ = screen.rank(conn, cfg, top=5)
    except Exception as exc:  # noqa: BLE001
        report["peringatan"].append(f"Peringkat gagal: {exc}")

    report["tidak_ada_sinyal_itu_normal"] = not report["sinyal"]
    return report


def render_text(report: dict, mode: str = "full") -> str:
    """Render jadi teks polos — cocok untuk WhatsApp/Telegram, tanpa tabel markdown.

    mode:
      'morning'   — porto + rekomendasi beli + position sizing (sebelum bursa)
      'afternoon' — review porto SAJA, tanpa rekomendasi beli (setelah bursa)
      'full'      — semua section (default / manual)
    """
    show_sinyal = mode in ("morning", "full")
    lines: list[str] = []

    # --- Header ---
    label = {"morning": "PAGI", "afternoon": "SORE", "full": ""}.get(mode, "")
    header = f"📊 *LAPORAN IDX {label}* — {report['tanggal']} {report['jam']}".strip()
    lines.append(header)
    lines.append("")

    # --- Pasar ---
    pasar = report.get("pasar") or {}
    if pasar.get("tersedia"):
        mark = "🟢 BULLISH" if pasar["bullish"] else "🔴 BEARISH"
        lines.append(f"*Pasar:* IHSG {pasar['ihsg']:,.0f} ({pasar['perubahan_pct']:+.2f}%) {mark}")
        lines.append(f"_{pasar['catatan']}_")
    if report.get("sesi"):
        lines.append(f"⏰ {report['sesi']['catatan']}")
    lines.append("")

    # --- Portofolio ---
    ring = report["ringkasan_porto"]
    live_tag = f" 📡{ring.get('live_count', 0)}/{ring.get('total_posisi', 0)} live" if ring.get("live_count") else ""
    lines.append(f"*💼 PORTOFOLIO* ({ring['jumlah_posisi']}/{ring['maks_posisi']} posisi){live_tag}")
    if ring.get("trading_balance") is not None:
        lines.append(f"Balance: Rp{ring['trading_balance']:,.0f} | Equity: Rp{ring.get('total_equity', 0):,.0f}")
    lines.append("")

    if not report["posisi"]:
        lines.append("_Belum ada posisi tercatat._")
    else:
        for pos in report["posisi"]:
            if pos.get("error"):
                flag = " ⚠️" if pos.get("tanpa_sl") else ""
                lines.append(f"• *{pos['ticker']}*{flag} — {pos['error']}")
                continue
            # Emoji status: 🟢 untung, 🔴 rugi
            icon = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
            flag = " ⚠️NO SL" if pos["tanpa_sl"] else ""
            lines.append(
                f"{icon} *{pos['ticker']}* {pos['lot']}lot | "
                f"avg {pos['avg_price']:,.0f} → {pos['last']:,.0f} | "
                f"P/L {pos['pnl_pct']:+.1f}%{flag}"
            )
            # Baris SL/TP dengan jarak
            parts = []
            if pos.get("stop_loss"):
                parts.append(f"SL {pos['stop_loss']:,.0f} (jarak {pos['sl_dist_pct']:.1f}%)")
            if pos.get("tp1"):
                parts.append(f"TP {pos['tp1']:,.0f} (jarak {pos['tp1_dist_pct']:.1f}%)")
            if pos.get("tp2"):
                parts.append(f"TP2 {pos['tp2']:,.0f} (jarak {pos['tp2_dist_pct']:.1f}%)")
            if parts:
                lines.append(f"   {' → '.join(parts)}")

        lines.append("")
        lines.append(f"📈 Unrealized: Rp{ring['unrealized_pnl']:+,.0f} | Exposure: {ring['exposure_pct']:.0f}%")
        if ring["tanpa_sl"]:
            lines.append(f"⚠️ Tanpa SL: {', '.join(ring['tanpa_sl'])}")
    lines.append("")

    # --- Rekomendasi Tambah Posisi ---
    if show_sinyal and report.get("rekomendasi_tambah"):
        slot = report.get("slot_tersedia", 0)
        saldo = report.get("saldo_tersedia", 0)
        lines.append(f"*💡 REKOMENDASI TAMBAH* (slot {slot} kosong | saldo Rp{saldo:,.0f})")
        for rec in report["rekomendasi_tambah"]:
            badge = BADGE.get(rec["label"], "⚪")
            sl_tp = ""
            if rec.get("sl"):
                sl_tp += f" | SL {rec['sl']:,.0f}"
            if rec.get("tp1"):
                sl_tp += f" | TP {rec['tp1']:,.0f}"
            lines.append(
                f"{badge} *{rec['ticker']}* skor {rec['skor']:.0f} · "
                f"entry ~{rec['entry']:,.0f} | maks {rec['lot_est']} lot{sl_tp}"
            )
        lines.append("_Bukan ajakan beli — cek dulu trigger & manajemen risiko._")
        lines.append("")

    # --- Saran Reposisi SL/TP ---
    if report.get("saran_reposisi"):
        lines.append("*📐 SARAN REPOSISI SL/TP*")
        for item in report["saran_reposisi"]:
            lines.append(f"• *{item['ticker']}*")
            for s in item["saran"]:
                lines.append(f"   {s}")
        lines.append("")

    # --- Aksi ---
    lines.append("*⚡ AKSI*")
    if not report["aksi"]:
        lines.append("_Tidak ada. Semua posisi sesuai rencana._")
    else:
        for act in report["aksi"]:
            harga = f" @ {act['harga_limit']:,.0f}" if act["harga_limit"] else ""
            lines.append(f"[{act['urgensi']}] *{act['ticker']}* — {act['aksi']}{harga}")
            lines.append(f"   _{act['alasan']}_")
    lines.append("")

    # --- Saran ubah rencana (SL/TP posisi berjalan) ---
    if report.get("saran_rencana"):
        lines.append("*🛠 SARAN UBAH SL/TP*")
        for saran in report["saran_rencana"]:
            bagian = []
            if "stop_loss_baru" in saran:
                lama = f"{saran['stop_loss_lama']:,.0f}" if saran.get("stop_loss_lama") else "—"
                bagian.append(f"SL {lama} → *{saran['stop_loss_baru']:,.0f}*")
            if "tp1_baru" in saran:
                lama = f"{saran['tp1_lama']:,.0f}" if saran.get("tp1_lama") else "—"
                bagian.append(f"TP1 {lama} → *{saran['tp1_baru']:,.0f}*")
            lines.append(f"• *{saran['ticker']}* — {' | '.join(bagian)}")
            for alasan in saran["alasan"]:
                lines.append(f"   _{alasan}_")
            lines.append(f"   `{saran['perintah']}`")
        lines.append("")

    # --- Sinyal Beli (pagi & full saja; sore = review tanpa rekomendasi) ---
    if show_sinyal:
        lines.append("*🔍 SINYAL BELI*")
        if not report["sinyal"]:
            lines.append("_Tidak ada. Sistem menahan diri — tidak ada setup memenuhi syarat._")
        else:
            if not report.get("strategi_terbukti"):
                lines.append("_(Belum terbukti untung — pengamatan, bukan rekomendasi)_")
            for sig in report["sinyal"]:
                lines.append(
                    f"• *{sig['ticker']}* skor {sig['score']:.0f} | "
                    f"Entry {sig['entry_price']:,} | SL {sig['stop_loss']:,} | "
                    f"TP {sig['tp1']:,} | {sig['position_lot']} lot"
                )
                lines.append(f"   _{sig['notes']}_")
        lines.append("")

        # Peringkat selalu tampil — inilah "tiap pagi ada isinya". Bedanya dengan
        # SINYAL BELI di atas: ini urutan kesiapan, bukan daftar yang boleh dibeli.
        if report.get("peringkat"):
            lines.append("*📋 PERINGKAT HARI INI*")
            for n, row in enumerate(report["peringkat"], 1):
                lines.append(
                    f"{n}. {BADGE.get(row['label'], '')} *{row['ticker']}* "
                    f"skor {row['score']:.0f} · {row['label']}"
                )
                lines.append(
                    f"   Entry {row['entry_price']:,} | SL {row['stop_loss']:,} | "
                    f"TP {row['tp1']:,} | {row['position_lot']} lot"
                )
                lines.append(f"   _{row['alasan']}_")
            lines.append("_Peringkat = urutan kesiapan, BUKAN ajakan beli._")
            lines.append("")

    # --- Edge / Backtest terbaru ---
    edge = report.get("edge", {})
    if edge:
        lines.append("*🔬 BACKTEST EDGE*")
        for name, info in edge.items():
            exp = info.get("expectancy")
            pf = info.get("profit_factor")
            trades = info.get("trade", 0)
            pval = info.get("p_value")
            if exp is None:
                continue
            icon = "✅" if exp > 0 and pval is not None and pval <= 0.05 else "❌"
            lines.append(
                f"{icon} {name}: exp {exp:+.3f}R | PF {pf:.2f} | "
                f"{trades} trade | p={pval}"
            )
        proven = report.get("strategi_terbukti", [])
        if proven:
            lines.append(f"🟢 Edge terbukti: {', '.join(proven)}")
        else:
            lines.append("🔴 Belum ada strategi terbukti → sinyal beli = pengamatan saja")
        lines.append("")

    # --- Peringatan (di bawah, bukan di atas — biar nggak nutupin info utama) ---
    if report["peringatan"]:
        lines.append("*⚠️ CATATAN*")
        for warning in report["peringatan"]:
            lines.append(f"• {warning}")
        lines.append("")

    lines.append("_Alat bantu analisis teknikal, bukan nasihat investasi._")
    return "\n".join(lines)
