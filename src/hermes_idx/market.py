"""Aturan bursa IDX: fraksi harga, auto rejection, biaya transaksi, lot.

Semua aturan yang bisa berubah disimpan sebagai tabel ber-`effective_from` supaya
backtest historis tidak memakai aturan hari ini untuk periode lampau (lihat issue #6).

CATATAN PENTING: saat ini hanya rezim terkini yang terisi. Rezim historis (fraksi harga
sebelum perubahan terakhir, dan ARB asimetris yang sempat berlaku) BELUM diisi karena
butuh verifikasi ke peraturan BEI. Backtest untuk periode lama akan memakai rezim terlama
yang tersedia dan menandainya lewat `regime_warning()`.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

LOT = 100
"""Lembar per lot di IDX."""

HARGA_MIN = 50
"""Batas bawah harga (saham 'gocap'). Tidak bisa ditransaksikan di bawah ini."""


# --------------------------------------------------------------------------- fraksi harga

@dataclass(frozen=True)
class TickRegime:
    effective_from: dt.date
    bands: tuple[tuple[float | None, int], ...]
    """(batas_atas_eksklusif, fraksi). Batas terakhir None = tak terhingga."""


TICK_REGIMES: tuple[TickRegime, ...] = (
    TickRegime(
        effective_from=dt.date(2000, 1, 1),
        bands=((200, 1), (500, 2), (2000, 5), (5000, 10), (None, 25)),
    ),
)
"""Rezim fraksi harga, urut menaik berdasarkan `effective_from`.

