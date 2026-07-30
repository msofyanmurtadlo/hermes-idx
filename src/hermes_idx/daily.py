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

from . import data as datamod, portfolio, screen, universe

URGENCY_ORDER = {"SEGERA": 0, "HARI INI": 1, "AMATI": 2}


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
        "sinyal": [],
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
    if benchmark.empty:
        report["pasar"] = {"tersedia": False}
        report["peringatan"].append(
            "Data IHSG tidak ada — rezim pasar tidak bisa dinilai, semua sinyal beli "
            "ditahan (fail-closed)."
        )
    else:
        last = float(benchmark["close"].iloc[-1])
        prev = float(benchmark["close"].iloc[-2]) if len(benchmark) > 1 else last
        bullish = bool(ctx.bullish_regime.iloc[-1]) if ctx.bullish_regime is not None else False
        report["pasar"] = {
            "tersedia": True,
            "ihsg": round(last, 2),
            "perubahan_pct": round((last / prev - 1) * 100, 2) if prev else 0.0,
            "bullish": bullish,
            "catatan": "IHSG di atas MA200" if bullish else
                       "IHSG di bawah MA200 — strategi long-only ditahan",
        }

    # --- posisi & aksi (paling mendesak duluan) -----------------------------
    positions = portfolio.positions(conn)
    total_pnl = 0.0
    total_value = 0.0
    for pos in positions:
        frame = datamod.load_ohlcv(conn, pos.ticker)
        if frame.empty:
            # Tetap catat `tanpa_sl`: posisi tanpa stop loss berbahaya terlepas dari
            # ada atau tidaknya data harga — justru lebih berbahaya, karena tidak ada
            # yang memantaunya.
            report["posisi"].append({
                "ticker": pos.ticker, "lot": pos.lot, "avg_price": pos.avg_price,
                "stop_loss": pos.stop_loss, "tanpa_sl": pos.stop_loss is None,
                "sektor": universe.sector_of(pos.ticker),
                "error": "tidak ada data harga — jalankan `data update`",
            })
            continue
        action = portfolio.review(pos, frame, cfg, now.date())
        last = float(frame["close"].iloc[-1])
        total_pnl += action.pnl_rp
        total_value += last * pos.lot * 100
        # Jarak ke SL/TP — info paling penting untuk monitoring harian.
        sl = pos.stop_loss
        tp1 = getattr(pos, "tp1", None)
        tp2 = getattr(pos, "tp2", None)
        sl_dist = round((last - sl) / last * 100, 1) if sl else None
        tp1_dist = round((tp1 - last) / last * 100, 1) if tp1 else None
        tp2_dist = round((tp2 - last) / last * 100, 1) if tp2 else None
        report["posisi"].append({
            "ticker": pos.ticker, "lot": pos.lot, "avg_price": pos.avg_price,
            "last": last, "pnl_rp": action.pnl_rp, "pnl_pct": action.pnl_pct,
            "stop_loss": sl, "tp1": tp1, "tp2": tp2,
            "sl_dist_pct": sl_dist, "tp1_dist_pct": tp1_dist, "tp2_dist_pct": tp2_dist,
            "tanpa_sl": sl is None,
            "sektor": universe.sector_of(pos.ticker),
        })
        if action.action != "HOLD":
            report["aksi"].append({
                "ticker": action.ticker, "aksi": action.action, "urgensi": action.urgency,
                "alasan": action.reason, "harga_limit": action.limit_price,
                "lot": action.lot, "estimasi_terima": action.est_proceeds,
            })

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
        "exposure_pct": round(total_value / cfg.modal * 100, 1) if cfg.modal else 0.0,
        "tanpa_sl": [p["ticker"] for p in report["posisi"] if p.get("tanpa_sl")],
        "trading_balance": trading_balance,
        "total_equity": total_equity,
    }

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
    lines.append(f"*💼 PORTOFOLIO* ({ring['jumlah_posisi']}/{ring['maks_posisi']} posisi)")
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

    # --- Peringatan (di bawah, bukan di atas — biar nggak nutupin info utama) ---
    if report["peringatan"]:
        lines.append("*⚠️ CATATAN*")
        for warning in report["peringatan"]:
            lines.append(f"• {warning}")
        lines.append("")

    lines.append("_Alat bantu analisis teknikal, bukan nasihat investasi._")
    return "\n".join(lines)
