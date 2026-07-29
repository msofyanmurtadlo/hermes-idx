"""Data layer: ambil, bersihkan, simpan OHLCV.

Adapter pattern — `PriceSource` adalah satu-satunya antarmuka yang dilihat modul lain,
supaya pergantian sumber tidak menyentuh strategi/backtest.

Keterbatasan yang diketahui (REVIEW-01 B4): Yahoo Finance tidak menyediakan nilai
transaksi rupiah maupun frekuensi. `value` diestimasi dari `volume × close` dan ditandai
`value_is_estimated = 1`. Filter likuiditas memakai angka estimasi ini — pemakai harus
tahu itu, jadi `universe()` mengembalikannya sebagai peringatan, bukan menyembunyikannya.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from typing import Iterable, Protocol

import httpx
import pandas as pd

from .db import get_meta, set_meta

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; hermes-idx/0.1)"}


class PriceSource(Protocol):
    name: str

    def fetch(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Kembalikan DataFrame ber-index `date` dengan kolom
        open/high/low/close/adj_close/volume. DataFrame kosong bila tidak ada data."""


class YahooSource:
    """Sumber prioritas-1. Gratis, historis panjang, tapi lihat catatan modul di atas."""

    name = "yahoo"

    def __init__(self, rate_limit_per_sec: float = 5.0, timeout: float = 20.0) -> None:
        self._min_interval = 1.0 / rate_limit_per_sec if rate_limit_per_sec > 0 else 0.0
        self._last_call = 0.0
        self._client = httpx.Client(headers=UA, timeout=timeout, follow_redirects=True)

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def fetch(self, ticker: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        symbol = ticker if ticker.startswith("^") else f"{ticker}.JK"
        params = {
            "period1": int(dt.datetime.combine(start, dt.time()).timestamp()),
            "period2": int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time()).timestamp()),
            "interval": "1d",
            "events": "div,split",
        }
        for attempt in range(4):
            self._throttle()
            try:
                resp = self._client.get(YAHOO_CHART.format(symbol=symbol), params=params)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
                continue
            if resp.status_code == 429:
                time.sleep(2**attempt * 2)
                continue
            if resp.status_code == 404:
                return pd.DataFrame()
            resp.raise_for_status()
            return _parse_yahoo(resp.json())
        return pd.DataFrame()


def _parse_yahoo(payload: dict) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    node = result[0]
    stamps = node.get("timestamp") or []
    if not stamps:
        return pd.DataFrame()
    quote = node["indicators"]["quote"][0]
    adj = (node["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    frame = pd.DataFrame(
        {
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
            "adj_close": adj if adj is not None else quote.get("close"),
        },
        index=pd.to_datetime(pd.Series(stamps), unit="s", utc=True)
        .dt.tz_convert("Asia/Jakarta")
        .dt.normalize()
        .dt.tz_localize(None),
    )
    frame.index.name = "date"
    return frame.dropna(subset=["close"])


# --------------------------------------------------------------------------- anomali

def flag_anomalies(frame: pd.DataFrame) -> pd.Series:
    """DL-6: tandai bar yang mencurigakan. Data tetap disimpan, hanya diberi label."""
    reason = pd.Series("", index=frame.index, dtype=object)
    ret = frame["close"].pct_change().abs()
    reason = reason.mask(ret > 0.35, "gap>35%")
    zero_vol = (frame["volume"].fillna(0) == 0).rolling(3, min_periods=3).sum() >= 3
    reason = reason.mask(zero_vol & (reason == ""), "volume 0 beruntun")
    reason = reason.mask(frame["close"] <= 0, "harga <= 0")
    return reason


# --------------------------------------------------------------------------- penyimpanan

def upsert_ohlcv(conn: sqlite3.Connection, ticker: str, frame: pd.DataFrame, source: str) -> int:
    """Simpan bar. `value` diestimasi dari volume × close dan ditandai sebagai estimasi."""
    if frame.empty:
        return 0
    reason = flag_anomalies(frame)
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            ticker,
            index.date().isoformat(),
            _f(row.get("open")), _f(row.get("high")), _f(row.get("low")),
            _f(row.get("close")), _f(row.get("adj_close")),
            int(row["volume"]) if pd.notna(row.get("volume")) else None,
            _estimate_value(row),
            None,
            1,
            1 if reason.loc[index] else 0,
            reason.loc[index] or None,
            source,
            now,
        )
        for index, row in frame.iterrows()
    ]
    conn.executemany(
        "INSERT INTO ohlcv (ticker, date, open, high, low, close, adj_close, volume, value,"
        " frequency, value_is_estimated, is_anomaly, anomaly_reason, source, fetched_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(ticker, date) DO UPDATE SET"
        " open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,"
        " adj_close=excluded.adj_close, volume=excluded.volume, value=excluded.value,"
        " value_is_estimated=excluded.value_is_estimated, is_anomaly=excluded.is_anomaly,"
        " anomaly_reason=excluded.anomaly_reason, source=excluded.source,"
        " fetched_at=excluded.fetched_at",
        rows,
    )
    conn.commit()
    return len(rows)


