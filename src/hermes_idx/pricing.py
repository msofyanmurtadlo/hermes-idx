"""Aturan fraksi harga IDX — harga order harus kelipatan tick yang valid.

Fraksi harga BEI (pasar reguler):
    Rp50–199     : Rp1
    Rp200–499    : Rp2
    Rp500–1.999  : Rp5
    Rp2.000–4.999: Rp10
    >= Rp5.000   : Rp25
    (< Rp50, papan akselerasi: Rp1)

Semua harga yang disarankan ke user (SL, TP, trailing stop) WAJIB lewat
`snap_price()` — Stockbit menolak order yang tidak kelipatan fraksi
("Fraksi harga harus dalam kelipatan 25").
"""
from __future__ import annotations

import math


def tick_of(price: float) -> int:
    """Fraksi (tick size) untuk level harga tertentu."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def snap_price(price: float, mode: str = "nearest") -> int:
    """Bulatkan harga ke kelipatan fraksi yang valid.

    mode='floor'  → turun; untuk SL (proteksi tidak berkurang, harga jual
                     stop sedikit lebih baik).
    mode='ceil'   → naik; untuk TP.
    mode='nearest'→ terdekat; untuk entry estimate.
    """
    tick = tick_of(price)
    if mode == "floor":
        return int(math.floor(price / tick) * tick)
    if mode == "ceil":
        return int(math.ceil(price / tick) * tick)
    return int(round(price / tick) * tick)
