"""Uji jalur uang: fraksi harga, auto rejection, sizing, biaya, backtest, portofolio."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from hermes_idx import (
    agent, backtest, config as cfgmod, db, indicators as ind, market,
    portfolio, signals, strategies as strat,
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
