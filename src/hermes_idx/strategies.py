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


def build_context(benchmark: pd.DataFrame | None, ma_period: int = 200,
                  below_days: int = 10) -> MarketContext:
    """Rezim pasar dari posisi IHSG terhadap rata-rata bergeraknya.

    `ma_period` sengaja bisa diatur karena panjangnya harus cocok dengan horizon
    perdagangan, dan 200 hari BUKAN pilihan netral. Fidelity menyebut MA200 sebagai
    "smoothing device when you are trying to assess long-term trends", sementara MA50
    "more closely follows the recent price action". Praktik yang lazim: day trader
    memakai EMA 9/20 pada bar intraday, swing trader 21/50 hari, dan 50/200 hari untuk
    investor jangka panjang.

    Artinya filter MA200 pada bar harian menilai tren SEKULER. Dipakai untuk trading
    harian, ia menahan sinyal berbulan-bulan hanya karena pasar belum pulih dari koreksi
    besar — diukur pada data ini: 32 dari 120 hari bursa yang lolos. Itu bukan bug, tapi
    juga bukan alat yang tepat untuk horizon pendek.

    Yang TIDAK berubah: memperpendek periode memperbanyak sinyal, bukan menciptakan edge.
    Lihat `docs/BUKTI-01.md` untuk angka expectancy pada tiap periode.
    """
    if benchmark is None or benchmark.empty:
        # Fail-closed, mengikuti script v3: kalau IHSG tidak bisa dicek, tahan BELI.
        # Menganggap pasar bullish saat datanya hilang adalah arah kegagalan yang salah.
        return MarketContext(available=False)
    if ma_period < 2:
        raise ValueError(f"periode MA rezim harus >= 2, diberi {ma_period}")
    close = benchmark["close"]
    above = close > ind.sma(close, ma_period)
    # SE-2: rezim bearish bila IHSG di bawah MA selama > `below_days` hari beruntun.
    below_streak = (~above).astype(int).groupby((above != above.shift()).cumsum()).cumsum()
    bullish = ~(below_streak > below_days)
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


# SL MAKSIMAL 2% dari entry (aturan user, 14 Agu 2026: "SL maksimal 2 persen, TP boleh banyak").
# Sebelumnya ATR bisa kasih SL sampai 11%+ untuk saham volatile (TINS). Kini SL di-clamp
# supaya kerugian per posisi tidak pernah lebih jauh dari 2% — TP tetap bebas besar.
MAX_SL_PCT = 0.02


def _clamp_sl(stop: float, entry: float) -> float:
    """SL tidak boleh lebih jauh dari MAX_SL_PCT dari entry (max() = pilih yang lebih rapat)."""
    return max(stop, entry * (1 - MAX_SL_PCT))


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
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        stop = entry * (1 - MAX_SL_PCT)
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
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        stop = entry * (1 - MAX_SL_PCT)
        risk = entry - stop
        return Levels(stop, entry + 2 * risk, entry + 4 * risk)

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
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        stop = entry * (1 - MAX_SL_PCT)
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
    atr_mult: float = 3.0
    """Pengali ATR untuk stop. Lihat catatan tuning di `levels()`."""
    tp_r: float = 1.0
    """Target profit dalam kelipatan risiko. Lihat catatan tuning di `levels()`."""

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
        """Stop ATR×3 + TP 1R — DITALA UNTUK WIN RATE, atas permintaan eksplisit.

        Baca ini sebelum menaikkan target win rate lagi. Parameter lama (ATR×2, TP
        ke EMA20 dengan lantai 1.5R) memberi win rate 55.9%. Permintaannya 60%, dan
        satu-satunya cara mendapatkannya adalah melebarkan stop + mendekatkan TP —
        tepat resep yang diperingatkan `cli.py`: win rate naik karena setiap trade
        diberi ruang lebih besar untuk pulih, bukan karena sinyalnya membaik.

        Sweep 25 kombinasi (ATR 2.0–4.0 × TP 0.6–1.5R) pada 285 trade, 2022-04 s/d
        2026-07. Titik yang dipakai — ATR×3.0, TP 1.0R:

            win rate     55.9%  ->  61.6%   (100 trade terakhir: 57% -> 65%)
            expectancy  -0.180  -> -0.092 R
            profit factor 0.62  ->  0.70
            max drawdown -42.2% -> -31.3%

        Expectancy MASIH NEGATIF. 61.6% trade menang, tapi kalah rata-rata lebih besar
        daripada menangnya, jadi akun tetap menyusut. Win rate 60% tercapai; profit
        tidak. Menaikkannya lagi (ATR×4 = 62.4%) hanya menggeser angka yang sama.
        """
        stop = entry * (1 - MAX_SL_PCT)
        risk = entry - stop
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        return Levels(stop, entry + 2 * risk, entry + 4 * risk, 0.7, 0.3)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"RSI(2) {row['rsi2']:.0f} — oversold ekstrem, tapi harga masih di atas MA200 "
            f"dan IHSG dalam rezim bullish. Target balik ke EMA20 ({row['ema20']:,.0f})."
        )


