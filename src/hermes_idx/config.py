"""Konfigurasi pengguna.

Catatan: angka di YAML ditulis TANPA pemisah underscore (`50000000`, bukan `50_000_000`).
PyYAML (YAML 1.1) menerima underscore, tapi parser YAML 1.2 memparsenya sebagai string —
jebakan portabilitas yang tidak perlu. Lihat REVIEW-01 S4.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .market import Fees

DEFAULT_HOME = Path.home() / ".hermes-idx"

DEFAULTS: dict[str, Any] = {
    "akun": {
        "modal": 50000000,
        "risk_per_trade_pct": 1.0,
        "max_position_pct": 20.0,
        "max_open_positions": 4,
        "max_per_sector": 2,
        "max_sector_exposure_pct": 40.0,
    },
    "biaya": {
        "fee_beli_pct": 0.15,
        "fee_jual_pct": 0.25,
        "fee_minimum_rp": 0,
        "slippage_tick": 1,
    },
    "screening": {
        "strategies": ["breakout", "pullback", "momentum_rs", "mean_reversion"],
        "min_score": 55,
        "max_results": 15,
        "market_regime_filter": True,
        # Panjang MA rezim harus cocok dengan horizon dagang. 200 = tren jangka panjang
        # (Fidelity menyebutnya alat untuk "long-term trends"); 50 mengikuti pergerakan
        # harga terkini; 20 untuk horizon sangat pendek.
        #
        # Default 50, bukan 200, karena diukur — bukan karena lebih pendek terdengar
        # cocok untuk trading harian. Pada data ini (lihat docs/BUKTI-01.md):
        #   v3score        MA200 -0,194 → MA50 -0,124  (membaik, dan trade 523 → 651)
        #   breakout       MA200 -0,224 → MA50 -0,219  (praktis sama)
        #   mean_reversion MA200 -0,082 → MA50 -0,182  (MEMBURUK 2,2×)
        #
        # Jadi 50 menang untuk dua dari tiga, tapi `mean_reversion` jelas lebih suka 200.
        # Kalau strategi itu yang dipakai, kembalikan ke 200. Semua angka tetap NEGATIF —
        # memperpendek MA memperbanyak sinyal, tidak menciptakan edge.
        "regime_ma_period": 50,
        "regime_below_days": 10,
        "valid_days": 3,
    },
    "universe": {
        "min_avg_value_20d": 2000000000,
        "min_price": 50,
        "max_price": 50000,
        "min_listing_days": 250,
        "exclude_boards": ["Pemantauan Khusus"],
        # Screening HANYA emiten bluechip kurasi (universe.py SECTORS, ~63 emiten).
        # Posisi porto yang non-bluechip TETAP dipantau SL/TP-nya lewat `posisi`,
        # hanya tidak pernah muncul sebagai kandidat sinyal beli baru.
        "bluechip_only": True,
    },
    "exit": {
        "time_stop_days": 20,
        # Horizon SWING. Tanpa batas ini posisi rata-rata dipegang 42,6 hari — itu
        # position trading, bukan swing. 0 = tanpa batas.
        "max_holding_days": 12,
        "partial_tp1": False,
        "breakeven_at_r": 0.0,
        "trailing_activate_at_r": 1.5,
        "trailing_method": "chandelier",
        "alert_near_sl_pct": 2.0,
    },
    "data": {
        "benchmark": "^JKSE",
        "history_years": 5,
        "rate_limit_per_sec": 5.0,
        "stale_after_days": 2,
    },
    "mcp": {
        # Server MCP TradingView — sumber harga cadangan (fallback) untuk laporan
        # harian bila TradingView Scanner utama gagal. Kosong = fitur mati.
        "command": "",
        "timeout": 60,
    },
}


@dataclass
class Config:
    data: dict[str, Any]
    path: Path

    # -- akses cepat yang dipakai di banyak tempat -------------------------------
    @property
    def modal(self) -> float:
        return float(self.data["akun"]["modal"])

    @property
    def risk_pct(self) -> float:
        return float(self.data["akun"]["risk_per_trade_pct"]) / 100

    @property
    def max_position_pct(self) -> float:
        return float(self.data["akun"]["max_position_pct"]) / 100

    @property
    def fees(self) -> Fees:
        b = self.data["biaya"]
        return Fees(
            buy_pct=float(b["fee_beli_pct"]) / 100,
            sell_pct=float(b["fee_jual_pct"]) / 100,
            min_fee_rp=float(b.get("fee_minimum_rp", 0)),
            slippage_ticks=int(b["slippage_tick"]),
        )

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(home: Path | None = None) -> Config:
    home = Path(home) if home else DEFAULT_HOME
    path = home / "config.yaml"
    user: dict = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise ValueError(f"{path} bukan mapping YAML yang valid")
        user = loaded or {}
    cfg = Config(data=_deep_merge(DEFAULTS, user), path=path)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Validasi di batas sistem — config salah ketik tidak boleh diam-diam lolos."""
    if cfg.modal <= 0:
        raise ValueError("akun.modal harus > 0")
    if not 0 < cfg.risk_pct <= 0.1:
        raise ValueError("akun.risk_per_trade_pct harus di antara 0 dan 10")
    if not 0 < cfg.max_position_pct <= 1:
        raise ValueError("akun.max_position_pct harus di antara 0 dan 100")
    for key in ("fee_beli_pct", "fee_jual_pct"):
        if not 0 <= float(cfg.data["biaya"][key]) < 5:
            raise ValueError(f"biaya.{key} di luar rentang wajar (0–5%)")
    if cfg.data["biaya"]["slippage_tick"] < 0:
        raise ValueError("biaya.slippage_tick tidak boleh negatif")


def db_path(home: Path | None = None) -> Path:
    return (Path(home) if home else DEFAULT_HOME) / "hermes.db"
