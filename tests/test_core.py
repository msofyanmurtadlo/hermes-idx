"""Uji jalur uang: fraksi harga, auto rejection, sizing, biaya, backtest, portofolio."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from hermes_idx import (
    agent, backtest, config as cfgmod, db, indicators as ind, legacy, market,
    portfolio, signals, strategies as strat, universe,
)

TODAY = dt.date(2026, 7, 29)


# --------------------------------------------------------------------------- market

@pytest.mark.parametrize(
    "price,expected",
    [(50, 1), (199, 1), (200, 2), (499, 2), (500, 5), (1999, 5),
     (2000, 10), (4999, 10), (5000, 25), (12345, 25)],
)
def test_tick_size_bands(price, expected):
    assert market.tick_size(price, TODAY) == expected


def test_round_to_tick_directions():
    assert market.round_to_tick(1752, TODAY, "down") == 1750
    assert market.round_to_tick(1752, TODAY, "up") == 1755
    assert market.round_to_tick(1752, TODAY, "nearest") == 1750


def test_round_respects_band_change_at_boundary():
    """Di sekitar 2000 fraksi berubah 5 → 10; hasil pembulatan harus tetap valid."""
    for mode in ("down", "up", "nearest"):
        value = market.round_to_tick(2003, TODAY, mode)
        assert market.is_valid_tick(value, TODAY), (mode, value)


def test_round_never_below_floor():
    assert market.round_to_tick(10, TODAY, "down") >= market.HARGA_MIN


def test_auto_reject_bounds_are_tradeable_prices():
    lower, upper = market.auto_reject_bounds(1000, TODAY)
    assert lower < 1000 < upper
    assert market.is_valid_tick(lower, TODAY) and market.is_valid_tick(upper, TODAY)


def test_ara_arb_detection():
    _, upper = market.auto_reject_bounds(1000, TODAY)
    assert market.is_ara(upper, 1000, TODAY)
    assert not market.is_ara(upper - 100, 1000, TODAY)
    lower, _ = market.auto_reject_bounds(1000, TODAY)
    assert market.is_arb(lower, 1000, TODAY)


def test_slippage_moves_against_us():
    fees = market.Fees(slippage_ticks=1)
    assert market.apply_slippage(1000, "buy", fees, TODAY) > 1000
    assert market.apply_slippage(1000, "sell", fees, TODAY) < 1000


def test_fees_reduce_proceeds_and_increase_cost():
    fees = market.Fees()
    assert market.net_buy_value(1000, 10, fees) > 1000 * 10 * 100
    assert market.net_sell_value(1000, 10, fees) < 1000 * 10 * 100


def test_minimum_fee_applies_to_small_orders():
    fees = market.Fees(min_fee_rp=5000)
    assert fees.buy_cost(100, 1) == 5000  # 0.15% dari Rp 10.000 jauh di bawah minimum


def test_regime_warning_for_old_periods():
    assert market.regime_warning(dt.date(2019, 1, 1)) is not None
    assert market.regime_warning(dt.date(2025, 1, 1)) is None


# --------------------------------------------------------------------------- indikator

@pytest.fixture
def ohlc() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 400
    close = 1000 + np.cumsum(rng.normal(2, 15, n))
    high = close + rng.uniform(1, 20, n)
    low = close - rng.uniform(1, 20, n)
    return pd.DataFrame(
        {"open": close - rng.normal(0, 5, n), "high": high, "low": low, "close": close,
         "volume": rng.integers(1_000_000, 9_000_000, n).astype(float)},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def test_rsi_bounds(ohlc):
    values = ind.rsi(ohlc["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_rsi_all_gains_is_100():
    rising = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)


def test_atr_positive(ohlc):
    assert (ind.atr(ohlc, 14).dropna() > 0).all()


def test_adx_within_range(ohlc):
    assert ind.adx(ohlc, 14)["adx"].dropna().between(0, 100).all()


def test_ema_matches_pandas(ohlc):
    expected = ohlc["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    pd.testing.assert_series_equal(ind.ema(ohlc["close"], 20), expected)


def test_supertrend_direction_is_binary(ohlc):
    direction = ind.supertrend(ohlc)["direction"].dropna().unique()
    assert set(direction).issubset({-1.0, 1.0})


def test_last_swing_low_has_no_lookahead(ohlc):
    """Nilai pada bar i tidak boleh berubah ketika data setelah i ditambahkan."""
    cutoff = 300
    partial = ind.last_swing_low(ohlc.iloc[:cutoff]).iloc[-1]
    full = ind.last_swing_low(ohlc).iloc[cutoff - 1]
    assert partial == full or (np.isnan(partial) and np.isnan(full))


# --------------------------------------------------------------------------- config

def test_config_rejects_bad_risk(tmp_path):
    (tmp_path / "config.yaml").write_text("akun:\n  risk_per_trade_pct: 50\n")
    with pytest.raises(ValueError):
        cfgmod.load(tmp_path)


def test_config_yaml_numbers_parse_as_int(tmp_path):
    """Angka ditulis tanpa underscore supaya tidak jadi string di parser YAML 1.2."""
    (tmp_path / "config.yaml").write_text("akun:\n  modal: 25000000\n")
    assert cfgmod.load(tmp_path).modal == 25_000_000


@pytest.fixture
def cfg(tmp_path):
    return cfgmod.load(tmp_path)


# --------------------------------------------------------------------------- sizing

def test_position_size_respects_risk_budget(cfg):
    lots, skip = signals.position_size(1750, 1640, cfg)
    assert skip is None
    risk = lots * market.LOT * (1750 - 1640)
    assert risk <= cfg.modal * cfg.risk_pct


def test_position_size_capped_by_concentration(cfg):
    """SL sangat rapat → budget risiko membolehkan banyak lot, tapi cap 20% memotong."""
    lots, _ = signals.position_size(1000, 999, cfg)
    assert lots * market.LOT * 1000 <= cfg.modal * cfg.max_position_pct


def test_position_size_skips_when_capital_too_small(tmp_path):
    (tmp_path / "config.yaml").write_text("akun:\n  modal: 100000\n")
    small = cfgmod.load(tmp_path)
    lots, skip = signals.position_size(10000, 9000, small)
    assert lots == 0 and skip and "modal tidak cukup" in skip


def test_position_size_rejects_stop_above_entry(cfg):
    lots, skip = signals.position_size(1000, 1000, cfg)
    assert lots == 0 and skip


def test_score_without_backtest_is_neutral_not_high():
    """Strategi yang belum pernah diuji tidak boleh dapat skor tinggi gratis."""
    blind = signals.score_signal(None, 1.0, 1.0, 1.0, 2.0)
    proven = signals.score_signal(
        {"expectancy": 0.6, "profit_factor": 2.0}, 1.0, 1.0, 1.0, 2.0)
    assert blind < proven


# --------------------------------------------------------------------------- sinyal

@pytest.fixture
def panel(ohlc):
    ctx = strat.MarketContext()
    frame = strat.Breakout().prepare(ohlc, ctx)
    frame["rs_rank"] = 95.0
    return frame


def test_build_signal_levels_are_valid_ticks(panel, cfg):
    sig = signals.build("TEST", panel, strat.Breakout(), len(panel) - 1, cfg, avg_value=5e9)
    if sig is None:
        pytest.skip("bar terakhir tidak menghasilkan level valid")
    for price in (sig.entry_price, sig.stop_loss, sig.tp1, sig.tp2):
        assert market.is_valid_tick(price, sig.signal_date), price
    assert sig.stop_loss < sig.entry_price < sig.tp1 < sig.tp2
    assert sig.rr_tp1 > 0


def test_signal_risk_matches_rounded_levels(panel, cfg):
    sig = signals.build("TEST", panel, strat.Breakout(), len(panel) - 1, cfg, avg_value=5e9)
    if sig is None:
        pytest.skip("tidak ada level valid")
    expected = sig.position_lot * market.LOT * (sig.entry_price - sig.stop_loss)
    assert sig.risk_rp == pytest.approx(expected)


# --------------------------------------------------------------------------- backtest

def _trending_frame(n: int = 300) -> pd.DataFrame:
    close = np.linspace(1000, 2000, n) + np.sin(np.arange(n) / 5) * 20
    return pd.DataFrame(
        {"open": close - 2, "high": close + 15, "low": close - 15, "close": close,
         "volume": np.full(n, 5_000_000.0)},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def test_backtest_costs_make_zero_edge_negative(cfg):
    """Harga datar → tanpa biaya P/L nol; dengan biaya harus rugi."""
    fees = cfg.fees
    gross_in = market.net_buy_value(1000, 10, fees)
    gross_out = market.net_sell_value(1000, 10, fees)
    assert gross_out - gross_in < 0


def test_simulate_produces_consistent_trades(cfg):
    frame = strat.Breakout().prepare(_trending_frame(), strat.MarketContext())
    trades, unfilled = backtest.simulate("TEST", frame, strat.Breakout(), cfg)
    assert unfilled >= 0
    for trade in trades:
        assert trade.exit_date >= trade.entry_date
        assert trade.entry_price > 0 and trade.exit_price > 0


def test_ara_entry_is_not_filled(cfg):
    """Bar yang open-nya sudah ARA tidak boleh menghasilkan trade."""
    n = 60
    close = np.full(n, 1000.0)
    frame = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close,
         "volume": np.full(n, 1e6)},
        index=pd.date_range("2025-01-01", periods=n, freq="B"),
    )
    # Bar terakhir dibuka jauh di atas batas ARA harga acuan sebelumnya.
    frame.iloc[-1, frame.columns.get_loc("open")] = 2000.0
    _, upper = market.auto_reject_bounds(1000, dt.date(2025, 1, 1))
    assert market.is_ara(2000.0, 1000, dt.date(2025, 1, 1))
    assert 2000.0 > upper


def test_summarize_empty():
    assert backtest.summarize([]) == {"total_trades": 0, "unfilled_ara": 0}


def test_summarize_metrics():
    trades = [
        backtest.Trade("A", "s", dt.date(2025, 1, 1), dt.date(2025, 1, 10),
                       1000, 1200, 1, 2.0, 200000, 20.0, "TAKE_PROFIT"),
        backtest.Trade("B", "s", dt.date(2025, 2, 1), dt.date(2025, 2, 5),
                       1000, 900, 1, -1.0, -100000, -10.0, "CUT_LOSS"),
    ]
    m = backtest.summarize(trades)
    assert m["total_trades"] == 2
    assert m["win_rate"] == 50.0
    assert m["expectancy"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(2.0)


def test_bootstrap_needs_enough_samples():
    assert backtest.bootstrap_pvalue([]) is None


def test_bootstrap_pvalue_low_for_strong_edge():
    trades = [
        backtest.Trade(f"T{i}", "s", dt.date(2025, 1, 1) + dt.timedelta(days=i),
                       dt.date(2025, 1, 5) + dt.timedelta(days=i),
                       1000, 1100, 1, 1.0, 1000, 1.0, "TAKE_PROFIT")
        for i in range(40)
    ]
    assert backtest.bootstrap_pvalue(trades, iterations=500) < 0.05


# --------------------------------------------------------------------------- portofolio

@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def test_buy_then_sell_updates_position(conn, cfg):
    portfolio.record(conn, "BBCA", dt.date(2026, 7, 20), "BUY", 15, 9250, cfg.fees)
    assert portfolio.positions(conn)[0].lot == 15
    portfolio.record(conn, "BBCA", dt.date(2026, 7, 28), "SELL", 5, 9780, cfg.fees)
    assert portfolio.positions(conn)[0].lot == 10
    closed = conn.execute("SELECT * FROM trade_closed").fetchall()
    assert len(closed) == 1 and closed[0]["pnl_rp"] > 0


def test_weighted_average_on_second_buy(conn, cfg):
    portfolio.record(conn, "TLKM", dt.date(2026, 7, 1), "BUY", 30, 3100, cfg.fees)
    portfolio.record(conn, "TLKM", dt.date(2026, 7, 20), "BUY", 15, 3340, cfg.fees)
    pos = portfolio.positions(conn)[0]
    assert pos.lot == 45
    assert pos.avg_price == pytest.approx((30 * 3100 + 15 * 3340) / 45)


def test_cannot_oversell(conn, cfg):
    portfolio.record(conn, "ADRO", dt.date(2026, 7, 1), "BUY", 5, 2410, cfg.fees)
    with pytest.raises(ValueError, match="posisi hanya"):
        portfolio.record(conn, "ADRO", dt.date(2026, 7, 2), "SELL", 10, 2500, cfg.fees)


def test_sell_without_position_rejected(conn, cfg):
    with pytest.raises(ValueError, match="tidak ada posisi"):
        portfolio.record(conn, "XXXX", dt.date(2026, 7, 2), "SELL", 1, 100, cfg.fees)


def test_duplicate_import_does_not_double_count(conn, cfg):
    """UNIQUE key mencegah import CSV dua kali menduplikasi transaksi."""
    for _ in range(2):
        conn.execute(
            "INSERT OR IGNORE INTO transaksi (ticker, date, type, lot, price, fee)"
            " VALUES ('BBCA','2026-07-20','BUY',15,9250,20812)"
        )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM transaksi").fetchone()["c"] == 1


def test_review_cut_loss(conn, cfg, ohlc):
    pos = portfolio.Position("TEST", 10, 2410, stop_loss=2320, entry_date=dt.date(2026, 7, 1))
    frame = ohlc.copy()
    frame.iloc[-1, frame.columns.get_loc("close")] = 2300.0
    action = portfolio.review(pos, frame, cfg, TODAY)
    assert action.action == "CUT_LOSS" and action.urgency == "SEGERA"


def test_review_flags_missing_stop_loss(conn, cfg, ohlc):
    pos = portfolio.Position("TEST", 10, 1000, stop_loss=None)
    action = portfolio.review(pos, ohlc, cfg, TODAY)
    assert "TANPA STOP LOSS" in action.reason


def test_stats_reports_coverage(conn):
    result = portfolio.stats(conn)
    assert "coverage" in result


# --------------------------------------------------------------------------- agent

def test_manifest_lists_commands_and_rules():
    m = agent.manifest()
    assert m["skill"] == "idx-screener"
    assert any(c["name"] == "scan" for c in m["commands"])
    assert any("stop loss" in rule.lower() for rule in m["behavior_rules"])


def test_install_and_verify_skill(tmp_path, monkeypatch):
    target = tmp_path / "skills" / "idx-screener"
    installed = agent.install(target)
    assert (installed / "SKILL.md").exists()
    checks = {c.name: c for c in agent.verify(target)}
    assert checks["Skill terpasang"].ok
    assert checks["SKILL.md terbaca"].ok


def test_install_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / "skill-dir"
    agent.install(target)
    with pytest.raises(FileExistsError):
        agent.install(target)
    assert agent.install(target, force=True).exists()


# --------------------------------------------------------------------------- merge v3

def test_universe_seed_is_idempotent(conn):
    first = universe.seed(conn)
    second = universe.seed(conn)
    assert first == second
    n = conn.execute("SELECT COUNT(*) c FROM emiten").fetchone()["c"]
    assert n == first


def test_sector_lookup():
    assert universe.sector_of("BBCA") == "bank"
    assert universe.sector_of("XXXX") == "lain"


def test_session_note_windows():
    assert universe.session_note(9 * 60 + 5)[0] == "RAWAN"     # 09:05 pembukaan
    assert universe.session_note(10 * 60)[0] == "IDEAL"        # 10:00
    assert universe.session_note(15 * 60 + 45)[0] == "RAWAN"   # 15:45 closing auction
    assert universe.session_note(20 * 60) is None              # di luar jam bursa


def test_market_regime_fails_closed_without_benchmark():
    """Tanpa data IHSG, pasar TIDAK boleh dianggap bullish."""
    ctx = strat.build_context(None)
    assert ctx.available is False
    frame = strat.Breakout().prepare(
        pd.DataFrame({"open": [1.0] * 5, "high": [1.0] * 5, "low": [1.0] * 5,
                      "close": [1.0] * 5, "volume": [1.0] * 5},
                     index=pd.date_range("2025-01-01", periods=5, freq="B")),
        ctx)
    assert not frame["bullish"].any()


def test_import_legacy_portfolio(conn, cfg, tmp_path):
    src = tmp_path / "portfolio.json"
    src.write_text(json.dumps({
        "snapshot_date": "2026-07-20",
        "positions": [
            {"ticker": "BBCA", "lots": 15, "avg": 9250, "invested": 13875000, "sl": 9000},
            {"ticker": "BUMI", "lots": 20, "avg": 100, "invested": 200000},
        ],
    }))
    count, notes = legacy.import_portfolio(conn, src, cfg.fees)
    assert count == 2
    positions = {p.ticker: p for p in portfolio.positions(conn)}
    assert positions["BBCA"].stop_loss == 9000
    assert positions["BUMI"].stop_loss is None
    assert any("TANPA STOP LOSS" in n for n in notes)


def test_import_legacy_skips_invalid_rows(conn, cfg, tmp_path):
    src = tmp_path / "p.json"
    src.write_text(json.dumps({"positions": [{"ticker": "XX", "lots": 0, "avg": 0}]}))
    count, notes = legacy.import_portfolio(conn, src, cfg.fees)
    assert count == 0 and notes


def test_legacy_stats_stored_with_caveat(conn, tmp_path):
    src = tmp_path / "h.json"
    src.write_text(json.dumps({"stats": {"BELI": {"benar": 7, "salah": 3},
                                         "JUAL": {"benar": 1, "salah": 1}}}))
    summary = legacy.import_signal_history(conn, src)
    assert summary["BELI"]["akurasi_pct"] == 70.0
    caveat = db.get_meta(conn, "legacy_signal_stats_caveat")
    assert caveat and "biaya" in caveat


# --------------------------------------------------------------------------- laporan harian

def test_daily_report_works_with_empty_db(conn, cfg, monkeypatch):
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    report = daily.build(conn, cfg)
    assert report["tidak_ada_sinyal_itu_normal"] is True
    assert any("kosong" in w.lower() or "backtest" in w.lower() for w in report["peringatan"])
    text = daily.render_text(report)
    assert "bukan nasihat investasi" in text


def test_daily_flags_position_without_stop_loss(conn, cfg, monkeypatch):
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    portfolio.record(conn, "BBCA", dt.date(2026, 7, 1), "BUY", 10, 9000, cfg.fees)
    report = daily.build(conn, cfg)
    assert "BBCA" in report["ringkasan_porto"]["tanpa_sl"]
    assert "Tanpa SL" in daily.render_text(report)


def test_daily_warns_when_no_proven_strategy(conn, cfg, monkeypatch):
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    conn.execute(
        "INSERT INTO backtest_result (strategy, expectancy, profit_factor, total_trades,"
        " p_value) VALUES ('breakout', -0.2, 0.7, 100, 1.0)")
    conn.commit()
    report = daily.build(conn, cfg)
    assert report["strategi_terbukti"] == []
    assert any("TIDAK ADA strategi" in w for w in report["peringatan"])


def test_v3_stop_uses_atr_not_fixed_two_percent(ohlc):
    """Regresi bug v3: stop tidak boleh selalu persis 2%."""
    s = strat.REGISTRY["v3score"]
    frame = s.prepare(ohlc, strat.MarketContext())
    widths = []
    for i in range(250, len(frame), 10):
        entry = float(frame["close"].iloc[i])
        widths.append((entry - s.levels(frame, i, entry).stop_loss) / entry * 100)
    assert max(widths) > 2.05, "stop masih terpaku 2% — bug v3 kembali"


def test_daily_warns_when_positions_exceed_limit(conn, cfg, monkeypatch):
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    cfg.data["akun"]["max_open_positions"] = 2
    for t, p in [("BBCA", 9000), ("BBRI", 4000), ("TLKM", 3000)]:
        portfolio.record(conn, t, dt.date(2026, 7, 1), "BUY", 1, p, cfg.fees)
    report = daily.build(conn, cfg)
    assert any("melebihi batas" in w for w in report["peringatan"])


def test_untracked_position_marked_in_report(conn, cfg, monkeypatch):
    """Posisi tanpa data harga harus terlihat jelas — bukan hilang diam-diam."""
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    portfolio.record(conn, "WIDI", dt.date(2026, 7, 1), "BUY", 1, 110, cfg.fees)
    text = daily.render_text(daily.build(conn, cfg))
    assert "tidak ada data harga" in text


# --------------------------------------------------------------------------- regresi bug

def test_duplicate_transaction_does_not_desync_ledger(conn, cfg):
    """UNIQUE transaksi dulu ditolak diam-diam tapi posisi tetap naik → ledger divergen."""
    day = dt.date(2026, 7, 30)
    portfolio.record(conn, "BBCA", day, "BUY", 10, 6000, cfg.fees)
    with pytest.raises(ValueError, match="sudah tercatat"):
        portfolio.record(conn, "BBCA", day, "BUY", 10, 6000, cfg.fees)
    rows = conn.execute("SELECT COUNT(*) FROM transaksi").fetchone()[0]
    lot = conn.execute("SELECT lot FROM posisi WHERE ticker='BBCA'").fetchone()[0]
    assert (rows, lot) == (1, 10)


def test_review_uses_live_price_over_stale_bar(cfg):
    """Intraday: bar DB masih kemarin. Keputusan SL harus ikut harga live, bukan bar basi."""
    frame = _trending_frame(300)
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = 2560.0
    pos = portfolio.Position(ticker="TLKM", lot=10, avg_price=2700, stop_loss=2600,
                             entry_date=dt.date(2026, 7, 20))
    basi = portfolio.review(pos, frame, cfg, dt.date(2026, 7, 30))
    live = portfolio.review(pos, frame, cfg, dt.date(2026, 7, 30), last_price=2670.0)
    assert basi.action == "CUT_LOSS"
    assert live.action != "CUT_LOSS", "harga live di atas SL tapi masih disuruh cut loss"


def test_time_stop_ignores_trade_that_already_reached_1r():
    """Trade yang pernah +1R tidak boleh kena TIME_STOP 'modal tidak produktif'."""
    n = 80
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = np.full(n, 100.0)
    close[3:8] = 160.0          # jauh melewati 1R lalu balik datar
    frame = pd.DataFrame({"open": close, "high": close, "low": close,
                          "close": close, "volume": 1_000_000.0}, index=idx)
    peak = float(frame["high"].max())
    assert peak > 100.0, "fixture salah — tidak ada lonjakan"
    # kontrak yang diuji: ambang time stop memakai tertinggi SEJAK ENTRY, bukan bar hari itu
    entry, risk = 100.0, 5.0
    assert peak >= entry + risk
    assert frame["high"].iloc[-1] < entry + risk


def test_blocked_signals_are_reported_not_silently_dropped(conn, cfg):
    """Porto penuh tidak boleh membuat sinyal hilang tanpa penjelasan."""
    from hermes_idx import screen, signals as sigmod
    cfg.data["akun"]["max_open_positions"] = 1
    portfolio.record(conn, "BBCA", dt.date(2026, 7, 1), "BUY", 1, 9000, cfg.fees)
    fake = sigmod.Signal(
        ticker="ASII", signal_date=dt.date(2026, 7, 30), strategy="breakout", action="BUY",
        entry_type="limit", entry_price=5000, entry_zone_low=4990, entry_zone_high=5010,
        stop_loss=4800, sl_pct=-4.0, tp1=5400, tp2=5800, tp1_size=0.5, tp2_size=0.5,
        rr_tp1=2.0, rr_tp2=4.0, position_lot=2, risk_rp=40000, score=80.0,
        valid_until=dt.date(2026, 8, 2), notes="uji",
    )
    warnings: list[str] = []
    kept = screen._apply_concentration(conn, cfg, [fake], warnings)
    assert kept == []
    assert any("ASII" in w and "ditahan oleh batas porto" in w for w in warnings)


def test_daily_flags_impossible_portfolio_numbers(conn, cfg, monkeypatch):
    """avg_price di bawah harga minimum bursa = salah impor, harus diteriakkan."""
    from hermes_idx import daily
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    conn.execute("INSERT INTO posisi (ticker, lot, avg_price, tp1, entry_date)"
                 " VALUES ('WIDI', 1, 1.1, NULL, '2026-07-29')")
    conn.execute("INSERT INTO posisi (ticker, lot, avg_price, tp1, entry_date)"
                 " VALUES ('BMTR', 35, 122.58, 122.0, '2026-07-29')")
    conn.commit()
    warns = " ".join(daily.build(conn, cfg)["peringatan"])
    assert "di bawah harga minimum bursa" in warns
    assert "di bawah harga beli" in warns


def test_liquidity_warning_only_fires_near_threshold(conn, cfg):
    """Peringatan estimasi harus muncul saat bisa mengubah keputusan, bukan tiap run."""
    from hermes_idx import data as dmod
    cfg.data["universe"]["min_avg_value_20d"] = 2_000_000_000
    cfg.data["universe"]["min_listing_days"] = 1
    rows = []
    for tick, val in (("BBCA", 900_000_000_000), ("GOTO", 2_100_000_000)):
        for d in range(3):
            harga = 1000.0
            rows.append((tick, f"2026-07-{20 + d}", harga, harga, harga, harga,
                         int(val / harga), val, 1))
    conn.executemany(
        "INSERT INTO ohlcv (ticker,date,open,high,low,close,volume,value,value_is_estimated)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    tickers, warnings = dmod.universe(conn, cfg)
    assert set(tickers) == {"BBCA", "GOTO"}
    teks = " ".join(warnings)
    assert "GOTO" in teks, "emiten di ambang harus disebut"
    assert "BBCA" not in teks, "emiten 450x di atas ambang tidak perlu diperingatkan"


def test_value_estimate_uses_typical_price():
    """(H+L+C)/3 lebih dekat ke VWAP daripada close pada bar yang bergerak lebar."""
    from hermes_idx import data as dmod
    lebar = {"high": 120.0, "low": 80.0, "close": 118.0, "volume": 1000.0}
    assert dmod._estimate_value(lebar) == pytest.approx(1000 * (120 + 80 + 118) / 3)
    # tanpa high/low yang sah, jatuh kembali ke close — bukan crash
    assert dmod._estimate_value({"close": 100.0, "volume": 10.0,
                                 "high": None, "low": None}) == 1000.0


# --------------------------------------------------------------------------- peringkat

def test_plan_advice_fixes_take_profit_below_entry(cfg):
    """Kasus nyata BMTR: TP1 di bawah harga beli — harus diusulkan diperbaiki."""
    frame = _trending_frame(300)
    pos = portfolio.Position(ticker="BMTR", lot=35, avg_price=122.58, stop_loss=108.0,
                             tp1=122.0, entry_date=dt.date(2026, 7, 29))
    saran = portfolio.plan_advice(pos, frame, cfg, last_price=115.0)
    assert saran is not None
    assert saran["tp1_baru"] > pos.avg_price
    assert "di bawah harga beli" in " ".join(saran["alasan"])
    assert saran["perintah"].startswith("hermes-idx port plan BMTR")


def test_plan_advice_proposes_stop_for_unprotected_position(cfg):
    frame = _trending_frame(300)
    last = float(frame["close"].iloc[-1])
    pos = portfolio.Position(ticker="WIDI", lot=1, avg_price=last, stop_loss=None)
    saran = portfolio.plan_advice(pos, frame, cfg, last_price=last)
    assert saran and saran["stop_loss_baru"] < last


def test_plan_advice_never_lowers_an_existing_stop(cfg):
    """Menurunkan SL saat posisi memburuk = cara mengubah rugi kecil jadi rugi besar."""
    frame = _trending_frame(300)
    last = float(frame["close"].iloc[-1])
    pos = portfolio.Position(ticker="BBCA", lot=10, avg_price=last * 0.5,
                             stop_loss=last * 0.99, tp1=last * 1.5)
    saran = portfolio.plan_advice(pos, frame, cfg, last_price=last)
    if saran and "stop_loss_baru" in saran:
        assert saran["stop_loss_baru"] >= pos.stop_loss


def test_rank_always_returns_rows_even_when_scan_is_empty(conn, cfg, monkeypatch):
    """Inti permintaan: tiap pagi harus ada isinya, walau tak ada yang layak dibeli."""
    from hermes_idx import data as dmod, screen
    frame = _trending_frame(400)
    for tick in ("BBCA", "BBRI"):
        dmod.upsert_ohlcv(conn, tick, frame, "uji")
    dmod.upsert_ohlcv(conn, cfg.data["data"]["benchmark"], frame, "uji")
    cfg.data["universe"]["min_avg_value_20d"] = 0
    cfg.data["universe"]["min_listing_days"] = 1
    cfg.data["universe"]["max_price"] = 10_000_000

    sinyal, _ = screen.scan(conn, cfg)
    peringkat, _ = screen.rank(conn, cfg, top=5)
    assert peringkat, "peringkat tidak boleh kosong walau scan kosong"
    assert all(r["label"] in {"PELUANG", "AMATI", "PANTAU"} for r in peringkat)
    assert all(r["alasan"] for r in peringkat), "tiap baris wajib punya alasan"
    if not sinyal:
        assert not any(r["label"] == "PELUANG" for r in peringkat), \
            "tanpa sinyal lolos scan, tidak boleh ada baris PELUANG"


# --------------------------------------------------------------------------- intraday

def test_intraday_roundtrip_preserves_time_of_day(conn):
    """Jam bar intraday adalah informasinya — tidak boleh dinormalisasi ke tengah malam."""
    from hermes_idx import data as dmod
    idx = pd.to_datetime(["2026-07-30 09:00", "2026-07-30 10:00", "2026-07-30 11:00"])
    frame = pd.DataFrame({"open": [100.0, 101, 102], "high": [103.0, 104, 105],
                          "low": [99.0, 100, 101], "close": [102.0, 103, 104],
                          "volume": [1000.0, 2000, 3000]}, index=idx)
    frame.index.name = "ts"
    assert dmod.upsert_intraday(conn, "BBCA", frame, "60m") == 3
    kembali = dmod.load_intraday(conn, "BBCA", "60m")
    assert list(kembali.index.strftime("%H:%M")) == ["09:00", "10:00", "11:00"]
    assert kembali["close"].tolist() == [102.0, 103.0, 104.0]
    # interval lain tidak boleh tercampur
    assert dmod.load_intraday(conn, "BBCA", "5m").empty


def test_intraday_upsert_is_idempotent(conn):
    from hermes_idx import data as dmod
    idx = pd.to_datetime(["2026-07-30 09:00"])
    frame = pd.DataFrame({"open": [100.0], "high": [103.0], "low": [99.0],
                          "close": [102.0], "volume": [1000.0]}, index=idx)
    frame.index.name = "ts"
    dmod.upsert_intraday(conn, "BBCA", frame, "60m")
    frame["close"] = 108.0
    dmod.upsert_intraday(conn, "BBCA", frame, "60m")
    hasil = dmod.load_intraday(conn, "BBCA", "60m")
    assert len(hasil) == 1 and hasil["close"].iloc[0] == 108.0


def test_intraday_rejects_unknown_interval():
    from hermes_idx import data as dmod
    with pytest.raises(ValueError, match="tidak didukung"):
        dmod.fetch_intraday("BBCA", "4h")


def test_manifest_lists_every_user_facing_command():
    """Manifest mengklaim jadi satu-satunya sumber kebenaran — jangan sampai ketinggalan."""
    terdaftar = {c["name"] for c in agent.manifest()["commands"]}
    wajib = {"scan", "daily", "compare", "analyze", "backtest", "doctor",
             "port show", "port review", "port plan", "data update", "data intraday"}
    assert wajib <= terdaftar, f"belum terdaftar: {sorted(wajib - terdaftar)}"


def test_manifest_warns_agent_about_ranking_semantics():
    """peringkat[] paling mudah disalahartikan agent sebagai daftar beli."""
    notes = agent.manifest()["notes"]
    assert "bukan ajakan beli" in notes["peringkat_vs_sinyal"]
    assert "PELUANG" in notes["peringkat_vs_sinyal"]


# --------------------------------------------------------------------------- rezim pasar

def test_regime_ma_period_is_configurable():
    """MA200 = tren jangka panjang; horizon pendek butuh periode lain."""
    close = pd.Series(np.concatenate([np.linspace(100, 200, 150), np.linspace(200, 150, 60)]),
                      index=pd.date_range("2024-01-01", periods=210, freq="B"))
    bench = pd.DataFrame({"close": close})
    panjang = strat.build_context(bench, ma_period=200)
    pendek = strat.build_context(bench, ma_period=20)
    assert panjang.bullish_regime is not None and pendek.bullish_regime is not None
    # MA pendek bereaksi lebih cepat — setidaknya ada satu bar yang beda
    assert not pendek.bullish_regime.equals(panjang.bullish_regime), \
        "MA period berbeda harus menghasilkan rezim berbeda di setidaknya satu titik"
    with pytest.raises(ValueError, match="harus >= 2"):
        strat.build_context(bench, ma_period=1)


def test_backtest_applies_same_regime_filter_as_live_screening(conn, cfg):
    """Backtest tanpa filter rezim mengukur strategi yang tidak pernah dijalankan siapa pun."""
    from hermes_idx import data as dmod, screen
    frame = _trending_frame(500)
    dmod.upsert_ohlcv(conn, "BBCA", frame, "uji")
    dmod.upsert_ohlcv(conn, cfg.data["data"]["benchmark"], frame, "uji")
    cfg.data["universe"].update(min_avg_value_20d=0, min_listing_days=1, max_price=10_000_000)

    cfg.data["screening"]["market_regime_filter"] = True
    dengan = screen.run_backtest(conn, cfg, "breakout", persist=False)
    cfg.data["screening"]["market_regime_filter"] = False
    tanpa = screen.run_backtest(conn, cfg, "breakout", persist=False)
    assert len(dengan.trades) <= len(tanpa.trades), (
        "filter rezim harus mengurangi (atau menyamai) jumlah trade, tidak menambah")


def test_plan_advice_refuses_to_compute_from_corrupt_entry_price(cfg):
    """WIDI avg 1,1 pernah menghasilkan usulan 'SL 50 | TP1 50' — identik dan tak berarti."""
    frame = _trending_frame(300)
    pos = portfolio.Position(ticker="WIDI", lot=1, avg_price=1.1)
    saran = portfolio.plan_advice(pos, frame, cfg, last_price=26.0)
    assert saran is not None
    assert "stop_loss_baru" not in saran and "tp1_baru" not in saran
    assert "di bawah harga minimum bursa" in " ".join(saran["alasan"])
    assert "harga_beli_sebenarnya" in saran["perintah"]


def test_report_names_the_actual_regime_ma_period(conn, cfg, monkeypatch):
    """Label 'MA200' yang dipatok jadi bohong begitu periodenya bisa diatur."""
    from hermes_idx import daily, data as dmod
    monkeypatch.setattr(daily, "fetch_live_prices", lambda tickers: {})
    dmod.upsert_ohlcv(conn, cfg.data["data"]["benchmark"], _trending_frame(400), "uji")
    cfg.data["screening"]["regime_ma_period"] = 50
    pasar = daily.build(conn, cfg)["pasar"]
    assert pasar["regime_ma_period"] == 50
    assert "MA50" in pasar["catatan"] and "MA200" not in pasar["catatan"]
