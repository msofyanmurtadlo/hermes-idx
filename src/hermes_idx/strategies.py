"""Strategi bawaan S1–S4 (PRD §6.3).

Kontrak penting: `entry_signal` pada bar T hanya boleh memakai informasi yang tersedia
pada close T. Tidak ada `.shift(-n)` di jalur sinyal. Fungsi struktur yang butuh bar kanan
(`swing_low`) dipakai lewat `last_swing_low()` yang sudah di-shift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Levels:
    stop_loss: float
    tp1: float
    tp2: float
    tp1_size: float = 0.5
    tp2_size: float = 0.5
    entry_type: str = "limit"


class Strategy(Protocol):
    name: str
    label: str
    long_only: bool

    def prepare(self, df: pd.DataFrame, ctx: "MarketContext") -> pd.DataFrame: ...
    def entry_signal(self, df: pd.DataFrame) -> pd.Series: ...
    def exit_signal(self, df: pd.DataFrame) -> pd.Series: ...
    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels: ...
    def reason(self, df: pd.DataFrame, i: int) -> str: ...


@dataclass
class MarketContext:
    """Konteks pasar yang sama untuk semua emiten pada satu run."""

    benchmark_close: pd.Series | None = None
    bullish_regime: pd.Series | None = None
    """Series bool per tanggal: True bila IHSG di atas MA200 (SE-2)."""
    available: bool = True
    """False bila data IHSG tidak ada. Rezim di-anggap TIDAK bullish (fail-closed)."""


def build_context(benchmark: pd.DataFrame | None) -> MarketContext:
    if benchmark is None or benchmark.empty:
        # Fail-closed, mengikuti script v3: kalau IHSG tidak bisa dicek, tahan BELI.
        # Menganggap pasar bullish saat datanya hilang adalah arah kegagalan yang salah.
        return MarketContext(available=False)
    close = benchmark["close"]
    above = close > ind.sma(close, 200)
    # SE-2: rezim dinyatakan bearish bila IHSG di bawah MA200 selama > 10 hari beruntun.
    below_streak = (~above).astype(int).groupby((above != above.shift()).cumsum()).cumsum()
    bullish = ~(below_streak > 10)
    return MarketContext(benchmark_close=close, bullish_regime=bullish)


def _common(df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ind.ema(out["close"], 20)
    out["ema50"] = ind.ema(out["close"], 50)
    out["ema200"] = ind.ema(out["close"], 200)
    out["atr14"] = ind.atr(out, 14)
    out["rsi14"] = ind.rsi(out["close"], 14)
    out["vol_ma20"] = ind.sma(out["volume"], 20)
    out["swing_low"] = ind.last_swing_low(out)
    if ctx.benchmark_close is not None:
        out["rs"] = ind.rel_strength(out["close"], ctx.benchmark_close, 63)
    else:
        out["rs"] = np.nan
    if ctx.bullish_regime is not None:
        out["bullish"] = ctx.bullish_regime.reindex(out.index).ffill().fillna(False)
    else:
        out["bullish"] = bool(ctx.available)
    return out


def _atr_stop(df: pd.DataFrame, i: int, entry: float, mult: float) -> float:
    atr_value = df["atr14"].iloc[i]
    if not np.isfinite(atr_value) or atr_value <= 0:
        return entry * 0.93
    return entry - mult * float(atr_value)


# --------------------------------------------------------------------------- S1

@dataclass
class Breakout:
    name: str = "breakout"
    label: str = "Breakout Konsolidasi + Volume"
    long_only: bool = True
    lookback: int = 20
    vol_mult: float = 2.0
    adx_min: float = 20.0

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        out["resistance"] = ind.highest(out["high"], self.lookback).shift(1)
        out["adx"] = ind.adx(out, 14)["adx"]
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            (df["close"] > df["resistance"])
            & (df["volume"] > df["vol_ma20"] * self.vol_mult)
            & (df["adx"] > self.adx_min)
            & (df["close"] > df["ema50"])
            & (df["ema50"] > df["ema200"])
        ).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] < df["ema20"]).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        by_atr = _atr_stop(df, i, entry, 2.0)
        swing = df["swing_low"].iloc[i]
        # "pilih yang lebih dekat" = stop yang lebih tinggi (risiko lebih kecil).
        stop = max(by_atr, float(swing)) if np.isfinite(swing) else by_atr
        stop = min(stop, entry * 0.995)
        risk = entry - stop
        return Levels(stop, entry + 2 * risk, entry + 4 * risk)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        ratio = row["volume"] / row["vol_ma20"] if row["vol_ma20"] else float("nan")
        return (
            f"Tembus resistance {self.lookback}-hari di {row['resistance']:,.0f} dengan volume "
            f"{ratio:.1f}× rata-rata. EMA50 > EMA200, ADX {row['adx']:.0f}."
        )


# --------------------------------------------------------------------------- S2

@dataclass
class Pullback:
    name: str = "pullback"
    label: str = "Pullback ke MA dalam Uptrend"
    long_only: bool = True

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        out["touch_ema20"] = out["low"] <= out["ema20"] * 1.01
        out["bull_candle"] = out["close"] > out["open"]
        out["rsi_turn"] = (out["rsi14"] > out["rsi14"].shift(1)) & (
            out["rsi14"].shift(1).between(40, 55)
        )
        out["vol_declining"] = out["volume"] < out["vol_ma20"]
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            (df["ema20"] > df["ema50"])
            & (df["ema50"] > df["ema200"])
            & df["touch_ema20"]
            & df["bull_candle"]
            & df["rsi_turn"]
            & df["vol_declining"]
        ).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        return ((df["close"] < df["ema50"]) | (df["rsi14"] > 80)).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        atr_value = df["atr14"].iloc[i]
        low = float(df["low"].iloc[i])
        stop = low - 0.5 * float(atr_value) if np.isfinite(atr_value) else low * 0.97
        stop = min(stop, entry * 0.995)
        risk = entry - stop
        prior_high = float(ind.highest(df["high"], 20).iloc[i])
        tp2 = max(prior_high, entry + 3 * risk)
        return Levels(stop, entry + 1.5 * risk, tp2)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"Koreksi menyentuh EMA20 ({row['ema20']:,.0f}) lalu ditutup naik. "
            f"RSI berbalik dari {df['rsi14'].iloc[i - 1]:.0f}. Volume koreksi menurun."
        )


# --------------------------------------------------------------------------- S3

@dataclass
class MomentumRS:
    name: str = "momentum_rs"
    label: str = "Momentum Relative Strength"
    long_only: bool = True
    rs_percentile: float = 90.0

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        macd_frame = ind.macd(out["close"])
        out["hist"] = macd_frame["hist"]
        out["chandelier"] = ind.chandelier_exit(out, 22, 3.0)
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            (df["rs_rank"] >= self.rs_percentile)
            & (df["close"] > df["ema50"])
            & (df["hist"] > 0)
            & (df["hist"] > df["hist"].shift(1))
            & (df["rsi14"] < 78)
        ).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] < df["chandelier"]).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        stop = min(_atr_stop(df, i, entry, 2.5), entry * 0.995)
        risk = entry - stop
        return Levels(stop, entry + 2 * risk, entry + 4 * risk)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"RS vs IHSG persentil {row['rs_rank']:.0f} (3 bulan). Harga di atas EMA50, "
            f"MACD histogram positif dan menguat."
        )


# --------------------------------------------------------------------------- S4

@dataclass
class MeanReversion:
    name: str = "mean_reversion"
    label: str = "Mean Reversion Oversold"
    long_only: bool = True
    requires_bull_regime: bool = True

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        out["rsi2"] = ind.rsi(out["close"], 2)
        out["ma200"] = ind.sma(out["close"], 200)
        out["bb_lower"] = ind.bollinger(out["close"], 20, 2.0)["lower"]
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            df["bullish"]
            & (df["rsi2"] < 10)
            & (df["close"] > df["ma200"])
            & (df["low"] <= df["bb_lower"])
        ).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] >= df["ema20"]).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        stop = min(_atr_stop(df, i, entry, 2.0), entry * 0.995)
        risk = entry - stop
        target = float(df["ema20"].iloc[i])
        tp1 = max(target, entry + 0.8 * risk)
        return Levels(stop, tp1, max(tp1 * 1.02, entry + 1.5 * risk), 0.7, 0.3)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"RSI(2) {row['rsi2']:.0f} — oversold ekstrem, tapi harga masih di atas MA200 "
            f"dan IHSG dalam rezim bullish. Target balik ke EMA20 ({row['ema20']:,.0f})."
        )


REGISTRY: dict[str, Strategy] = {
    s.name: s for s in (Breakout(), Pullback(), MomentumRS(), MeanReversion())
}


def get(names: list[str] | None = None) -> list[Strategy]:
    if not names:
        return list(REGISTRY.values())
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise KeyError(f"strategi tidak dikenal: {', '.join(missing)} "
                       f"(tersedia: {', '.join(REGISTRY)})")
    return [REGISTRY[n] for n in names]
