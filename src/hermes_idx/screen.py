"""Pipeline screening: universe → indikator → strategi → sinyal berskor."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import backtest, data, signals, strategies as strat, universe


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
    screening = cfg.data["screening"]
    return strat.build_context(
        benchmark if not benchmark.empty else None,
        ma_period=int(screening.get("regime_ma_period", 200)),
        below_days=int(screening.get("regime_below_days", 10)),
    )


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
    if not ctx.available:
        warnings.append(
            "Data IHSG tidak tersedia — rezim pasar tidak bisa dinilai. Semua sinyal BELI "
            "ditahan (fail-closed). Jalankan `data update` untuk mengambil ^JKSE."
        )
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
    found = _apply_concentration(conn, cfg, found, warnings)
    return (found[:top] if top else found), warnings


def rank(conn, cfg, top: int = 5, as_of: dt.date | None = None
         ) -> tuple[list[dict], list[str]]:
    """Peringkat SELURUH universe, bukan hanya yang lolos ambang.

    `scan()` menjawab "mana yang boleh dibeli" dan sering mengembalikan daftar kosong —
    di pasar bearish itu 73% hari (diukur pada 120 hari bursa terakhir). Fungsi ini
    menjawab pertanyaan berbeda: "kalau harus memilih hari ini, mana yang paling siap?"
    Jadi selalu ada isi, tapi tiap baris diberi label yang tidak menyamarkan keadaan:

      PELUANG — trigger nyala, rezim bullish, skor lolos ambang, slot porto ada.
      AMATI   — setup terbentuk tapi ada yang mengganjal (rezim/skor/slot/sudah dipegang).
      PANTAU  — belum ada trigger sama sekali; level di bawahnya sekadar ancang-ancang.

    Peringkat BUKAN rekomendasi beli. Selama `compare` belum menemukan strategi dengan
    expectancy positif, PELUANG pun tetap belum terbukti menguntungkan.
    """
    tickers, warnings = data.universe(conn, cfg)
    if not tickers:
        return [], warnings + ["Universe kosong — jalankan `hermes-idx data update` dulu."]

    ctx = context(conn, cfg)
    known_edges = edges(conn)
    avg_values = _avg_values(conn)
    min_score = cfg.data["screening"]["min_score"]
    held = {r["ticker"] for r in conn.execute("SELECT ticker FROM posisi WHERE lot > 0")}
    max_open = int(cfg.data["akun"]["max_open_positions"])
    slots = max_open - len(held)

    best: dict[str, dict] = {}
    for strategy in strat.get(cfg.data["screening"]["strategies"]):
        panel = load_panel(conn, tickers, strategy, ctx)
        for ticker, frame in panel.items():
            if as_of is not None:
                frame = frame[frame.index.date <= as_of]
                if frame.empty:
                    continue
            i = len(frame) - 1
            triggered = bool(strategy.entry_signal(frame).iloc[i])
            signal = signals.build(ticker, frame, strategy, i, cfg,
                                   edge=known_edges.get(strategy.name),
                                   avg_value=avg_values.get(ticker, 0.0))
            if signal is None:
                continue
            bullish = bool(frame["bullish"].iloc[i])
            if not triggered:
                label, alasan = "PANTAU", "belum ada trigger entry"
            elif ticker in held:
                label, alasan = "AMATI", "sudah dipegang"
            elif not bullish and cfg.data["screening"]["market_regime_filter"]:
                label, alasan = "AMATI", "rezim IHSG bearish"
            elif signal.skip_reason:
                label, alasan = "AMATI", signal.skip_reason
            elif signal.score < min_score:
                label, alasan = "AMATI", f"skor {signal.score:.0f} < ambang {min_score:.0f}"
            elif slots <= 0:
                label, alasan = "AMATI", f"slot porto penuh ({len(held)}/{max_open})"
            else:
                label, alasan = "PELUANG", "lolos seluruh ambang"

            row = signal.as_row()
            row.update(label=label, alasan=alasan, triggered=triggered,
                       sektor=universe.sector_of(ticker))
            # Satu baris per emiten: strategi dengan skor tertinggi yang mewakilinya.
            # Trigger selalu menang atas non-trigger, berapa pun skornya — setup nyata
            # lebih berarti daripada ancang-ancang berskor tinggi.
            prev = best.get(ticker)
            if prev is None or (triggered, signal.score) > (prev["triggered"], prev["score"]):
                best[ticker] = row

    ranked = sorted(best.values(), key=lambda r: (r["triggered"], r["score"]), reverse=True)
    return ranked[:top] if top else ranked, warnings


def _apply_concentration(conn, cfg, found, warnings):
    """Batas jumlah posisi & per sektor — diambil dari aturan script v3.

    Config `max_open_positions` dan `max_per_sector` sebelumnya ada tapi tidak pernah
    ditegakkan di mana pun. Sinyal yang terpotong tidak dibuang diam-diam: alasannya
    ditulis ke `skip_reason` supaya tetap terlihat user.
    """
    max_open = int(cfg.data["akun"]["max_open_positions"])
    max_sector = int(cfg.data["akun"].get("max_per_sector", 2))

    held = conn.execute("SELECT ticker FROM posisi WHERE lot > 0").fetchall()
    held_tickers = {r["ticker"] for r in held}
    sector_count: dict[str, int] = {}
    for ticker in held_tickers:
        sector = universe.sector_of(ticker)
        sector_count[sector] = sector_count.get(sector, 0) + 1

    slots = max_open - len(held_tickers)
    if slots <= 0 and found:
        warnings.append(f"Posisi terbuka sudah {len(held_tickers)}/{max_open} — "
                        f"semua sinyal baru ditahan.")

    kept = []
    for sig in found:
        if sig.ticker in held_tickers:
            sig.skip_reason = "sudah dipegang"
            continue
        sector = universe.sector_of(sig.ticker)
        if slots <= 0:
            sig.skip_reason = f"slot penuh ({max_open}/{max_open})"
            continue
        if sector_count.get(sector, 0) >= max_sector:
            sig.skip_reason = f"sektor {sector} sudah {sector_count[sector]}/{max_sector}"
            continue
        kept.append(sig)
        slots -= 1
        sector_count[sector] = sector_count.get(sector, 0) + 1

    # Docstring di atas menjanjikan sinyal terpotong "tidak dibuang diam-diam", tapi dulu
    # fungsi ini hanya mengembalikan `kept` sehingga `skip_reason` tak pernah sampai ke
    # user. Di HP dengan porto penuh (6/6) artinya SEMUA sinyal hilang tanpa penjelasan.
    dropped = [s for s in found if s.skip_reason]
    if dropped:
        warnings.append(
            "Sinyal berikut ditahan oleh batas porto, bukan karena setup-nya jelek: "
            + ", ".join(f"{s.ticker} (skor {s.score:.0f}, {s.skip_reason})" for s in dropped)
        )
    return kept


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
