"""Impor data dari script Hermes lama (`idx-bluechip-report.py` v3).

Script lama menyimpan state di dua file JSON yang ditimpa setiap run:
- `portfolio.json`      — posisi + trailing stop yang sudah dinaikkan
- `idx-signal-history.json` — sinyal run terakhir + akumulasi akurasi BELI/JUAL

Riwayat itu tidak boleh hilang saat pindah ke hermes-idx. Modul ini memindahkannya ke
SQLite, sekaligus **menandai batas kepercayaannya**: akurasi versi lama diukur sebagai
arah harga keesokan hari, bukan hasil trade sungguhan (tidak memperhitungkan SL kena,
tidak memperhitungkan biaya). Angka itu tidak sebanding dengan expectancy hermes-idx,
jadi disimpan sebagai catatan, bukan dilebur ke statistik.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import market


def import_portfolio(conn, path: Path, fees: market.Fees) -> tuple[int, list[str]]:
    """Pindahkan `portfolio.json` ke tabel `posisi`. Kembalikan (jumlah, catatan)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    notes: list[str] = []
    count = 0

    snapshot_date = data.get("snapshot_date")
    entry_date = snapshot_date if snapshot_date else dt.date.today().isoformat()

    for pos in data.get("positions", []):
        ticker = str(pos.get("ticker", "")).upper()
        lots = int(pos.get("lots") or 0)
        avg = float(pos.get("avg") or 0)
        if not ticker or lots <= 0 or avg <= 0:
            notes.append(f"{ticker or '?'}: dilewati — lot/avg tidak valid")
            continue

        invested = pos.get("invested")
        if invested:
            implied = float(invested) / (lots * market.LOT)
            if abs(implied - avg) / avg > 0.02:
                notes.append(
                    f"{ticker}: avg {avg:,.2f} tidak konsisten dengan invested "
                    f"Rp{float(invested):,.0f} (implied {implied:,.2f}) — memakai avg"
                )

        conn.execute(
            "INSERT INTO posisi (ticker, lot, avg_price, stop_loss, tp1, entry_date, strategy)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(ticker) DO UPDATE SET lot=excluded.lot, avg_price=excluded.avg_price,"
            " stop_loss=COALESCE(excluded.stop_loss, posisi.stop_loss),"
            " tp1=COALESCE(excluded.tp1, posisi.tp1)",
            (ticker, lots, avg, pos.get("sl") or None, pos.get("tp") or None,
             entry_date, "legacy_v3"),
        )
        count += 1
        if not pos.get("sl"):
            notes.append(f"{ticker}: TANPA STOP LOSS — tetapkan dengan `port plan`")
        if not snapshot_date:
            notes.append(f"{ticker}: entry_date tidak diketahui, dipakai hari ini "
                         f"— time stop akan salah hitung")

    conn.commit()
    return count, notes


def import_signal_history(conn, path: Path) -> dict:
    """Simpan akurasi versi lama sebagai catatan, bukan sebagai statistik hermes-idx."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    stats = data.get("stats", {})
    summary = {}
    for action in ("BELI", "JUAL"):
        node = stats.get(action) or {}
        right, wrong = int(node.get("benar", 0)), int(node.get("salah", 0))
        total = right + wrong
        summary[action] = {
            "benar": right, "salah": wrong, "total": total,
            "akurasi_pct": round(right / total * 100, 1) if total else None,
        }

    from .db import set_meta

    set_meta(conn, "legacy_signal_stats", json.dumps(summary, ensure_ascii=False))
    set_meta(conn, "legacy_signal_stats_caveat",
             "Akurasi ini diukur sebagai arah harga keesokan hari, bukan hasil trade. "
             "Tidak memperhitungkan stop loss yang kena maupun biaya transaksi, jadi "
             "cenderung lebih optimis daripada expectancy. Tidak sebanding dengan metrik "
             "backtest hermes-idx.")
    set_meta(conn, "legacy_last_signal_date",
             str((data.get("last_signals") or {}).get("date", "")))
    return summary


def legacy_paths(home: Path | None = None) -> dict[str, Path]:
    base = Path(home) if home else Path.home() / ".hermes" / "scripts"
    return {
        "portfolio": base / "portfolio.json",
        "signal_history": base / "idx-signal-history.json",
    }