`effective_from` rezim pertama sengaja dibuat sangat lampau sebagai fallback. Begitu
riwayat perubahan fraksi harga terverifikasi (issue #6), tambahkan entri baru di sini —
tidak ada modul lain yang perlu disentuh.
"""

EARLIEST_VERIFIED_REGIME = dt.date(2023, 1, 1)
"""Tanggal paling awal yang rezimnya sudah kami verifikasi. Sebelum ini, hasil
pembulatan harga belum tentu benar — dipakai oleh `regime_warning()`."""


def _regime_for(date: dt.date | None) -> TickRegime:
    if date is None:
        return TICK_REGIMES[-1]
    chosen = TICK_REGIMES[0]
    for regime in TICK_REGIMES:
        if regime.effective_from <= date:
            chosen = regime
        else:
            break
    return chosen


def tick_size(price: float, date: dt.date | None = None) -> int:
    """Fraksi harga yang berlaku untuk `price` pada `date`."""
    if price < 0:
        raise ValueError(f"harga negatif: {price}")
    for upper, tick in _regime_for(date).bands:
        if upper is None or price < upper:
            return tick
    raise AssertionError("band fraksi harga tidak lengkap")


def round_to_tick(price: float, date: dt.date | None = None, mode: str = "nearest") -> int:
    """Bulatkan `price` ke fraksi harga yang valid.

    mode: 'down' | 'up' | 'nearest'.

    Pembulatan dilakukan terhadap fraksi di *hasil* pembulatan, bukan di harga input —
    penting di sekitar batas band (mis. 2000, di mana fraksi berubah 5 → 10).
    """
    if mode not in ("down", "up", "nearest"):
        raise ValueError(f"mode tidak dikenal: {mode}")
    if price <= 0:
        raise ValueError(f"harga harus positif: {price}")

    candidate = _round_with(price, tick_size(price, date), mode)
    # Harga hasil bisa jatuh ke band lain; ulangi sekali dengan fraksi band tujuan.
    tick_after = tick_size(candidate, date)
    if tick_after != tick_size(price, date):
        candidate = _round_with(price, tick_after, mode)
    return max(int(candidate), HARGA_MIN)


def _round_with(price: float, tick: int, mode: str) -> int:
    ratio = price / tick
    eps = 1e-9
    if mode == "down":
        steps = math.floor(ratio + eps)
    elif mode == "up":
        steps = math.ceil(ratio - eps)
    else:
        steps = math.floor(ratio + 0.5 + eps)
    return int(max(steps, 1) * tick)


def is_valid_tick(price: float, date: dt.date | None = None) -> bool:
    return price >= HARGA_MIN and abs(price % tick_size(price, date)) < 1e-9


# --------------------------------------------------------------------------- auto rejection

@dataclass(frozen=True)
class AutoRejectRegime:
    effective_from: dt.date
    bands: tuple[tuple[float | None, float, float], ...]
    """(batas_atas_eksklusif, batas_ara, batas_arb) sebagai fraksi desimal."""


AUTO_REJECT_REGIMES: tuple[AutoRejectRegime, ...] = (
    AutoRejectRegime(
        effective_from=dt.date(2000, 1, 1),
        bands=((200, 0.35, 0.35), (5000, 0.25, 0.25), (None, 0.20, 0.20)),
    ),
)
"""Batas auto rejection atas/bawah terhadap harga acuan (previous close).

Rezim ARB asimetris yang sempat berlaku BELUM diisi — lihat issue #1. Selama belum
terisi, backtest periode tersebut akan terlalu optimis di sisi ARB.
"""


def _ar_regime_for(date: dt.date | None) -> AutoRejectRegime:
    if date is None:
        return AUTO_REJECT_REGIMES[-1]
    chosen = AUTO_REJECT_REGIMES[0]
    for regime in AUTO_REJECT_REGIMES:
        if regime.effective_from <= date:
            chosen = regime
        else:
            break
    return chosen


def auto_reject_bounds(prev_close: float, date: dt.date | None = None) -> tuple[int, int]:
    """Batas (bawah, atas) harga yang boleh diperdagangkan hari ini.

    Di luar rentang ini order akan ditolak sistem bursa — artinya saham yang menyentuh
    batas atas (ARA) praktis tidak bisa dibeli, dan yang menyentuh batas bawah (ARB)
    tidak bisa dijual. Backtest wajib memperhitungkan ini (issue #1).
    """
    if prev_close <= 0:
        raise ValueError(f"harga acuan harus positif: {prev_close}")
    regime = _ar_regime_for(date)
    for upper, ara, arb in regime.bands:
        if upper is None or prev_close < upper:
            lower_raw = prev_close * (1 - arb)
            upper_raw = prev_close * (1 + ara)
            lower = max(round_to_tick(lower_raw, date, "up"), HARGA_MIN)
            return lower, round_to_tick(upper_raw, date, "down")
    raise AssertionError("band auto rejection tidak lengkap")


def is_ara(price: float, prev_close: float, date: dt.date | None = None) -> bool:
    """True bila `price` sudah berada di batas atas — tidak bisa dibeli lagi."""
    return price >= auto_reject_bounds(prev_close, date)[1]


def is_arb(price: float, prev_close: float, date: dt.date | None = None) -> bool:
    """True bila `price` sudah berada di batas bawah — tidak bisa dijual."""
    return price <= auto_reject_bounds(prev_close, date)[0]


def regime_warning(period_start: dt.date) -> str | None:
    """Peringatan bila backtest menjangkau periode yang rezim aturannya belum diverifikasi."""
    if period_start < EARLIEST_VERIFIED_REGIME:
        return (
            f"Periode backtest dimulai {period_start} — lebih awal dari rezim aturan bursa "
            f"yang sudah diverifikasi ({EARLIEST_VERIFIED_REGIME}). Fraksi harga dan batas "
            f"auto rejection untuk periode itu memakai rezim terlama yang tersedia dan "
            f"bisa salah. Lihat issue #1 dan #6."
        )
    return None


# --------------------------------------------------------------------------- biaya & lot

@dataclass(frozen=True)
class Fees:
    """Biaya transaksi. Semua persentase dalam fraksi desimal (0.0015 = 0.15%)."""

    buy_pct: float = 0.0015
    sell_pct: float = 0.0025
    """Sudah termasuk PPh final 0.1% atas nilai penjualan."""
    min_fee_rp: float = 0.0
    """Fee minimum per transaksi. Banyak broker menerapkan; signifikan untuk posisi kecil."""
    slippage_ticks: int = 1

    def buy_cost(self, price: float, lots: int) -> float:
        return max(price * lots * LOT * self.buy_pct, self.min_fee_rp)

    def sell_cost(self, price: float, lots: int) -> float:
        return max(price * lots * LOT * self.sell_pct, self.min_fee_rp)


def lots_to_shares(lots: int) -> int:
    return lots * LOT


def net_buy_value(price: float, lots: int, fees: Fees) -> float:
    """Total rupiah keluar saat membeli (harga + fee)."""
    return price * lots * LOT + fees.buy_cost(price, lots)


def net_sell_value(price: float, lots: int, fees: Fees) -> float:
    """Total rupiah masuk saat menjual (harga − fee)."""
    return price * lots * LOT - fees.sell_cost(price, lots)


def apply_slippage(price: float, side: str, fees: Fees, date: dt.date | None = None) -> int:
    """Geser harga sejauh `slippage_ticks` ke arah yang merugikan kita.

    side: 'buy' (harga naik) | 'sell' (harga turun).
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side tidak dikenal: {side}")
    price_now = float(price)
    for _ in range(max(fees.slippage_ticks, 0)):
        tick = tick_size(price_now, date)
        price_now += tick if side == "buy" else -tick
        price_now = max(price_now, HARGA_MIN)
    return round_to_tick(price_now, date, "up" if side == "buy" else "down")
