"""Pipeline screening: universe → indikator → strategi → sinyal berskor."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import backtest, data, signals, strategies as strat


def load_panel(conn, tickers: list[str], strategy, ctx, min_bars: int = 250
               ) -> dict[str, pd.DataFrame]:
    panel: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        frame = data.load_ohlcv(conn, ticker)
        if frame.empty or len(frame) < min_bars:
            continue
        panel[ticker] = strategy.prepare(frame, ctx)
    signals.add_rs_rank(panel)
    return panel


def context(conn, cfg) -> strat.MarketContext:
    benchmark = data.load_ohlcv(conn, cfg.data["data"]["benchmark"])
    return strat.build_context(benchmark if not benchmark.empty else None)


def edges(conn) -> dict[str, dict]:
    """Ringkasan backtest terakhir per strategi, untuk komponen edge di SB-5.

    Hanya memakai hasil yang SUDAH tersimpan — skor tidak pernah memakai backtest yang
    dijalankan atas periode yang mencakup sinyal itu sendiri (REVIEW-01 B3).
    """
    rows = conn.execute(
        "SELECT strategy, expectancy, profit_factor, total_trades, p_value, period_end"
        " FROM backtest_result WHERE id IN"
        " (SELECT MAX(id) FROM backtest_result GROUP BY strategy)"
    ).fetchall()
    return {r["strategy"]: dict(r) for r in rows}


def scan(conn, cfg, strategy_names: list[str] | None = None, min_score: float | None = None,
         top: int | None = None, as_of: dt.date | None = None) -> tuple[list[signals.Signal], list[str]]:
    """Jalankan screening penuh. Kembalikan (sinyal_terurut, peringatan)."""
    tickers, warnings = data.universe(conn, cfg)
    if not tickers:
        return [], warnings + ["Universe kosong — jalankan `hermes-idx data update` dulu."]

    age = data.data_age_days(conn)
    stale = cfg.data["data"]["stale_after_days"]
    if age is not None and age > stale:
        warnings.append(f"Data terakhir berumur {age} hari (> {stale}). Sinyal mungkin basi.")

    ctx = context(conn, cfg)
    known_edges = edges(conn)
    min_score = cfg.data["screening"]["min_score"] if min_score is None else min_score
    avg_values = _avg_values(conn)

    found: list[signals.Signal] = []
    for strategy in strat.get(strategy_names or cfg.data["screening"]["strategies"]):
        panel = load_panel(conn, tickers, strategy, ctx)
        for ticker, frame in panel.items():
            if as_of is not None:
                frame = frame[frame.index.date <= as_of]
                if frame.empty:
                    continue
            triggered = strategy.entry_signal(frame)
            if not bool(triggered.iloc[-1]):
                continue
            if cfg.data["screening"]["market_regime_filter"] and not bool(frame["bullish"].iloc[-1]):
                continue
            signal = signals.build(
                ticker, frame, strategy, len(frame) - 1, cfg,
                edge=known_edges.get(strategy.name),
                avg_value=avg_values.get(ticker, 0.0),
            )
            if signal and signal.score >= min_score and signal.skip_reason is None:
                found.append(signal)

    found.sort(key=lambda s: s.score, reverse=True)
    return (found[:top] if top else found), warnings


def _avg_values(conn) -> dict[str, float]:
    rows = conn.execute(
        "SELECT ticker, AVG(value) AS v FROM ohlcv"
        " WHERE date > (SELECT date(MAX(date), '-30 day') FROM ohlcv) GROUP BY ticker"
    ).fetchall()
    return {r["ticker"]: (r["v"] or 0.0) for r in rows}


def run_backtest(conn, cfg, strategy_name: str, persist: bool = True) -> backtest.Result:
    strategy = strat.get([strategy_name])[0]
    tickers, warnings = data.universe(conn, cfg)
    ctx = context(conn, cfg)
    panel = load_panel(conn, tickers, strategy, ctx)
    result = backtest.rolling_oos(panel, strategy, cfg)
    result.warnings.extend(warnings)

    if persist and result.trades:
        metrics = result.metrics(cfg.risk_pct)
        conn.execute(
            "INSERT INTO backtest_result (strategy, run_date, period_start, period_end, method,"
            " total_trades, win_rate, expectancy, profit_factor, max_dd, sharpe, p_value,"
            " unfilled_ara, warnings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (strategy_name, dt.date.today().isoformat(),
             result.period_start.isoformat() if result.period_start else None,
             result.period_end.isoformat() if result.period_end else None,
             result.method, metrics["total_trades"], metrics["win_rate"], metrics["expectancy"],
             metrics["profit_factor"], metrics["max_dd"], metrics["sharpe"],
             backtest.bootstrap_pvalue(result.trades), result.unfilled_ara,
             " | ".join(result.warnings)),
        )
        conn.commit()
    return result
