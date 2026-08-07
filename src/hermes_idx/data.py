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
    """Nilai transaksi harian ≈ harga tipikal × volume.

    Nilai sebenarnya = jumlah (harga × lot) seluruh transaksi hari itu, dan hanya IDX
    yang punya angka itu — endpoint resminya diblokir Cloudflare (403). TradingView pun
    tidak membantu: kolom `Value.Traded` miliknya ternyata persis `close × volume`
    (dicek pada BBCA: 6.450 × 152.663.200 = 984.677.640.000, sama sampai digit terakhir).

    Karena itu tetap estimasi, tapi memakai harga tipikal (H+L+C)/3 alih-alih close
    saja. Close adalah satu titik di ujung hari; pada bar yang bergerak lebar, close bisa
    jauh dari harga rata-rata transaksi. Harga tipikal jauh lebih dekat ke VWAP.
    """
    if pd.isna(row.get("volume")) or pd.isna(row.get("close")):
        return None
    close = float(row["close"])
    high, low = row.get("high"), row.get("low")
    if pd.notna(high) and pd.notna(low) and float(high) >= float(low) > 0:
        price = (float(high) + float(low) + close) / 3
    else:
        price = close
    return float(row["volume"]) * price


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
                if seen >= today:
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


# --------------------------------------------------------------------------- intraday

INTRADAY_LIMITS: dict[str, str] = {
    "1m": "5d", "5m": "60d", "15m": "60d", "30m": "60d", "60m": "730d",
}
"""Kedalaman maksimum per interval — batas keras Yahoo, diukur langsung pada ticker .JK.

Angka ini menentukan seberapa jauh strategi intraday bisa diuji, dan perbedaannya besar:
5m hanya 60 hari bursa (satu rezim pasar saja, tidak cukup untuk menyimpulkan edge),
sementara 60m menjangkau ~3 tahun dan melewati beberapa rezim. Minta range lebih panjang
dari daftar ini tidak menghasilkan error — Yahoo diam-diam memotongnya.
"""


def fetch_intraday(ticker: str, interval: str = "60m", timeout: float = 30.0
                   ) -> pd.DataFrame:
    """Ambil bar intraday. DataFrame ber-index `ts` (waktu Jakarta, tanpa tzinfo)."""
    if interval not in INTRADAY_LIMITS:
        raise ValueError(
            f"interval '{interval}' tidak didukung (pilih: {', '.join(INTRADAY_LIMITS)})")
    symbol = ticker if ticker.startswith("^") else f"{ticker}.JK"
    with httpx.Client(headers=UA, timeout=timeout, follow_redirects=True) as client:
        resp = client.get(YAHOO_CHART.format(symbol=symbol),
                          params={"interval": interval, "range": INTRADAY_LIMITS[interval]})
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        frame = _parse_yahoo_intraday(resp.json())
    return frame


def _parse_yahoo_intraday(payload: dict) -> pd.DataFrame:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()
    node = result[0]
    stamps = node.get("timestamp") or []
    if not stamps:
        return pd.DataFrame()
    quote = node["indicators"]["quote"][0]
    frame = pd.DataFrame(
        {"open": quote.get("open"), "high": quote.get("high"), "low": quote.get("low"),
         "close": quote.get("close"), "volume": quote.get("volume")},
        # Bar intraday TIDAK di-normalize ke tengah malam seperti bar harian — jamnya
        # justru informasi utamanya (sesi pembukaan vs penutupan berperilaku beda).
        index=pd.to_datetime(pd.Series(stamps), unit="s", utc=True)
        .dt.tz_convert("Asia/Jakarta").dt.tz_localize(None),
    )
    frame.index.name = "ts"
    return frame.dropna(subset=["close"])


def upsert_intraday(conn: sqlite3.Connection, ticker: str, frame: pd.DataFrame,
                    interval: str, source: str = "yahoo") -> int:
    if frame.empty:
        return 0
    now = dt.datetime.now().isoformat(timespec="seconds")
    rows = [
        (ticker, ts.isoformat(sep=" "), interval,
         _f(row.get("open")), _f(row.get("high")), _f(row.get("low")), _f(row.get("close")),
         int(row["volume"]) if pd.notna(row.get("volume")) else None, source, now)
        for ts, row in frame.iterrows()
    ]
    conn.executemany(
        "INSERT INTO ohlcv_intraday (ticker, ts, interval, open, high, low, close, volume,"
        " source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(ticker, ts, interval) DO UPDATE SET open=excluded.open,"
        " high=excluded.high, low=excluded.low, close=excluded.close,"
        " volume=excluded.volume, fetched_at=excluded.fetched_at",
        rows,
    )
    conn.commit()
    return len(rows)


