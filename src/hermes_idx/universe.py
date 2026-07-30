"""Universe bluechip IDX + peta sektor.

Diambil dari `idx-bluechip-report.py` v3 (script Hermes yang sudah dipakai harian).
Daftar ini kurasi manual — jauh lebih berguna daripada menunggu daftar emiten IDX
otomatis, dan sudah terbukti dipakai. Sektor dipakai untuk batas konsentrasi.
"""

from __future__ import annotations

SECTORS: dict[str, str] = {
    "BBCA": "bank", "BBRI": "bank", "BMRI": "bank", "BBNI": "bank", "BBTN": "bank",
    "TLKM": "telco", "ISAT": "telco", "EXCL": "telco", "TOWR": "telco",
    "EMTK": "media", "SCMA": "media", "GOTO": "media", "BMTR": "media",
    "UNVR": "consumer", "ICBP": "consumer", "INDF": "consumer", "MYOR": "consumer",
    "KLBF": "health", "SIDO": "health", "HEAL": "health",
    "AMRT": "retail", "ACES": "retail", "MAPI": "retail",
    "CPIN": "poultry", "JPFA": "poultry",
    "ASII": "auto", "UNTR": "auto",
    "AKRA": "energy", "BRPT": "energy", "DSSA": "energy",
    "ADRO": "mining", "AADI": "mining", "ADMR": "mining", "PTBA": "mining",
    "ITMG": "mining", "BUMI": "mining", "PGAS": "mining", "PGEO": "mining",
    "MEDC": "mining", "MDKA": "mining", "ANTM": "mining", "INCO": "mining",
    "TINS": "mining", "AMMN": "mining", "MBMA": "mining", "NCKL": "mining",
    "SMGR": "cement", "INTP": "cement",
    "INKP": "paper",
    "CTRA": "property", "JSMR": "property",
}

BLUECHIP: tuple[str, ...] = tuple(sorted(SECTORS))
"""Daftar emiten default. Dipakai `data update` bila tabel `emiten` masih kosong."""


def sector_of(ticker: str) -> str:
    return SECTORS.get(ticker.upper(), "lain")


IDX_SCANNER = "https://scanner.tradingview.com/indonesia/scan"
"""Sumber daftar emiten IDX (issue #4 — IDX sendiri tidak punya API publik).

Endpoint yang sama sudah dipakai `daily.fetch_live_prices()` untuk harga real-time,
jadi tidak ada ketergantungan baru. Satu request mengembalikan seluruh papan (~843).
"""


def fetch_all(timeout: float = 30.0) -> list[tuple[str, str | None, str]]:
    """Seluruh emiten saham IDX dari TradingView Scanner. Return (ticker, nama, sektor).

    Sektor: emiten yang ada di peta kurasi memakai label Indonesia yang sudah dipakai
    batas konsentrasi (`bank`, `mining`, ...); sisanya memakai label sektor TradingView
    apa adanya. Dicampur begitu supaya batas per-sektor untuk bluechip tidak berubah
    arti hanya karena universe-nya diperluas.
    """
    import httpx

    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "columns": ["description", "sector"],
        "range": [0, 5000],
        "sort": {"sortBy": "name", "sortOrder": "asc"},
    }
    resp = httpx.post(IDX_SCANNER, json=payload, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; hermes-idx/0.1)"})
    resp.raise_for_status()
    rows = []
    for item in resp.json().get("data", []):
        ticker = str(item["s"]).split(":")[-1].upper()
        cells = list(item.get("d") or [])
        nama = cells[0] if len(cells) > 0 else None
        tv_sector = cells[1] if len(cells) > 1 else None
        rows.append((ticker, nama, SECTORS.get(ticker) or tv_sector or "lain"))
    return rows


def seed(conn, rows: list[tuple[str, str | None, str]] | None = None) -> int:
    """Isi tabel `emiten`. Default: daftar bluechip kurasi. Idempoten."""
    rows = [(t, n, s, "Utama") for t, n, s in rows] if rows else \
        [(t, None, sector_of(t), "Utama") for t in BLUECHIP]
    conn.executemany(
        "INSERT INTO emiten (ticker, nama, sektor, papan) VALUES (?,?,?,?)"
        " ON CONFLICT(ticker) DO UPDATE SET sektor = excluded.sektor",
        rows,
    )
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------- sesi bursa

SESSIONS = (
    (540, 555, "RAWAN", "09:00–09:15 pembukaan volatil — tunggu harga stabil dulu"),
    (555, 660, "IDEAL", "09:15–11:00 jam ideal untuk entry"),
    (660, 810, "SEPI", "11:00–13:30 istirahat/sesi tipis — likuiditas berkurang"),
    (810, 930, "IDEAL", "13:30–15:30 jam ideal untuk entry"),
    (930, 960, "RAWAN", "15:30–16:00 closing auction — hindari entry baru"),
)
"""Jendela waktu perdagangan IDX dalam menit sejak tengah malam WIB.

Pengetahuan spesifik IDX dari script v3. Bukan sekadar kosmetik: entry di menit-menit
pembukaan dan di closing auction punya slippage jauh lebih besar daripada asumsi 1 tick.
"""


def session_note(minutes_since_midnight: int) -> tuple[str, str] | None:
    """Kembalikan (level, catatan) untuk jam sekarang, atau None bila di luar jam bursa."""
    for start, end, level, note in SESSIONS:
        if start <= minutes_since_midnight < end:
            return level, note
    return None