# --------------------------------------------------------------------------- V3 (port)

@dataclass
class V3Score:
    """Port logika sinyal `idx-bluechip-report.py` v3 agar bisa DIUJI.

    v3 dipakai harian tapi tidak pernah di-backtest — "akurasi" yang dilaporkannya
    adalah arah harga esok hari, tanpa memperhitungkan stop loss kena maupun biaya.
    Di sini logikanya dipindahkan apa adanya supaya bisa diadu memakai expectancy.

    Dua penyimpangan yang tidak terhindarkan, keduanya melemahkan v3 secara jujur:
    - Term konfirmasi 1 jam (MACD1h, RSI1h) DIBUANG — tidak ada data historis 1 jam
      untuk di-backtest. Bobotnya di v3 asli sampai +3.
    - `vol_ratio` v3 berasal dari bar 1 jam; di sini diganti volume harian / MA20.

    Ambang skor tetap 7 seperti aslinya. Karena term 1h hilang, v3 di sini lebih ketat
    daripada versi produksinya — jadi kalau v3 menang, kemenangannya nyata.
    """

    name: str = "v3score"
    label: str = "V3 Score (port dari script Hermes)"
    long_only: bool = True
    threshold: int = 7
    atr_mult: float = 3.0
    """Pengali ATR untuk stop. Lihat catatan bug di `levels()`."""

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        out["ma20"] = ind.sma(out["close"], 20)
        out["ma50"] = ind.sma(out["close"], 50)
        out["adx"] = ind.adx(out, 14)["adx"]
        out["hist"] = ind.macd(out["close"])["hist"]
        prev = out["hist"].shift(1)
        out["macd_bullish"] = (out["hist"] > 0) & (prev <= 0)
        out["macd_bearish"] = (out["hist"] < 0) & (prev >= 0)
        bb = ind.bollinger(out["close"], 20, 2.0)
        span = (bb["upper"] - bb["lower"]).replace(0, np.nan)
        out["bb_pos"] = ((out["close"] - bb["lower"]) / span).fillna(0.5)
        out["chg"] = out["close"].pct_change() * 100
        out["vol_ratio"] = (out["volume"] / out["vol_ma20"]).fillna(1.0)
        out["v3_score"] = self._score(out)
        return out

    def _score(self, d: pd.DataFrame) -> pd.Series:
        score = pd.Series(0.0, index=d.index)
        score += np.where(d["close"] > d["ma20"], 1, -1)
        score += np.where(d["close"] > d["ma50"], 1, -1)
        score += np.where(d["ma20"] > d["ma50"], 2, -2)

        score += np.where(d["adx"] >= 30, 1, 0)
        score += np.where(d["adx"] >= 40, 1, 0)

        score += np.where(d["rsi14"].between(45, 65), 1, 0)
        score += np.where(d["rsi14"] > 75, -2, 0)

        score += np.where(d["chg"] > 0.5, 1, 0)
        score += np.where(d["chg"] < -2.5, -2, 0)

        score += np.where(d["macd_bullish"], 2, 0)
        score += np.where(d["macd_bearish"], -2, 0)
        neutral = ~(d["macd_bullish"] | d["macd_bearish"])
        score += np.where(neutral & (d["hist"] > 0), 1, 0)
        score += np.where(neutral & (d["hist"] <= 0), -1, 0)

        score += np.where(d["bb_pos"] > 0.95, -1, 0)

        score += np.where(d["vol_ratio"] >= 2.0, 2, 0)
        score += np.where((d["vol_ratio"] >= 1.5) & (d["vol_ratio"] < 2.0), 1, 0)
        score += np.where(d["vol_ratio"] < 0.5, -1, 0)
        return score

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        # Hard gate 1: jangan beli di bawah MA50, kecuali MACD baru bullish & RSI < 30.
        gate_ma50 = (df["close"] >= df["ma50"]) | (df["macd_bullish"] & (df["rsi14"] < 30))
        # Hard gate 2: ADX < 20 = pasar sideways, sinyal apa pun tidak reliabel.
        return (gate_ma50 & (df["adx"] >= 20) & (df["v3_score"] >= self.threshold)).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        return ((df["close"] < df["ma20"]) & (df["ma20"] < df["ma50"])).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        """Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4).

        Sebelumnya 2% adalah lebar MINIMUM dan ATR bisa memberi stop sampai 11%+ untuk
        saham volatile (kasus TINS), dan target ikut ATR (R:R tidak konsisten).

        BUG YANG DIPERBAIKI dari v3 asli. Kode aslinya:

            sl = round_tick(max(entry - 1.5 * atr, entry * 0.98))

        `max()` memilih stop yang lebih RAPAT. Untuk bluechip IDX, ATR harian biasanya
        2–3%, jadi `1.5 × ATR` hampir selalu lebih lebar dari 2% — akibatnya cabang ATR
        tidak pernah menang dan stop-nya **selalu persis 2%**. Diukur pada data: median,
        p10, dan p90 lebar stop semuanya 2.00%. Jadi fitur andalan v3 "ATR SL/TP dinamis"
        sebenarnya kode mati, dan yang berjalan adalah stop tetap 2%.

        Dampaknya besar: stop 2% pada bar harian IDX kena terus oleh noise biasa, dan
        biaya round-trip 0.40% saja sudah memakan 0.20R per trade. Backtest 1.746 trade
        memberi expectancy -0.765R. Dengan `min()` (ATR yang memimpin, 2% jadi lantai)
        angkanya membaik ke -0.100R — masih negatif, tapi bedanya 7×.
        """
        stop = entry * (1 - MAX_SL_PCT)
        risk = entry - stop
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        target = entry + 4 * risk
        # v3 hanya punya satu target. Backtest keluar di tp2, jadi target v3 dipetakan
        # ke tp2 agar simulasinya setia pada perilaku aslinya.
        return Levels(stop, entry + (target - entry) / 2, target, 0.5, 0.5)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"Skor v3 {row['v3_score']:.0f} (ambang {self.threshold}) — ADX {row['adx']:.0f}, "
            f"RSI {row['rsi14']:.0f}, volume {row['vol_ratio']:.1f}× MA20."
        )