def _f(value) -> float | None:
    return float(value) if pd.notna(value) else None


def _estimate_value(row) -> float | None:
    if pd.isna(row.get("volume")) or pd.isna(row.get("close")):
        return None
    return float(row["volume"]) * float(row["close"])


def last_date(conn: sqlite3.Connection, ticker: str) -> dt.date | None:
    row = conn.execute("SELECT MAX(date) AS d FROM ohlcv WHERE ticker = ?", (ticker,)).fetchone()
    return dt.date.fromisoformat(row["d"]) if row and row["d"] else None


def load_ohlcv(conn: sqlite3.Connection, ticker: str, start: dt.date | None = None) -> pd.DataFrame:
    query = "SELECT * FROM ohlcv WHERE ticker = ?"
    params: list = [ticker]
    if start:
        query += " AND date >= ?"
        params.append(start.isoformat())
    frame = pd.read_sql_query(query + " ORDER BY date", conn, params=params, parse_dates=["date"])
    return frame.set_index("date") if not frame.empty else frame


def update(
    conn: sqlite3.Connection,
    tickers: Iterable[str],
    source: PriceSource,
    history_years: int = 5,
    full: bool = False,
    progress=None,
) -> dict[str, int]:
    """DL-3 incremental + DL-7 resume-safe.

    Checkpoint disimpan di tabel `meta` setelah tiap ticker, jadi proses yang terputus
    (HP tidur, koneksi mati) tinggal dijalankan ulang dan melanjutkan dari titik terakhir.
    """
    today = dt.date.today()
    results: dict[str, int] = {}
    for ticker in tickers:
        start = dt.date(today.year - history_years, today.month, 1)
        if not full:
            seen = last_date(conn, ticker)
            if seen:
                if seen >= today - dt.timedelta(days=1):
                    results[ticker] = 0
                    if progress:
                        progress(ticker, 0)
                    continue
                start = seen - dt.timedelta(days=5)  # overlap kecil untuk revisi bar
        try:
            frame = source.fetch(ticker, start, today)
            count = upsert_ohlcv(conn, ticker, frame, source.name)
        except Exception as exc:  # noqa: BLE001 - satu ticker gagal tidak boleh menghentikan sisanya
            results[ticker] = -1
            set_meta(conn, f"error:{ticker}", f"{today.isoformat()}: {exc}")
            if progress:
                progress(ticker, -1)
            continue
        results[ticker] = count
        set_meta(conn, "last_ticker", ticker)
        set_meta(conn, "last_update", today.isoformat())
        if progress:
            progress(ticker, count)
    return results


def data_age_days(conn: sqlite3.Connection) -> int | None:
    """Umur data terbaru dalam hari kalender. None bila database kosong."""
    row = conn.execute("SELECT MAX(date) AS d FROM ohlcv").fetchone()
    if not row or not row["d"]:
        return None
    return (dt.date.today() - dt.date.fromisoformat(row["d"])).days


# --------------------------------------------------------------------------- universe

def universe(conn: sqlite3.Connection, cfg) -> tuple[list[str], list[str]]:
    """Filter likuiditas & kelayakan. Kembalikan (daftar_ticker, peringatan)."""
    uni = cfg.data["universe"]
    rows = conn.execute(
        """
        WITH terakhir AS (
            SELECT ticker, MAX(date) AS d FROM ohlcv GROUP BY ticker
        ),
        agg AS (
            SELECT o.ticker,
                   AVG(o.value) AS avg_value,
                   COUNT(*) AS bars,
                   MAX(o.value_is_estimated) AS estimated
            FROM ohlcv o
            JOIN terakhir t ON t.ticker = o.ticker
            WHERE o.date > date(t.d, '-30 day')
            GROUP BY o.ticker
        )
        SELECT a.ticker, a.avg_value, a.estimated,
               (SELECT COUNT(*) FROM ohlcv x WHERE x.ticker = a.ticker) AS total_bars,
               (SELECT close FROM ohlcv y WHERE y.ticker = a.ticker ORDER BY date DESC LIMIT 1) AS last_close
        FROM agg a
        """
    ).fetchall()

    tickers, estimated_any = [], False
    for row in rows:
        if row["ticker"].startswith("^"):
            continue
        if row["total_bars"] < uni["min_listing_days"]:
            continue
        close = row["last_close"] or 0
        if not (uni["min_price"] <= close <= uni["max_price"]):
            continue
        if (row["avg_value"] or 0) < uni["min_avg_value_20d"]:
            continue
        estimated_any = estimated_any or bool(row["estimated"])
        tickers.append(row["ticker"])

    warnings = []
    if estimated_any:
        warnings.append(
            "Nilai transaksi harian diestimasi dari volume × close (sumber tidak menyediakan "
            "nilai rupiah sebenarnya). Filter likuiditas karena itu bersifat aproksimasi — "
            "lihat issue #4."
        )
    return sorted(tickers), warnings