def load_intraday(conn: sqlite3.Connection, ticker: str, interval: str = "60m"
                  ) -> pd.DataFrame:
    frame = pd.read_sql_query(
        "SELECT ts, open, high, low, close, volume FROM ohlcv_intraday"
        " WHERE ticker = ? AND interval = ? ORDER BY ts",
        conn, params=[ticker, interval], parse_dates=["ts"],
    )
    return frame.set_index("ts") if not frame.empty else frame


def update_intraday(conn: sqlite3.Connection, tickers: Iterable[str], interval: str = "60m",
                    progress=None) -> dict[str, int]:
    """Sama seperti `update()`: satu ticker gagal tidak menghentikan sisanya."""
    results: dict[str, int] = {}
    for ticker in tickers:
        try:
            count = upsert_intraday(conn, ticker, fetch_intraday(ticker, interval), interval)
        except Exception as exc:  # noqa: BLE001
            results[ticker] = -1
            set_meta(conn, f"error:intraday:{ticker}", f"{dt.date.today()}: {exc}")
            if progress:
                progress(ticker, -1)
            continue
        results[ticker] = count
        set_meta(conn, f"last_intraday:{interval}", dt.datetime.now().isoformat(timespec="seconds"))
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
    """Filter likuiditas & kelayakan. Kembalikan (daftar_ticker, peringatan).

    Bila `universe.bluechip_only` aktif (default), hanya emiten kurasi bluechip
    (universe.BLUECHIP) yang bisa lolos jadi kandidat sinyal beli. Emiten porto
    non-bluechip tetap dipantau lewat tabel `posisi` — filter ini hanya membatasi
    KANDIDAT BARU, bukan pengawasan posisi yang sudah ada."""
    from .universe import BLUECHIP  # impor lokal: hindari lingkaran saat module load

    uni = cfg.data["universe"]
    bluechip_only = bool(uni.get("bluechip_only", False))
    allowed = set(BLUECHIP) if bluechip_only else None
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

    minimum = uni["min_avg_value_20d"]
    # Estimasi nilai transaksi meleset beberapa persen dari angka sebenarnya. Itu hanya
    # PENTING kalau ada emiten yang duduk dekat ambang, di mana meleset sedikit bisa
    # membalik keputusan lolos/tidak. Untuk BBCA (Rp985 miliar vs ambang Rp2 miliar)
    # selisih 5% tidak mengubah apa pun. Dulu peringatan ini muncul di SETIAP run tanpa
    # syarat, jadi ia jadi bising yang dilewati mata, bukan informasi.
    BORDERLINE = 0.25

    tickers, borderline = [], []
    for row in rows:
        if row["ticker"].startswith("^"):
            continue
        if allowed is not None and row["ticker"] not in allowed:
            continue
        if row["total_bars"] < uni["min_listing_days"]:
            continue
        close = row["last_close"] or 0
        if not (uni["min_price"] <= close <= uni["max_price"]):
            continue
        value = row["avg_value"] or 0
        if row["estimated"] and minimum and abs(value - minimum) <= minimum * BORDERLINE:
            borderline.append((row["ticker"], value))
        if value < minimum:
            continue
        tickers.append(row["ticker"])

    warnings = []
    if borderline:
        detail = ", ".join(f"{t} (≈Rp{v / 1e9:.1f} M)" for t, v in sorted(borderline))
        warnings.append(
            f"Nilai transaksi adalah estimasi (harga tipikal × volume) — IDX tidak "
            f"membuka angka sebenarnya. Emiten ini duduk dalam ±{BORDERLINE:.0%} dari "
            f"ambang likuiditas Rp{minimum / 1e9:.1f} M, jadi lolos/tidaknya bisa berubah "
            f"kalau estimasinya meleset: {detail}."
        )
    return sorted(tickers), warnings
