"""Klien MCP TradingView — sumber harga cadangan via Model Context Protocol.

Kenapa MCP dan bukan langsung TradingView Scanner?
- Scanner (daily.fetch_live_prices) adalah sumber UTAMA: 1 request batch, cepat,
  sudah terbukti. Tapi satu titik gagal — kalau scanner down atau format berubah,
  seluruh laporan jatuh ke harga DB basi.
- Server MCP tradingview-mcp (terpasang terpisah, venv sendiri) menyediakan tool
  `yahoo_price` dengan jalur data berbeda (Yahoo Finance). Ia jadi CADANGAN yang
  benar-benar independen — bukan "cadangan" yang memakai endpoint sama.

Implementasi: JSON-RPC 2.0 langsung lewat stdin/stdout server MCP (stdio transport).
Tidak bergantung pustaka `mcp` — cukup subprocess + stdlib. Satu sesi stdio untuk
semua simbol (initialize → N × tools/call), diukur ~2 detik untuk 3 simbol.

Semua kegagalan ditelan dan menghasilkan dict kosong — kontraknya sama dengan
fetch_live_prices: pemanggil tidak boleh crash gara-gara sumber cadangan.
"""

from __future__ import annotations

import json
import os
import subprocess

INIT_MSG = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "hermes-idx", "version": "0.1"},
    },
}
INITIALIZED_NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}
BASE_ID = 100
"""id JSON-RPC mulai dari sini; respons dipetakan balik ke ticker lewat id."""


def _yahoo_symbol(ticker: str) -> str:
    """Ticker lokal → simbol Yahoo. IHSG di scanner = COMPOSITE, di Yahoo = ^JKSE."""
    if ticker in ("COMPOSITE", "^JKSE"):
        return "^JKSE"
    return f"{ticker.upper()}.JK"


def _local_symbol(ticker: str) -> str:
    """Kebalikan _yahoo_symbol untuk memetakan hasil balik ke ticker lokal."""
    if ticker == "^JKSE":
        return "COMPOSITE"
    return ticker.removesuffix(".JK").upper()


def fetch_quotes(tickers: list[str], command: str, timeout: int = 60) -> dict[str, dict]:
    """Ambil harga dari server MCP (tool yahoo_price). Return format sama dengan
    daily.fetch_live_prices: {ticker: {"price", "chg_pct", "source": "MCP"}}.

    Gagal apa pun (server tidak ada, timeout, format berubah) → dict kosong."""
    if not tickers or not command:
        return {}

    messages = [INIT_MSG, INITIALIZED_NOTE]
    by_id: dict[int, str] = {}
    for i, t in enumerate(tickers):
        req_id = BASE_ID + i
        by_id[req_id] = _local_symbol(t)
        messages.append({
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": "yahoo_price", "arguments": {"symbol": _yahoo_symbol(t)}},
        })

    payload = "".join(json.dumps(m) + "\n" for m in messages)
    cmd = command.split() if isinstance(command, str) else list(command)
    cmd = [os.path.expanduser(c) for c in cmd]
    try:
        proc = subprocess.run(cmd + ["stdio"], input=payload, capture_output=True,
                              text=True, timeout=timeout)
    except Exception:
        return {}

    result: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        req_id = msg.get("id")
        if not isinstance(req_id, int) or req_id not in by_id or "result" not in msg:
            continue
        texts = [c.get("text", "") for c in msg["result"].get("content", [])
                 if c.get("type") == "text"]
        if not texts:
            continue
        try:
            quote = json.loads(texts[0])
        except ValueError:
            continue
        price = quote.get("price")
        if price is None or float(price) <= 0:
            continue
        result[by_id[req_id]] = {
            "price": float(price),
            "chg_pct": float(quote.get("change_pct") or 0.0),
            "source": "MCP",
        }
    return result
