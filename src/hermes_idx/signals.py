"""Signal Builder: ubah sinyal mentah jadi rekomendasi actionable (PRD §6.4).

Pembulatan harga (koreksi atas SB-2, lihat REVIEW-01 S2): PRD menyebut "SL dibulatkan ke
bawah karena konservatif". Memberi ruang lebih pada SL bukan tindakan konservatif — itu
memperbesar kerugian per lembar. Kebijakan di sini dipertahankan sesuai maksud PRD (SL
diberi ruang) tapi diberi nama yang benar, dan position sizing memakai SL SETELAH
pembulatan sehingga nominal risiko yang ditampilkan selalu tepat, bukan perkiraan.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from . import market, strategies as strat

TICK_POLICY = {
    "entry_limit": "down",   # bid lebih rendah: lebih murah, risiko tidak terisi
    "entry_stop": "up",      # trigger lebih tinggi: butuh konfirmasi lebih
    "stop_loss": "down",     # menjauh dari entry: memberi ruang (bukan 'konservatif')
    "take_profit": "down",   # mendekat ke entry: target lebih mudah tercapai
}


@dataclass
class Signal:
    ticker: str
    signal_date: dt.date
    strategy: str
    action: str
    entry_type: str
    entry_price: int
    entry_zone_low: int
    entry_zone_high: int
    stop_loss: int
    sl_pct: float
    tp1: int
    tp2: int
    tp1_size: float
    tp2_size: float
    rr_tp1: float
    rr_tp2: float
    position_lot: int
    risk_rp: float
    score: float
    valid_until: dt.date
    notes: str
    skip_reason: str | None = None

    def as_row(self) -> dict:
        row = asdict(self)
        row["signal_date"] = self.signal_date.isoformat()
        row["valid_until"] = self.valid_until.isoformat()
        return row


# --------------------------------------------------------------------------- sizing

def position_size(
    entry: int, stop: int, cfg, avg_daily_volume_lots: float | None = None
) -> tuple[int, str | None]:
    """SB-3. Kembalikan (lot, alasan_skip)."""
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0, "stop loss tidak di bawah entry"

    budget = cfg.modal * cfg.risk_pct
    lots = math.floor(budget / (risk_per_share * market.LOT))

    max_by_concentration = math.floor(
        cfg.modal * cfg.max_position_pct / (entry * market.LOT)
    )
    lots = min(lots, max_by_concentration)

    if avg_daily_volume_lots and avg_daily_volume_lots > 0:
        lots = min(lots, math.floor(avg_daily_volume_lots * 0.05))

    if lots < 1:
        return 0, "SKIP: modal tidak cukup untuk risk management yang benar"
    return lots, None


# --------------------------------------------------------------------------- skor

def _norm(value: float, low: float, high: float) -> float:
    if not np.isfinite(value) or high <= low:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def score_signal(
    edge: dict | None, setup: float, liquidity: float, regime: float, rr: float
) -> float:
    """SB-5. `edge` = ringkasan backtest strategi ini, atau None bila belum ada.

    Bila belum ada hasil backtest, komponen edge diberi 0.5 (netral) — BUKAN nilai tinggi.
    Sistem tidak boleh memberi skor tinggi hanya karena belum pernah diuji.
    """
    if edge:
        edge_score = 0.6 * _norm(edge.get("expectancy", 0.0), -0.2, 0.8) + 0.4 * _norm(
            edge.get("profit_factor", 1.0), 0.8, 2.5
        )
    else:
        edge_score = 0.5
    total = (
        0.35 * edge_score
        + 0.25 * np.clip(setup, 0, 1)
        + 0.15 * np.clip(liquidity, 0, 1)
        + 0.15 * np.clip(regime, 0, 1)
        + 0.10 * _norm(rr, 1.0, 4.0)
    )
    return round(float(total) * 100, 1)


def _setup_quality(df: pd.DataFrame, i: int) -> float:
    row = df.iloc[i]
    points, total = 0.0, 0.0
    for key, ok in (
        ("vol_ma20", row.get("volume", 0) > (row.get("vol_ma20") or np.inf)),
        ("ema50", row.get("close", 0) > (row.get("ema50") or np.inf)),
        ("ema200", row.get("close", 0) > (row.get("ema200") or np.inf)),
    ):
        if key in df.columns:
            total += 1
            points += 1.0 if bool(ok) else 0.0
    rsi = row.get("rsi14", np.nan)
    if np.isfinite(rsi):
        total += 1
        points += 1.0 if 45 <= rsi <= 72 else 0.3
    return points / total if total else 0.5


def _liquidity_score(avg_value: float, minimum: float) -> float:
    """Skor likuiditas pada skala log — beda Rp2 M vs Rp4 M jauh lebih berarti
    daripada Rp200 M vs Rp202 M.

    `minimum` dijaga > 0: mematikan filter likuiditas (`min_avg_value_20d: 0`) adalah
    config yang sah, tapi dulu membuat `log10(0)` melempar ValueError dan menjatuhkan
    seluruh screening.
    """
    if not np.isfinite(avg_value) or avg_value <= 0:
        return 0.0
    floor = max(float(minimum), 1.0)
    return _norm(math.log10(avg_value), math.log10(floor), math.log10(floor * 50))


# --------------------------------------------------------------------------- builder

def add_rs_rank(panel: dict[str, pd.DataFrame]) -> None:
    """Isi kolom `rs_rank` (persentil lintas emiten per tanggal), in-place.

    Ini perhitungan cross-sectional — tidak bisa dilakukan per-ticker sendiri-sendiri.
    """
    if not panel:
        return
    rs = pd.DataFrame({t: d["rs"] for t, d in panel.items() if "rs" in d.columns})
    if rs.empty:
        return
    ranks = rs.rank(axis=1, pct=True) * 100
    for ticker, frame in panel.items():
        if ticker in ranks.columns:
            frame["rs_rank"] = ranks[ticker].reindex(frame.index)
        else:
            frame["rs_rank"] = np.nan


def build(
    ticker: str,
    df: pd.DataFrame,
    strategy: strat.Strategy,
    i: int,
    cfg,
    edge: dict | None = None,
    avg_value: float = 0.0,
) -> Signal | None:
    """Bangun satu rekomendasi beli dari bar `i`. None bila level tidak masuk akal."""
    date = df.index[i].date()
    close = float(df["close"].iloc[i])
    levels = strategy.levels(df, i, close)

    entry_mode = TICK_POLICY["entry_stop" if levels.entry_type == "buy_stop" else "entry_limit"]
    entry = market.round_to_tick(close, date, entry_mode)
    stop = market.round_to_tick(levels.stop_loss, date, TICK_POLICY["stop_loss"])
    # Aturan user 14 Agu 2026 ("SL maksimal 2%") — rounding "down" bisa menambah 1 tick lagi,
    # jadi clamp ulang ke level 2% yang di-round UP (mendekati entry) supaya jarak tidak tembus.
    stop = max(stop, market.round_to_tick(entry * (1 - strat.MAX_SL_PCT), date, "up"))
    if stop >= entry:
        return None

    risk = entry - stop
    # Level FIX (aturan user 14 Agu 2026): TP dihitung dari RISK FINAL supaya R:R persis
    # 1:2 (TP1 = 2×risk) dan 1:4 (TP2 = 4×risk) — bukan dari levels.tp1 yang bisa melenceng
    # akibat pembulatan fraksi harga terpisah.
    tp1 = market.round_to_tick(entry + 2 * risk, date, TICK_POLICY["take_profit"])
    tp2 = market.round_to_tick(entry + 4 * risk, date, TICK_POLICY["take_profit"])
    if tp1 <= entry or tp2 <= tp1:
        return None

    avg_vol_lots = float(df["volume"].iloc[max(0, i - 19): i + 1].mean() or 0) / market.LOT
    lots, skip = position_size(entry, stop, cfg, avg_vol_lots)

    regime = 1.0 if bool(df["bullish"].iloc[i]) else 0.25
    rr1 = (tp1 - entry) / risk
    score = score_signal(
        edge=edge,
        setup=_setup_quality(df, i),
        liquidity=_liquidity_score(avg_value, cfg.data["universe"]["min_avg_value_20d"]),
        regime=regime,
        rr=rr1,
    )

    zone_tick = market.tick_size(entry, date)
    return Signal(
        ticker=ticker,
        signal_date=date,
        strategy=strategy.name,
        action="BUY",
        entry_type=levels.entry_type,
        entry_price=entry,
        entry_zone_low=market.round_to_tick(entry - 2 * zone_tick, date, "down"),
        entry_zone_high=market.round_to_tick(entry + 2 * zone_tick, date, "up"),
        stop_loss=stop,
        sl_pct=round(-risk / entry * 100, 2),
        tp1=tp1,
        tp2=tp2,
        tp1_size=levels.tp1_size,
        tp2_size=levels.tp2_size,
        rr_tp1=round(rr1, 2),
        rr_tp2=round((tp2 - entry) / risk, 2),
        position_lot=lots,
        risk_rp=round(lots * market.LOT * risk, 0),
        score=score,
        valid_until=date + dt.timedelta(days=cfg.data["screening"]["valid_days"]),
        notes=strategy.reason(df, i),
        skip_reason=skip,
    )


def persist(conn, signals: list[Signal]) -> int:
    rows = [
        (
            s.ticker, s.signal_date.isoformat(), s.strategy, s.action, s.entry_type,
            s.entry_price, s.entry_zone_low, s.entry_zone_high, s.stop_loss, s.sl_pct,
            s.tp1, s.tp2, s.tp1_size, s.tp2_size, s.rr_tp1, s.rr_tp2,
            s.position_lot, s.risk_rp, s.score, s.valid_until.isoformat(), s.notes,
        )
        for s in signals
    ]
    conn.executemany(
        "INSERT INTO signal (ticker, signal_date, strategy, action, entry_type, entry_price,"
        " entry_zone_low, entry_zone_high, stop_loss, sl_pct, tp1, tp2, tp1_size, tp2_size,"
        " rr_tp1, rr_tp2, position_lot, risk_rp, score, valid_until, notes)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(ticker, signal_date, strategy) DO UPDATE SET score=excluded.score,"
        " entry_price=excluded.entry_price, stop_loss=excluded.stop_loss,"
        " tp1=excluded.tp1, tp2=excluded.tp2, notes=excluded.notes",
        rows,
    )
    conn.commit()
    return len(rows)