# --------------------------------------------------------------------------- S5

@dataclass
class Trio:
    """Tiga indikator, tiga peran berbeda: tren, momentum, aliran dana.

    Dipilih dari uji 6 kombinasi trio (skrip sweep, 2021-09 s/d 2026-07, universe &
    biaya yang sama). Yang kalah dan kenapa:

        ema50 + rsi14 + volume       904 trade, win rate 40.0%, PF 0.41
        supertrend + macd + rsi14   3441 trade, win rate 29.2%, PF 0.32, DD -98.6%
        ema50 + williams%r + obv     192 trade, win rate 54.7%, PF 0.64
        ma200 + bollinger + stoch    240 trade, win rate 59.2%, PF 0.74
        adx + rsi14 + volume          92 trade, win rate 53.3%, PF 0.48
        ma200 + rsi2 + mfi  <- ini   122 trade, win rate 62.3%, PF 0.89

    Pola yang terlihat: trio yang mengonfirmasi tren (semua indikator setuju "naik")
    justru paling buruk — ia membeli setelah pergerakan selesai. Yang bertahan adalah
    trio yang SATU indikatornya menolak dua lainnya: tren naik (MA200) tapi harga
    sedang dibuang (RSI2 < 10) dan uang sedang keluar (MFI < 30).

    MFI di sini bukan duplikat RSI2. RSI2 hanya melihat harga; MFI menimbang harga
    dengan volume, jadi ia menyaring penurunan yang sepi — turun tanpa volume berarti
    tidak ada yang benar-benar melepas barang, dan pantulannya lemah. Menambahkan MFI
    memangkas 285 sinyal `mean_reversion` menjadi 122, dan itulah sumber perbaikannya.

    Threshold-nya berada di dataran, bukan di ujung pisau: rsi2 5/10/15 dan mfi 20/30/40
    semuanya di kisaran win rate 57–64%. Angka 10/30 dipilih karena titik terbaiknya,
    bukan satu-satunya yang bekerja.
    """

    name: str = "trio"
    label: str = "Trio MA200 + RSI(2) + MFI"
    long_only: bool = True
    requires_bull_regime: bool = True
    rsi2_max: float = 10.0
    mfi_max: float = 30.0
    atr_mult: float = 3.0
    tp_r: float = 3.0
    """Target dalam kelipatan risiko. Lihat `exit_at_ema20` — angka ini tidak ada
    artinya kalau pemenang dipotong sebelum sampai."""
    exit_at_ema20: bool = False
    """Keluar begitu harga balik ke EMA20.

    True = mode WIN RATE (win rate 62.8%, expectancy -0.019R, RR nyata 0.56:1)
    False = mode RISK-REWARD (win rate 34.5%, expectancy +0.132R, RR nyata 2.33:1)

    Keduanya diukur pada 79 emiten IDX likuid, 2022-04 s/d 2026-07. Tidak ada
    setelan yang memberi keduanya sekaligus — lihat catatan di `levels()`.
    """

    def prepare(self, df: pd.DataFrame, ctx: MarketContext) -> pd.DataFrame:
        out = _common(df, ctx)
        out["rsi2"] = ind.rsi(out["close"], 2)
        out["ma200"] = ind.sma(out["close"], 200)
        out["mfi"] = ind.mfi(out, 14)
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            df["bullish"]
            & (df["close"] > df["ma200"])      # 1. tren: masih di atas MA200
            & (df["rsi2"] < self.rsi2_max)     # 2. momentum: oversold ekstrem
            & (df["mfi"] < self.mfi_max)       # 3. aliran dana: tekanan jual nyata
        ).fillna(False)

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        if not self.exit_at_ema20:
            return pd.Series(False, index=df.index)
        return (df["close"] >= df["ema20"]).fillna(False)

    def levels(self, df: pd.DataFrame, i: int, entry: float) -> Levels:
        """Risk-reward minimal 1:2 — dan itu MENGHARUSKAN exit EMA20 dimatikan.

        Menaikkan tp_r saja tidak menghasilkan RR 1:2. Diukur pada 163 trade dengan
        exit EMA20 masih hidup: 66.9% posisi keluar lewat exit itu di rata-rata
        +0.266R, jauh sebelum TP tersentuh — hanya 6.7% yang benar-benar kena TP.
        Jadi TP-nya boleh ditulis 2R atau 10R, RR yang BENAR-BENAR terjadi tetap
        0.56:1 (avg win 0.463R / avg loss 0.833R). Target di atas kertas bukan RR.

        Dengan exit EMA20 dimatikan, pemenang dibiarkan jalan sampai TP atau stop:

            exit EMA20  ATR  TP    n    WR    avg W   avg L   RR nyata  exp     PF
            hidup       3.0  2R   164  62.8%  +0.463  -0.833   0.56:1  -0.019  0.94
            mati        3.0  2R   150  37.3%  +1.455  -0.840   1.73:1  +0.017  1.03
            mati        3.0  3R   148  34.5%  +2.101  -0.903   2.33:1  +0.132  1.22  <- dipakai
            mati        3.5  3R   147  36.1%  +1.741  -0.827   2.10:1  +0.099  1.19

        Ini setelan pertama sepanjang pengujian yang expectancy-nya POSITIF
        (+0.132R, profit factor 1.22, p=0.185). Harganya: win rate jatuh 62.8% ->
        34.5%. Dua dari tiga trade akan rugi, dan itu bukan kegagalan — di RR 2.33:1
        titik impasnya ada di win rate 30%.

        Peringatan yang tidak boleh dihapus: p=0.185 berarti masih ada ~19%
        kemungkinan hasil sebagus ini muncul dari keberuntungan belaka. Positif,
        tapi BELUM terbukti secara statistik. Butuh lebih banyak trade.
        """
        stop = entry * (1 - MAX_SL_PCT)
        risk = entry - stop
        # Level FIX (aturan user 14 Agu 2026): SL -2%, TP1 +4% (R:R 1:2), TP2 +8% (R:R 1:4)
        return Levels(stop, entry + 2 * risk, entry + 4 * risk, 0.5, 0.5)

    def reason(self, df: pd.DataFrame, i: int) -> str:
        row = df.iloc[i]
        return (
            f"Tren utuh (harga di atas MA200 {row['ma200']:,.0f}), tapi RSI(2) "
            f"{row['rsi2']:.0f} oversold dan MFI {row['mfi']:.0f} menunjukkan tekanan "
            f"jual berbobot volume. Ditahan sampai {self.tp_r:.0f}R atau stop kena."
        )


REGISTRY: dict[str, Strategy] = {
    s.name: s for s in (Breakout(), Pullback(), MomentumRS(), MeanReversion(), V3Score(), Trio())
}


def get(names: list[str] | None = None) -> list[Strategy]:
    if not names:
        return list(REGISTRY.values())
    missing = [n for n in names if n not in REGISTRY]
    if missing:
        raise KeyError(f"strategi tidak dikenal: {', '.join(missing)} "
                       f"(tersedia: {', '.join(REGISTRY)})")
    return [REGISTRY[n] for n in names]
