"""Indikator teknikal, pandas/numpy murni.

Tidak memakai `pandas-ta` maupun TA-Lib. TA-Lib butuh kompilasi C yang tidak reliabel di
Termux; `pandas-ta` rusak pada numpy >= 2.0 (memakai `numpy.NaN` yang sudah dihapus).
Semua indikator di bawah ini one-liner sampai belasan baris, jadi menulis sendiri lebih
murah daripada menanggung dependensi rapuh di platform target.

Konvensi: fungsi menerima `pd.Series` atau `pd.DataFrame` ber-kolom
`open/high/low/close/volume` dan mengembalikan `pd.Series`/`pd.DataFrame` dengan index sama.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sma", "ema", "wilder", "rsi", "true_range", "atr", "adx", "macd",
    "bollinger", "keltner", "highest", "lowest", "stoch", "roc", "williams_r",
    "obv", "mfi", "vwap", "accum_dist", "supertrend", "chandelier_exit",
    "rel_strength", "swing_high", "swing_low", "higher_high_count",
]


# --------------------------------------------------------------------------- moving average

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Smoothing Wilder (RMA) — dipakai RSI, ATR, ADX."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# --------------------------------------------------------------------------- momentum

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder((-delta).clip(lower=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # loss == 0 dan gain > 0 → RSI 100; keduanya 0 → 50 (harga datar).
    out = out.where(loss != 0, np.where(gain > 0, 100.0, 50.0))
    return out.astype(float)


def roc(close: pd.Series, n: int = 20) -> pd.Series:
    return close.pct_change(n) * 100


def stoch(df: pd.DataFrame, n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    low_n = df["low"].rolling(n, min_periods=n).min()
    high_n = df["high"].rolling(n, min_periods=n).max()
    rng = (high_n - low_n).replace(0, np.nan)
    fast_k = 100 * (df["close"] - low_n) / rng
    slow_k = fast_k.rolling(k, min_periods=k).mean()
    return pd.DataFrame({"k": slow_k, "d": slow_k.rolling(d, min_periods=d).mean()})


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high_n = df["high"].rolling(n, min_periods=n).max()
    low_n = df["low"].rolling(n, min_periods=n).min()
    rng = (high_n - low_n).replace(0, np.nan)
    return -100 * (high_n - df["close"]) / rng


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


# --------------------------------------------------------------------------- volatilitas

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_n = wilder(true_range(df), n).replace(0, np.nan)
    plus_di = 100 * wilder(plus_dm, n) / atr_n
    minus_di = 100 * wilder(minus_dm, n) / atr_n
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": plus_di, "minus_di": minus_di})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def keltner(df: pd.DataFrame, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = ema(df["close"], n)
    rng = k * atr(df, n)
    return pd.DataFrame({"mid": mid, "upper": mid + rng, "lower": mid - rng})


# --------------------------------------------------------------------------- volume

def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    up = flow.where(tp.diff() > 0, 0.0).rolling(n, min_periods=n).sum()
    down = flow.where(tp.diff() < 0, 0.0).rolling(n, min_periods=n).sum()
    ratio = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + ratio)).fillna(100.0).where(up + down > 0)


def vwap(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(n, min_periods=n).sum()
    vol = df["volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)
    return pv / vol


def accum_dist(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm.fillna(0.0) * df["volume"]).cumsum()


# --------------------------------------------------------------------------- struktur

def highest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).max()


def lowest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).min()


def swing_high(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.Series:
    """True pada bar yang high-nya lebih tinggi dari `left` bar kiri & `right` bar kanan.

    Perhatikan: sinyal ini baru bisa dipakai `right` bar SETELAH bar tersebut, karena
    butuh konfirmasi bar kanan. Pemakai wajib `.shift(right)` agar bebas look-ahead.
    """
    high = df["high"]
    cond = pd.Series(True, index=df.index)
    for i in range(1, left + 1):
        cond &= high > high.shift(i)
    for i in range(1, right + 1):
        cond &= high > high.shift(-i)
    return cond


def swing_low(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.Series:
    """Kebalikan `swing_high`. Berlaku peringatan look-ahead yang sama."""
    low = df["low"]
    cond = pd.Series(True, index=df.index)
    for i in range(1, left + 1):
        cond &= low < low.shift(i)
    for i in range(1, right + 1):
        cond &= low < low.shift(-i)
    return cond


def last_swing_low(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.Series:
    """Harga swing low terakhir yang SUDAH terkonfirmasi pada tiap bar (bebas look-ahead)."""
    confirmed = swing_low(df, left, right).shift(right).fillna(False)
    return df["low"].where(confirmed).ffill()


def higher_high_count(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Jumlah bar dalam `n` terakhir yang membentuk higher-high sekaligus higher-low."""
    hh = (df["high"] > df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    return hh.rolling(n, min_periods=1).sum()


# --------------------------------------------------------------------------- trailing stop

def chandelier_exit(df: pd.DataFrame, n: int = 22, k: float = 3.0) -> pd.Series:
    """Chandelier Exit untuk posisi long: highest(high, n) − k × ATR(n)."""
    return highest(df["high"], n) - k * atr(df, n)


def supertrend(df: pd.DataFrame, n: int = 10, k: float = 3.0) -> pd.DataFrame:
    """Supertrend. Rekursif secara definisi, jadi memakai loop per bar.

    ponytail: loop Python O(bar). ~1250 bar × 300 emiten masih di bawah satu detik.
    Kalau nanti jadi bottleneck, pindahkan ke numpy stride tricks atau cache per-ticker.
    """
    atr_n = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = (hl2 + k * atr_n).to_numpy(dtype=float)
    lower_basic = (hl2 - k * atr_n).to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    size = len(df)
    upper = np.full(size, np.nan)
    lower = np.full(size, np.nan)
    trend = np.full(size, np.nan)
    direction = 1

    for i in range(size):
        if np.isnan(upper_basic[i]):
            continue
        if i == 0 or np.isnan(upper[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction = 1
        else:
            upper[i] = (
                min(upper_basic[i], upper[i - 1])
                if close[i - 1] <= upper[i - 1] else upper_basic[i]
            )
            lower[i] = (
                max(lower_basic[i], lower[i - 1])
                if close[i - 1] >= lower[i - 1] else lower_basic[i]
            )
            if close[i] > upper[i - 1]:
                direction = 1
            elif close[i] < lower[i - 1]:
                direction = -1
        trend[i] = direction

    line = np.where(trend == 1, lower, upper)
    return pd.DataFrame({"supertrend": line, "direction": trend}, index=df.index)


# --------------------------------------------------------------------------- relatif

def rel_strength(close: pd.Series, benchmark: pd.Series, n: int = 63) -> pd.Series:
    """Kekuatan relatif terhadap benchmark: selisih return `n` bar (dalam poin persen).

    `n=63` ≈ 3 bulan bursa. Benchmark di-align ke index `close`; tanggal yang tidak ada
    di benchmark di-forward-fill supaya suspensi sepihak tidak menghasilkan NaN beruntun.
    """
    bench = benchmark.reindex(close.index).ffill()
    return (close.pct_change(n) - bench.pct_change(n)) * 100
