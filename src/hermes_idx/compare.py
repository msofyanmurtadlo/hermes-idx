"""Adu strategi head-to-head — memilih pemenang lewat bukti, bukan selera.

Yang dipakai untuk memeringkat adalah **expectancy** dan **profit factor**, bukan win
rate. Alasannya ada di PRD §3.2: win rate mudah dinaikkan dengan memasang TP sangat dekat
dan SL sangat lebar, dan hasilnya bisa 85% menang dengan expectancy negatif.

Semua kandidat diuji pada universe, periode, dan biaya yang persis sama, lewat engine
yang sama — termasuk ARA/ARB, slippage, fee beli/jual, dan eksekusi di open T+1.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import backtest as bt, screen, strategies as strat

RANK_WEIGHTS = {
    "expectancy": 0.40,
    "profit_factor": 0.25,
    "max_dd": 0.15,
    "consistency": 0.10,
    "sharpe": 0.10,
}
"""BT-5. Win rate sengaja TIDAK ada bobotnya."""

MIN_TRADES_FOR_CLAIM = 30
"""Di bawah ini, perbedaan antar strategi tidak bisa dibedakan dari keberuntungan."""


@dataclass
class Entry:
    strategy: str
    label: str
    metrics: dict
    p_value: float | None
    folds: list[dict]
    composite: float = 0.0
    verdict: str = ""

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy, "label": self.label, "metrics": self.metrics,
            "p_value": self.p_value, "composite": round(self.composite, 3),
            "verdict": self.verdict, "folds": self.folds,
        }


def _norm(values: list[float], value: float, higher_is_better: bool = True) -> float:
    finite = [v for v in values if v is not None and v == v]
    if len(finite) < 2 or value is None or value != value:
        return 0.5
    low, high = min(finite), max(finite)
    if high <= low:
        return 0.5
    scaled = (value - low) / (high - low)
    return scaled if higher_is_better else 1 - scaled


def _consistency(folds: list[dict]) -> float:
    if not folds:
        return 0.0
    return sum(1 for f in folds if f["total_r"] > 0) / len(folds)


def run(conn, cfg, names: list[str] | None = None) -> list[Entry]:
    """Jalankan semua kandidat pada kondisi identik, peringkat dengan skor komposit."""
    candidates = strat.get(names) if names else list(strat.REGISTRY.values())
    entries: list[Entry] = []

    for strategy in candidates:
        # persist=True: `compare` adalah lari-bukti kanonik. Kalau hasilnya tidak
        # disimpan, `daily` dan skor sinyal tidak pernah tahu strategi mana yang punya
        # edge — persis yang terjadi saat dicoba di perangkat sungguhan.
        result = screen.run_backtest(conn, cfg, strategy.name, persist=True)
        metrics = result.metrics(cfg.risk_pct)
        folds = bt.fold_consistency(result.trades)
        entries.append(Entry(
            strategy=strategy.name,
            label=getattr(strategy, "label", strategy.name),
            metrics=metrics,
            p_value=bt.bootstrap_pvalue(result.trades),
            folds=folds,
        ))

    traded = [e for e in entries if e.metrics.get("total_trades", 0) > 0]
    if traded:
        exps = [e.metrics.get("expectancy") for e in traded]
        pfs = [_finite_pf(e.metrics.get("profit_factor")) for e in traded]
        dds = [e.metrics.get("max_dd") for e in traded]
        shs = [e.metrics.get("sharpe") for e in traded]
        cons = [_consistency(e.folds) for e in traded]

        for entry in traded:
            entry.composite = (
                RANK_WEIGHTS["expectancy"] * _norm(exps, entry.metrics.get("expectancy"))
                + RANK_WEIGHTS["profit_factor"] * _norm(pfs, _finite_pf(entry.metrics.get("profit_factor")))
                + RANK_WEIGHTS["max_dd"] * _norm(dds, entry.metrics.get("max_dd"))
                + RANK_WEIGHTS["consistency"] * _norm(cons, _consistency(entry.folds))
                + RANK_WEIGHTS["sharpe"] * _norm(shs, entry.metrics.get("sharpe"))
            )
            entry.verdict = _verdict(entry)

    for entry in entries:
        if entry.metrics.get("total_trades", 0) == 0:
            entry.verdict = "TIDAK ADA TRADE — tidak bisa dinilai"

    entries.sort(key=lambda e: (e.metrics.get("total_trades", 0) > 0, e.composite), reverse=True)
    return entries


def _finite_pf(value) -> float | None:
    """Profit factor tak terhingga (nol kerugian) tidak bisa dinormalisasi; batasi."""
    if value is None or value != value:
        return None
    return min(float(value), 10.0)


def _verdict(entry: Entry) -> str:
    trades = entry.metrics.get("total_trades", 0)
    exp = entry.metrics.get("expectancy", 0)
    if trades < MIN_TRADES_FOR_CLAIM:
        return f"SAMPEL KECIL ({trades} trade) — belum bisa disimpulkan"
    if exp is not None and exp <= 0:
        return "EXPECTANCY NEGATIF — merugi setelah biaya"
    if entry.p_value is not None and entry.p_value > 0.05:
        return f"TIDAK SIGNIFIKAN (p={entry.p_value}) — bisa jadi kebetulan"
    return f"ADA EDGE (p={entry.p_value})"


def recommendation(entries: list[Entry]) -> tuple[str | None, str]:
    """Kembalikan (strategi_pemenang, penjelasan). None bila tidak ada yang layak."""
    usable = [
        e for e in entries
        if e.metrics.get("total_trades", 0) >= MIN_TRADES_FOR_CLAIM
        and (e.metrics.get("expectancy") or 0) > 0
    ]
    if not usable:
        return None, (
            "Tidak ada strategi yang lolos ambang minimum (>= "
            f"{MIN_TRADES_FOR_CLAIM} trade DAN expectancy positif setelah biaya). "
            "Jangan pakai sinyal apa pun dari sini untuk uang sungguhan sampai ada "
            "yang lolos. Perbanyak data historis, lalu jalankan ulang."
        )
    best = usable[0]
    significant = [e for e in usable if e.p_value is not None and e.p_value <= 0.05]
    if not significant:
        return best.strategy, (
            f"'{best.strategy}' peringkat teratas, tapi TIDAK ADA satu pun kandidat yang "
            f"signifikan secara statistik (semua p > 0.05). Perbedaan peringkat ini belum "
            f"bisa dibedakan dari keberuntungan."
        )
    return best.strategy, (
        f"'{best.strategy}' menang: expectancy {best.metrics['expectancy']:+.3f}R, "
        f"profit factor {best.metrics['profit_factor']}, "
        f"{best.metrics['total_trades']} trade, p={best.p_value}."
    )
