"""Portfolio tracker & rekomendasi jual (PRD §6.4.2, §6.6.4)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind, market

URGENCY = {"CUT_LOSS": "SEGERA", "TAKE_PROFIT_1": "HARI INI", "TAKE_PROFIT_2": "HARI INI",
           "EXIT_SIGNAL": "HARI INI", "TIME_STOP": "AMATI", "DETERIORATION": "AMATI",
           "TRAILING_STOP": "AMATI", "HOLD": "AMATI"}


@dataclass
class Position:
    ticker: str
    lot: int
    avg_price: float
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    entry_date: dt.date | None = None
    strategy: str | None = None
    breakeven_moved: bool = False


@dataclass
class Action:
    ticker: str
    action: str
    urgency: str
    reason: str
    limit_price: int | None
    lot: int
    pnl_rp: float
    pnl_pct: float
    est_proceeds: float


def record(conn, ticker: str, date: dt.date, kind: str, lot: int, price: float,
           fees, signal_id: int | None = None, source: str = "manual",
           notes: str | None = None) -> None:
    """PT-1/PT-2. Weighted average, konsisten dengan tampilan Stockbit."""
    if lot <= 0:
        raise ValueError("lot harus > 0")
    if price <= 0:
        raise ValueError("harga harus > 0")
    kind = kind.upper()
    if kind not in ("BUY", "SELL"):
        raise ValueError("type harus BUY atau SELL")

    fee = fees.buy_cost(price, lot) if kind == "BUY" else fees.sell_cost(price, lot)
    conn.execute(
        "INSERT OR IGNORE INTO transaksi (ticker, date, type, lot, price, fee, signal_id,"
        " source, notes) VALUES (?,?,?,?,?,?,?,?,?)",
        (ticker, date.isoformat(), kind, lot, price, fee, signal_id, source, notes),
    )

    row = conn.execute("SELECT * FROM posisi WHERE ticker = ?", (ticker,)).fetchone()
    if kind == "BUY":
        if row:
            total_lot = row["lot"] + lot
            avg = (row["lot"] * row["avg_price"] + lot * price) / total_lot
            conn.execute("UPDATE posisi SET lot = ?, avg_price = ? WHERE ticker = ?",
                         (total_lot, avg, ticker))
        else:
            conn.execute(
                "INSERT INTO posisi (ticker, lot, avg_price, entry_date) VALUES (?,?,?,?)",
                (ticker, lot, price, date.isoformat()),
            )
    else:
        if not row:
            raise ValueError(f"tidak ada posisi {ticker} untuk dijual")
        if lot > row["lot"]:
            raise ValueError(f"jual {lot} lot tapi posisi hanya {row['lot']} lot")
        entry_date = dt.date.fromisoformat(row["entry_date"]) if row["entry_date"] else None
        gross_in = market.net_buy_value(row["avg_price"], lot, fees)
        gross_out = market.net_sell_value(price, lot, fees)
        pnl = gross_out - gross_in
        risk = (row["avg_price"] - row["stop_loss"]) if row["stop_loss"] else None
        conn.execute(
            "INSERT INTO trade_closed (ticker, entry_date, exit_date, entry_price, exit_price,"
            " lot, pnl_rp, pnl_pct, r_multiple, strategy, exit_reason, sl_respected,"
            " data_complete) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, entry_date.isoformat() if entry_date else None, date.isoformat(),
             row["avg_price"], price, lot, round(pnl, 0), round(pnl / gross_in * 100, 2),
             round(pnl / (risk * lot * market.LOT), 3) if risk and risk > 0 else None,
             row["strategy"], notes or "manual",
             1 if (row["stop_loss"] is None or price >= row["stop_loss"]) else 0,
             1 if entry_date and row["stop_loss"] else 0),
        )
        remaining = row["lot"] - lot
        if remaining == 0:
            conn.execute("DELETE FROM posisi WHERE ticker = ?", (ticker,))
        else:
            conn.execute("UPDATE posisi SET lot = ? WHERE ticker = ?", (remaining, ticker))
    conn.commit()


def positions(conn) -> list[Position]:
    rows = conn.execute("SELECT * FROM posisi ORDER BY ticker").fetchall()
    return [
        Position(
            ticker=r["ticker"], lot=r["lot"], avg_price=r["avg_price"],
            stop_loss=r["stop_loss"], tp1=r["tp1"], tp2=r["tp2"],
            entry_date=dt.date.fromisoformat(r["entry_date"]) if r["entry_date"] else None,
            strategy=r["strategy"], breakeven_moved=bool(r["breakeven_moved"]),
        )
        for r in rows
    ]


def set_plan(conn, ticker: str, stop_loss: float | None = None, tp1: float | None = None,
             tp2: float | None = None, strategy: str | None = None) -> None:
    """PT-10: lengkapi rencana untuk posisi yang belum punya SL/TP."""
    updates, params = [], []
    for column, value in (("stop_loss", stop_loss), ("tp1", tp1), ("tp2", tp2),
                          ("strategy", strategy)):
        if value is not None:
            updates.append(f"{column} = ?")
            params.append(value)
    if not updates:
        return
    params.append(ticker)
    conn.execute(f"UPDATE posisi SET {', '.join(updates)} WHERE ticker = ?", params)
    conn.commit()


def review(pos: Position, df: pd.DataFrame, cfg, today: dt.date | None = None) -> Action:
    """SB-6/SB-7: satu aksi per posisi, dengan alasan, urgensi, dan estimasi P/L."""
    today = today or dt.date.today()
    last = float(df["close"].iloc[-1])
    date = df.index[-1].date()
    fees = cfg.fees
    exit_cfg = cfg.data["exit"]

    pnl = market.net_sell_value(last, pos.lot, fees) - market.net_buy_value(
        pos.avg_price, pos.lot, fees)
    pnl_pct = pnl / market.net_buy_value(pos.avg_price, pos.lot, fees) * 100
    risk = (pos.avg_price - pos.stop_loss) if pos.stop_loss else None
    r_now = (last - pos.avg_price) / risk if risk and risk > 0 else None
    held = (today - pos.entry_date).days if pos.entry_date else None

    action, reason = "HOLD", "Belum ada trigger, struktur masih intact."

    if pos.stop_loss is None:
        action = "HOLD"
        reason = "⚠ TANPA STOP LOSS — tetapkan dulu dengan `port plan`."
    elif last <= pos.stop_loss:
        action, reason = "CUT_LOSS", f"Close {last:,.0f} di bawah SL {pos.stop_loss:,.0f}."
    elif pos.tp2 and last >= pos.tp2:
        action, reason = "TAKE_PROFIT_2", f"TP2 {pos.tp2:,.0f} tersentuh — jual sisa."
    elif pos.tp1 and last >= pos.tp1 and not pos.breakeven_moved:
        action, reason = "TAKE_PROFIT_1", f"TP1 {pos.tp1:,.0f} tersentuh — geser SL ke breakeven."
    elif r_now is not None and r_now >= exit_cfg["trailing_activate_at_r"]:
        trail = float(ind.chandelier_exit(df, 22, 3.0).iloc[-1])
        if np.isfinite(trail) and last <= trail:
            action, reason = "EXIT_SIGNAL", f"Harga menembus trailing stop {trail:,.0f}."
        else:
            action = "TRAILING_STOP"
            reason = (f"Sudah +{r_now:.1f}R. Aktifkan trailing di "
                      f"{trail:,.0f}." if np.isfinite(trail) else f"Sudah +{r_now:.1f}R.")
    elif held is not None and held > exit_cfg["time_stop_days"] and (r_now is None or r_now < 1):
        action = "TIME_STOP"
        reason = f"{held} hari, belum mencapai 1R — modal tidak produktif."
    elif _deteriorating(df):
        action, reason = "DETERIORATION", "Lower-low terbentuk dan volume distribusi."
    elif pos.stop_loss and (last - pos.stop_loss) / last * 100 < exit_cfg["alert_near_sl_pct"]:
        reason = f"⚠ Hanya {(last - pos.stop_loss) / last * 100:.1f}% di atas SL."

    lot_to_sell = pos.lot
    if action == "TAKE_PROFIT_1":
        lot_to_sell = max(pos.lot // 2, 1)

    return Action(
        ticker=pos.ticker,
        action=action,
        urgency=URGENCY.get(action, "AMATI"),
        reason=reason,
        limit_price=market.round_to_tick(last, date, "down") if action != "HOLD" else None,
        lot=lot_to_sell if action != "HOLD" else 0,
        pnl_rp=round(pnl, 0),
        pnl_pct=round(pnl_pct, 2),
        est_proceeds=round(market.net_sell_value(last, lot_to_sell, fees), 0),
    )


def _deteriorating(df: pd.DataFrame, lookback: int = 10) -> bool:
    if len(df) < lookback + 5:
        return False
    recent = df.iloc[-lookback:]
    lower_low = recent["low"].iloc[-1] < recent["low"].iloc[: lookback // 2].min()
    vol_up = recent["volume"].iloc[-3:].mean() > recent["volume"].mean() * 1.3
    down_days = (recent["close"].diff() < 0).sum() > lookback * 0.6
    return bool(lower_low and vol_up and down_days)


def stats(conn) -> dict:
    """PT-5 + PT-11: statistik personal DENGAN cakupan data eksplisit."""
    rows = conn.execute("SELECT * FROM trade_closed").fetchall()
    if not rows:
        return {"total_trades": 0, "coverage": "belum ada trade tertutup"}
    frame = pd.DataFrame([dict(r) for r in rows])
    complete = frame[frame["data_complete"] == 1]
    with_r = complete["r_multiple"].dropna()
    wins = (frame["pnl_rp"] > 0).sum()
    return {
        "total_trades": len(frame),
        "win_rate": round(wins / len(frame) * 100, 1),
        "realized_pnl_rp": round(float(frame["pnl_rp"].sum()), 0),
        "expectancy_r": round(float(with_r.mean()), 3) if len(with_r) else None,
        "coverage": (
            f"Expectancy berdasarkan {len(with_r)} dari {len(frame)} trade — "
            f"{len(frame) - len(with_r)} trade tidak punya data entry/SL lengkap."
        ),
    }
