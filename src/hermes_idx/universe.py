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


def seed(conn) -> int:
    """Isi tabel `emiten` dari daftar kurasi. Idempoten."""
    rows = [(t, None, sector_of(t), "Utama") for t in BLUECHIP]
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
