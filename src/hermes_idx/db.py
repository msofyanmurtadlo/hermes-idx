"""Skema & koneksi SQLite.

Perubahan terhadap skema di PRD §10 (semua dari review, issue #8 / REVIEW-01 M1):
- `signal` menampung seluruh field yang diwajibkan SB-1 (`entry_type`, `entry_zone_*`,
  `tp1_size`, `tp2_size`, `confidence_*`) — sebelumnya tidak muat.
- `transaksi` punya UNIQUE key supaya import CSV dua kali tidak menduplikasi diam-diam.
- `ohlcv` mencatat `source` & `fetched_at` karena desainnya multi-adapter.
- Ada `schema_version` supaya migrasi antar versi mungkin dilakukan.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS emiten (
    ticker TEXT PRIMARY KEY, nama TEXT, sektor TEXT, sub_sektor TEXT,
    papan TEXT, listing_date DATE, shares_outstanding INTEGER,
    is_active INTEGER DEFAULT 1, delisting_date DATE
);

CREATE TABLE IF NOT EXISTS ohlcv (
    ticker TEXT NOT NULL, date DATE NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL,
    volume INTEGER, value REAL, frequency INTEGER,
    value_is_estimated INTEGER DEFAULT 0,
    is_anomaly INTEGER DEFAULT 0, anomaly_reason TEXT,
    source TEXT, fetched_at TIMESTAMP,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);

CREATE TABLE IF NOT EXISTS corporate_action (
    ticker TEXT NOT NULL, ex_date DATE NOT NULL, type TEXT NOT NULL,
    ratio REAL, value REAL, source TEXT,
    PRIMARY KEY (ticker, ex_date, type)
);

CREATE TABLE IF NOT EXISTS signal (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, signal_date DATE NOT NULL,
    strategy TEXT NOT NULL, action TEXT NOT NULL, entry_type TEXT,
    entry_price REAL, entry_zone_low REAL, entry_zone_high REAL,
    stop_loss REAL, sl_pct REAL, tp1 REAL, tp2 REAL,
    tp1_size REAL, tp2_size REAL, rr_tp1 REAL, rr_tp2 REAL,
    position_lot INTEGER, risk_rp REAL, score REAL,
    confidence_low REAL, confidence_high REAL,
    valid_until DATE, notes TEXT, status TEXT DEFAULT 'ACTIVE',
    UNIQUE (ticker, signal_date, strategy)
);

CREATE TABLE IF NOT EXISTS transaksi (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, date DATE NOT NULL,
    type TEXT NOT NULL, lot INTEGER NOT NULL, price REAL NOT NULL,
    fee REAL, signal_id INTEGER, source TEXT, notes TEXT,
    UNIQUE (ticker, date, type, lot, price)
);

CREATE TABLE IF NOT EXISTS posisi (
    ticker TEXT PRIMARY KEY, lot INTEGER NOT NULL, avg_price REAL NOT NULL,
    stop_loss REAL, tp1 REAL, tp2 REAL, entry_date DATE,
    signal_id INTEGER, strategy TEXT, breakeven_moved INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trade_closed (
    id INTEGER PRIMARY KEY, ticker TEXT NOT NULL,
    entry_date DATE, exit_date DATE, entry_price REAL, exit_price REAL,
    lot INTEGER, pnl_rp REAL, pnl_pct REAL, r_multiple REAL,
    strategy TEXT, exit_reason TEXT,
    followed_plan INTEGER, sl_respected INTEGER, data_complete INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS backtest_result (
    id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, run_date DATE,
    period_start DATE, period_end DATE, method TEXT,
    total_trades INTEGER, win_rate REAL, expectancy REAL, profit_factor REAL,
    max_dd REAL, sharpe REAL, p_value REAL, unfilled_ara INTEGER,
    params_json TEXT, warnings TEXT
);

CREATE TABLE IF NOT EXISTS ohlcv_intraday (
    ticker TEXT NOT NULL, ts TIMESTAMP NOT NULL, interval TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    source TEXT, fetched_at TIMESTAMP,
    PRIMARY KEY (ticker, ts, interval)
);
CREATE INDEX IF NOT EXISTS idx_intraday_lookup ON ohlcv_intraday(ticker, interval, ts);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
"""Tabel `ohlcv_intraday` sengaja terpisah dari `ohlcv`, bukan menambah kolom `interval`
ke sana. Bar harian dan bar intraday punya sifat berbeda: yang harian permanen dan
lengkap sejak 5 tahun lalu, yang intraday hanya tersedia 60 hari terakhir (5m/15m) atau
730 hari (60m) dan terus digeser ke belakang oleh Yahoo. Mencampurnya akan membuat
setiap query harian harus menyaring interval, dan sekali lupa hasilnya salah diam-diam.

Penambahan tabel bersifat aditif, jadi SCHEMA_VERSION tidak dinaikkan — database lama
mendapat tabel ini otomatis saat `connect()` berikutnya tanpa perlu migrasi."""


def connect(path: str | Path) -> sqlite3.Connection:
    """Buka koneksi, pastikan skema ada, dan aktifkan foreign key."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"Database dibuat oleh skema versi {row['version']}, kode ini versi "
            f"{SCHEMA_VERSION}. Migrasi belum diimplementasikan."
        )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
