"""Backtest engine.

PENAMAAN (issue #2): strategi bawaan berparameter tetap — tidak ada yang di-fit. Karena
itu metode di sini disebut apa adanya: **rolling out-of-sample**, bukan walk-forward.
Walk-forward baru bermakna kalau periode train benar-benar memfit sesuatu. Kalau nanti
optimasi parameter diimplementasikan, ganti `method` menjadi `walk_forward` dan aktifkan
pelaporan degradasi in-sample vs out-of-sample (BT-4).

AUTO REJECTION (issue #1): sinyal yang open T+1-nya sudah menyentuh batas ARA dihitung
sebagai `unfilled` — tidak bisa dibeli di dunia nyata, jadi tidak boleh dihitung untung.
Exit yang jatuh pada bar ARB ditunda ke bar berikutnya yang bisa ditransaksikan.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import market, strategies as strat


@dataclass
class Trade:
    ticker: str
    strategy: str
    entry_date: dt.date
    exit_date: dt.date
    entry_price: float
    exit_price: float
    lots: int
    r_multiple: float
    pnl_rp: float
    pnl_pct: float
    exit_reason: str


@dataclass
class Result:
    strategy: str
    method: str
    period_start: dt.date | None
    period_end: dt.date | None
    trades: list[Trade] = field(default_factory=list)
    unfilled_ara: int = 0
    warnings: list[str] = field(default_factory=list)

    def metrics(self, risk_pct: float = 0.01) -> dict:
        return summarize(self.trades, self.unfilled_ara, risk_pct)


def simulate(
    ticker: str,
    df: pd.DataFrame,
    strategy: strat.Strategy,
    cfg,
    time_stop_days: int = 20,
    partial: bool = True,
    breakeven_at_r: float = 0.0,
    max_holding_days: int = 0,
) -> tuple[list[Trade], int]:
    """Simulasi satu emiten. Satu posisi pada satu waktu.

    Aturan eksekusi (BT-1): sinyal pada close T dieksekusi pada open T+1 + slippage.

    `partial` dan `breakeven_at_r` mengaktifkan rencana exit bertahap. Keduanya bisa
    dimatikan supaya hasil lama tetap bisa direproduksi — perbandingan A/B-nya ada di
    docstring `Trio.levels()`.
    """
    entries = strategy.entry_signal(df)
    # Backtest WAJIB memakai filter yang sama dengan screening live. Sebelumnya tidak:
    # `screen.scan()` menolak entry saat rezim bearish, sementara `simulate()` mengambil
    # semuanya. Akibatnya expectancy yang dilaporkan menggambarkan strategi yang tidak
    # pernah benar-benar dijalankan siapa pun, dan filter rezimnya sendiri tidak pernah
    # terukur — padahal ia yang menahan 73% hari bursa.
    if cfg.data["screening"].get("market_regime_filter", True) and "bullish" in df.columns:
        entries = entries & df["bullish"].astype(bool)
    entries = entries.to_numpy()
    exits = strategy.exit_signal(df).to_numpy()
    open_, high, low, close = (df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    dates = [d.date() for d in df.index]
    fees = cfg.fees

    trades: list[Trade] = []
    unfilled = 0
    i, size = 0, len(df)

    while i < size - 1:
        if not entries[i]:
            i += 1
            continue

        exec_i = i + 1
        prev_close = close[exec_i - 1]
        entry_raw = open_[exec_i]
        if not np.isfinite(entry_raw) or not np.isfinite(prev_close) or prev_close <= 0:
            i += 1
            continue

        # ARA: tidak ada offer untuk dibeli.
        if market.is_ara(entry_raw, prev_close, dates[exec_i]):
            unfilled += 1
            i += 1
            continue

        entry = market.apply_slippage(entry_raw, "buy", fees, dates[exec_i])
        levels = strategy.levels(df, i, float(entry))
        stop = market.round_to_tick(levels.stop_loss, dates[exec_i], "down")
        if stop >= entry:
            i += 1
            continue
        risk = entry - stop
        tp2 = market.round_to_tick(levels.tp2, dates[exec_i], "down")
        lots = max(int(cfg.modal * cfg.risk_pct // (risk * market.LOT)), 1)

        # RENCANA EXIT SEBENARNYA (perbaikan cacat, bukan fitur baru): mesin ini dulu
        # hanya memakai `stop_loss` dan `tp2`, lalu keluar sekaligus. `tp1`, `tp1_size`,
        # dan `breakeven_at_r` diabaikan — padahal ketiganya YANG dijalankan di layar
        # oleh `signals.build()` dan laporan harian. Artinya angka backtest selama ini
        # menggambarkan rencana yang tidak pernah dipakai siapa pun.
        #
        # Konsekuensi yang harus diketahui: partial exit hanya mungkin kalau posisinya
        # >= 2 lot (IDX tidak mengenal pecahan lot). Untuk modal kecil dengan stop lebar,
        # `lots` bisa jatuh ke 1 dan rencana ini otomatis kembali ke exit tunggal.
        tp1 = market.round_to_tick(levels.tp1, dates[exec_i], "down")
        lots_tp1 = int(lots * levels.tp1_size) if partial else 0
        if not (entry < tp1 < tp2) or lots_tp1 <= 0 or lots_tp1 >= lots:
            lots_tp1 = 0
        be_trigger = entry + breakeven_at_r * risk if breakeven_at_r else np.inf

        exit_i, exit_price, reason = None, None, ""
        peak_high = -np.inf  # tertinggi sejak entry — dipakai time stop di bawah
        tp1_price, sold_lots, sold_value = 0.0, 0, 0.0
        for j in range(exec_i, size):
            if np.isfinite(high[j]):
                peak_high = max(peak_high, high[j])
            if low[j] <= stop:
                # Gap melewati SL → eksekusi di open, bukan di harga SL.
                exit_price = min(open_[j], stop) if open_[j] < stop else stop
                exit_i, reason = j, ("BREAKEVEN" if stop >= entry else "CUT_LOSS")
            elif high[j] >= tp2:
                exit_price, exit_i, reason = tp2, j, "TAKE_PROFIT"
            elif exits[j]:
                if j + 1 >= size:
                    break
                exit_price, exit_i, reason = open_[j + 1], j + 1, "EXIT_SIGNAL"
            # "belum mencapai 1R" = tidak pernah menyentuhnya sejak entry. Dulu dicek
            # `high[j] < entry + risk`, yaitu bar hari itu saja — trade yang sempat +3R
            # lalu balik turun tetap di-TIME_STOP seolah tak pernah bergerak.
            elif j - exec_i >= time_stop_days and peak_high < entry + risk:
                if j + 1 >= size:
                    break
                exit_price, exit_i, reason = open_[j + 1], j + 1, "TIME_STOP"
            # Batas horizon SWING. Beda dari time stop di atas: time stop membuang posisi
            # yang tidak bergerak, batas ini membuang posisi yang MASIH bergerak tapi
            # sudah terlalu lama dipegang. Tanpa ini "swing" cuma sebutan — posisi yang
            # menuju 3R bisa ditahan berbulan-bulan.
            elif max_holding_days and j - exec_i >= max_holding_days:
                if j + 1 >= size:
                    break
                exit_price, exit_i, reason = open_[j + 1], j + 1, "MAX_HOLD"

            if exit_i is None:
                # Belum ditutup: ambil TP1 parsial, lalu naikkan stop ke breakeven.
                # Urutan ini disengaja. Stop TIDAK dinaikkan pada bar yang sama dengan
                # sentuhan 1R — di dalam satu bar kita tidak tahu mana yang lebih dulu,
                # high atau low, dan menganggap yang menguntungkan duluan adalah cara
                # backtest berbohong.
                if lots_tp1 and not sold_lots and high[j] >= tp1 and not (
                    j > 0 and np.isfinite(close[j - 1]) and close[j - 1] > 0
                    and market.is_arb(float(tp1), close[j - 1], dates[j])
                ):
                    tp1_price = market.apply_slippage(float(tp1), "sell", fees, dates[j])
                    sold_value = market.net_sell_value(tp1_price, lots_tp1, fees)
                    sold_lots = lots_tp1
                if peak_high >= be_trigger and stop < entry:
                    stop = entry
                continue
            # ARB: posisi tidak bisa dijual hari itu — tunda ke bar berikutnya.
            if j > 0 and np.isfinite(close[j - 1]) and close[j - 1] > 0 and market.is_arb(
                float(exit_price), close[j - 1], dates[j]
            ):
                exit_i, exit_price, reason = None, None, ""
                continue
            break

        if exit_i is None:
            break

        exit_final = market.apply_slippage(float(exit_price), "sell", fees, dates[exit_i])
        rest = lots - sold_lots
        gross_in = market.net_buy_value(entry, lots, fees)
        gross_out = sold_value + market.net_sell_value(exit_final, rest, fees)
        pnl = gross_out - gross_in
        if sold_lots:
            # Satu Trade per posisi, harga keluar = rata-rata tertimbang. Memecahnya jadi
            # dua Trade akan menggandakan hitungan trade dan membuat win rate bisa diatur
            # hanya dengan memperbanyak tahap TP.
            exit_final = (tp1_price * sold_lots + exit_final * rest) / lots
            reason = f"TP1+{reason}"
        trades.append(
            Trade(
                ticker=ticker,
                strategy=strategy.name,
                entry_date=dates[exec_i],
                exit_date=dates[exit_i],
                entry_price=float(entry),
                exit_price=float(exit_final),
                lots=lots,
                r_multiple=round(pnl / (risk * lots * market.LOT), 3),
                pnl_rp=round(pnl, 0),
                pnl_pct=round(pnl / gross_in * 100, 2),
                exit_reason=reason,
            )
        )
        i = exit_i + 1

    return trades, unfilled


# --------------------------------------------------------------------------- metrik

def summarize(trades: list[Trade], unfilled_ara: int = 0, risk_pct: float = 0.01) -> dict:
    """Ringkas performa. `risk_pct` = risiko per trade, dipakai membangun kurva ekuitas.

    Drawdown dihitung dari kurva ekuitas hasil risk-per-trade tetap (equity *= 1 + R×risk),
    bukan dari kumulatif R mentah — kumulatif R tidak punya denominator sehingga
    persentasenya tak bermakna.
    """
    if not trades:
        return {"total_trades": 0, "unfilled_ara": unfilled_ara}

    ordered = sorted(trades, key=lambda t: t.exit_date)
    r = np.array([t.r_multiple for t in ordered], dtype=float)
    wins, losses = r[r > 0], r[r <= 0]
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())

    equity = np.cumprod(1 + r * risk_pct)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1

    holding = np.array([(t.exit_date - t.entry_date).days for t in ordered], dtype=float)
    span_years = max(
        (max(t.exit_date for t in ordered) - min(t.entry_date for t in ordered)).days / 365.25,
        1e-9,
    )
    trades_per_year = len(trades) / span_years
    sharpe = (
        float(r.mean() / r.std(ddof=1) * np.sqrt(trades_per_year))
        if len(r) > 1 and r.std(ddof=1) > 0
        else float("nan")
    )

    return {
        "total_trades": len(trades),
        "win_rate": round(len(wins) / len(r) * 100, 1),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "expectancy": round(float(r.mean()), 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_dd": round(float(drawdown.min()) * 100, 1) if len(drawdown) else 0.0,
        "sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "avg_holding_days": round(float(holding.mean()), 1),
        "max_consecutive_loss": _max_streak(r <= 0),
        "trades_per_year": round(trades_per_year, 1),
        "total_r": round(float(r.sum()), 2),
        "unfilled_ara": unfilled_ara,
    }


def _max_streak(flags: np.ndarray) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def bootstrap_pvalue(trades: list[Trade], iterations: int = 10000, seed: int = 0) -> float | None:
    """BT-3 dengan koreksi (REVIEW-01 S5): resampling per TANGGAL, bukan per trade.

    Banyak posisi dibuka di hari yang sama dan bergerak bersama pasar. Resampling
    per-trade menganggapnya independen dan menghasilkan p-value terlalu optimis.
    """
    if len(trades) < 10:
        return None
    buckets: dict[dt.date, list[float]] = {}
    for trade in trades:
        buckets.setdefault(trade.entry_date, []).append(trade.r_multiple)
    blocks = [np.array(v, dtype=float) for v in buckets.values()]
    observed = float(np.concatenate(blocks).mean())
    if observed <= 0:
        return 1.0

    rng = np.random.default_rng(seed)
    centered = [b - observed for b in blocks]  # H0: expectancy = 0
    count, n_blocks = 0, len(blocks)
    for _ in range(iterations):
        picked = rng.integers(0, n_blocks, n_blocks)
        sample = np.concatenate([centered[k] for k in picked])
        if sample.mean() >= observed:
            count += 1
    return round((count + 1) / (iterations + 1), 4)


def rolling_oos(
    panel: dict[str, pd.DataFrame],
    strategy: strat.Strategy,
    cfg,
    test_months: int = 6,
) -> Result:
    """Jalankan simulasi lalu laporkan per fold `test_months`.

    Karena tidak ada parameter yang di-fit, seluruh periode sebenarnya out-of-sample;
    pembagian fold dipakai untuk mengukur KONSISTENSI, bukan untuk memisahkan train/test.
    """
    all_trades: list[Trade] = []
    unfilled = 0
    exit_cfg = cfg.data["exit"]
    for ticker, frame in panel.items():
        trades, miss = simulate(ticker, frame, strategy, cfg,
                                exit_cfg["time_stop_days"],
                                partial=exit_cfg.get("partial_tp1", True),
                                breakeven_at_r=exit_cfg.get("breakeven_at_r", 0.0) or 0.0,
                                max_holding_days=exit_cfg.get("max_holding_days", 0) or 0)
        all_trades.extend(trades)
        unfilled += miss

    start = min((t.entry_date for t in all_trades), default=None)
    end = max((t.exit_date for t in all_trades), default=None)
    warnings = []
    if start:
        warn = market.regime_warning(start)
        if warn:
            warnings.append(warn)
    return Result(strategy.name, "rolling_oos", start, end, all_trades, unfilled, warnings)


def fold_consistency(trades: list[Trade], months: int = 6) -> list[dict]:
    """Bagi trade per periode `months` dan hitung total R tiap periode."""
    if not trades:
        return []
    frame = pd.DataFrame(
        {"date": [t.entry_date for t in trades], "r": [t.r_multiple for t in trades]}
    )
    frame["date"] = pd.to_datetime(frame["date"])
    grouped = frame.set_index("date").resample(f"{months}MS")["r"]
    return [
        {"periode": str(period.date()), "trades": int(len(values)), "total_r": round(float(values.sum()), 2)}
        for period, values in grouped
        if len(values)
    ]
