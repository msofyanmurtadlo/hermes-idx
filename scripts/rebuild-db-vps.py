#!/usr/bin/env python3
"""Rebuild DB porto VPS dari snapshot Stockbit terakhir (8 Agustus 2026, 02.35)."""
import sqlite3, os, datetime as dt

DB = os.path.expanduser("~/.hermes-idx/hermes.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
today = dt.date.today().isoformat()

# --- Posisi per Stockbit 8 Agustus 2026 (screenshot detail per-ticker) ---
positions = [
    # ticker, lot, avg, SL, TP1, TP2, catatan
    ("BBCA", 12,  6605.72, 5975, 6850, None),
    ("BMTR", 35,   122.58,  108,  144, None),
    ("BUMI", 70,   173.26,  152,  190, None),
    ("INDF", 1,   6960.42, 6600, 7625, None),
    ("WIDI", 0.07,  15.84, None, None, None),   # tanpa SL/TP
]

for t, lot, avg, sl, tp1, tp2 in positions:
    conn.execute("""INSERT INTO posisi (ticker, lot, avg_price, stop_loss, tp1, tp2, entry_date)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                    lot=excluded.lot, avg_price=excluded.avg_price,
                    stop_loss=excluded.stop_loss, tp1=excluded.tp1, tp2=excluded.tp2""",
                 (t, lot, avg, sl, tp1, tp2, today))

# --- Trade closed (history) ---
closed = [
    ("DSSA", 835, 865, 15, 45000, 3.59, "TP"),
    ("BBRI", 2734.09, 2990, 10, 248435, 9.4, "trailing stop"),
]
for t, ep, xp, lot, pnl, pct, reason in closed:
    exists = conn.execute("SELECT 1 FROM trade_closed WHERE ticker=? AND exit_reason=? LIMIT 1",
                          (t, reason)).fetchone()
    if not exists:
        conn.execute("""INSERT INTO trade_closed (ticker, entry_price, exit_price, lot,
                        pnl_rp, pnl_pct, exit_reason, exit_date)
                        VALUES (?,?,?,?,?,?,?,?)""",
                     (t, ep, xp, lot, pnl, pct, reason, today))

# --- Balance & metadata ---
def set_meta(k, v):
    conn.execute("""INSERT INTO meta (key, value) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, str(v)))

set_meta("trading_balance", 4897533)
set_meta("equity_snapshot", 15011215)
set_meta("snapshot_date", "2026-08-08")
set_meta("snapshot_source", "Stockbit user 8 Agu 02:35 — screenshot detail per-ticker")

conn.commit()

# Verifikasi
print("=== POSISI ===")
for r in conn.execute("SELECT * FROM posisi ORDER BY ticker"):
    print(f"{r['ticker']:6} {r['lot']:>3} lot avg {r['avg_price']:>9,.2f} SL {r['stop_loss']} TP {r['tp1']}")
print("\n=== TRADE CLOSED ===")
for r in conn.execute("SELECT ticker, pnl_rp, pnl_pct, exit_reason FROM trade_closed"):
    print(f"{r['ticker']:6} +Rp{r['pnl_rp']:,.0f} (+{r['pnl_pct']}%) via {r['exit_reason']}")
print("\nBalance:", conn.execute("SELECT value FROM meta WHERE key='trading_balance'").fetchone()[0])
conn.close()
