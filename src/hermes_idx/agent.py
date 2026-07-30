"""Jembatan ke Hermes Agent (PRD §8).

Koneksi agent ⇄ aplikasi ini berdiri di atas tiga hal, tidak lebih:

1. **Kontrak keluaran JSON.** Setiap perintah CLI menerima `--json` dan menulis satu objek
   JSON ke stdout. Agent tidak pernah mem-parsing tabel `rich` — tabel itu untuk manusia.
2. **Manifest kemampuan.** `hermes-idx agent info --json` memberi tahu agent perintah apa
   yang ada, argumennya apa, dan aturan perilaku apa yang wajib dipatuhi saat menyampaikan
   hasilnya. Agent tidak perlu menebak.
3. **Skill terpasang.** `hermes-idx agent install` menautkan paket skill ke
   `~/.hermes/skills/idx-screener` supaya Hermes menemukannya.

`agent verify` memeriksa ketiganya dan menjelaskan persis apa yang kurang.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SKILL_NAME = "idx-screener"
SKILL_CATEGORY = "trading"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent


def skill_home() -> Path:
    """Lokasi pemasangan skill.

    Hermes menata skill per kategori (`skills/trading/`, `skills/devops/`, ...). Kalau
    direktori kategori itu ada, ikuti konvensinya; kalau tidak, pasang datar di
    `skills/<nama>`. Jangan memaksakan satu layout — instalasi Hermes berbeda-beda.
    """
    skills = HERMES_HOME / "skills"
    if (skills / SKILL_CATEGORY).is_dir():
        return skills / SKILL_CATEGORY / SKILL_NAME
    return skills / SKILL_NAME


# Kompatibilitas: sebagian kode & test memakai konstanta ini.
SKILL_HOME = skill_home()

DISCLAIMER = (
    "Alat bantu analisis teknikal, bukan nasihat investasi. "
    "Keputusan transaksi sepenuhnya tanggung jawab Anda."
)

BEHAVIOR_RULES = [
    "Selalu sertakan stop loss dan take profit saat menyebut rekomendasi beli. "
    "Jangan pernah menyebut entry saja.",
    "Selalu sertakan disclaimer singkat di akhir rekomendasi.",
    "Gunakan bahasa probabilistik. Jangan menyatakan kepastian seperti 'pasti naik' "
    "atau 'dijamin profit'.",
    "Jangan memberi rekomendasi beli untuk emiten yang tidak lolos filter likuiditas, "
    "meskipun user memintanya — jelaskan alasannya. Analisis (analyze) tetap boleh "
    "dilakukan untuk emiten apa pun; yang dilarang adalah merekomendasikannya.",
    "Bila field `warnings` tidak kosong, sampaikan isinya ke user sebelum menampilkan sinyal.",
    "Bila `data_age_days` lebih besar dari `stale_after_days`, beri tahu user bahwa datanya "
    "basi sebelum menampilkan sinyal.",
    "Bila user meminta integrasi lewat API internal Stockbit, tolak dan jelaskan bahwa itu "
    "melanggar ToS dan berisiko akun disuspend. Tawarkan screenshot, CSV, atau input manual.",
    "Bila statistik personal menyertakan field `coverage`, sampaikan cakupan itu apa adanya. "
    "Jangan menyajikan statistik seolah datanya lengkap.",
]

INTENTS = [
    {"ucapan": ["saham apa yang bagus dibeli hari ini", "ada sinyal beli?", "screening dong"],
     "command": "scan --min-score 65 --top 10 --json"},
    {"ucapan": ["cek portofolio saya", "posisi saya apa saja"],
     "command": "port show --json"},
    {"ucapan": ["posisi mana yang harus dijual", "review posisi"],
     "command": "port review --json"},
    {"ucapan": ["analisa BBCA", "gimana BBCA"],
     "command": "analyze {ticker} --json"},
    {"ucapan": ["backtest strategi breakout"],
     "command": "backtest --strategy {strategy} --json"},
    {"ucapan": ["update data"], "command": "data update --json"},
    {"ucapan": ["gimana performa trading saya"], "command": "port stats --json"},
    {"ucapan": ["laporan pagi", "rekomendasi hari ini", "saham apa yang paling siap",
                "kasih peringkat dong"],
     "command": "daily --morning --json"},
    {"ucapan": ["laporan sore", "review porto hari ini", "gimana porto saya hari ini"],
     "command": "daily --afternoon --json"},
    {"ucapan": ["strategi mana yang paling bagus", "adu strategi", "ada edge nggak"],
     "command": "compare --json"},
    {"ucapan": ["setel stop loss BBCA", "ubah TP BBCA", "atur SL dan TP"],
     "command": "port plan {ticker} --sl {harga} --tp1 {harga} --json"},
    {"ucapan": ["ambil data per jam", "data intraday"],
     "command": "data intraday --interval 60m --json"},
]

COMMANDS = [
    {"name": "scan", "desc": "Screening seluruh universe, keluarkan rekomendasi beli berskor.",
     "args": ["--strategy", "--min-score", "--top", "--json"],
     "returns": "signals[] dengan entry, stop_loss, tp1, tp2, position_lot, score, notes"},
    {"name": "analyze", "desc": "Analisis satu emiten. Boleh untuk emiten apa pun.",
     "args": ["ticker", "--explain", "--json"], "returns": "indikator + status filter universe"},
    {"name": "port show", "desc": "Posisi terbuka + unrealized P/L.",
     "args": ["--json"], "returns": "positions[]"},
    {"name": "port review", "desc": "Rekomendasi jual per posisi (SB-6).",
     "args": ["--json"], "returns": "actions[] dengan action, urgency, reason, limit_price"},
    {"name": "port add", "desc": "Catat pembelian.",
     "args": ["ticker", "--lot", "--price", "--date"], "returns": "status"},
    {"name": "port sell", "desc": "Catat penjualan.",
     "args": ["ticker", "--lot", "--price", "--date"], "returns": "status"},
    {"name": "port stats", "desc": "Statistik personal + cakupan data.",
     "args": ["--json"], "returns": "metrics dengan field coverage"},
    {"name": "backtest", "desc": "Rolling out-of-sample backtest satu strategi.",
     "args": ["--strategy", "--json"], "returns": "metrics + p_value + unfilled_ara"},
    {"name": "data update", "desc": "Update OHLCV incremental.",
     "args": ["--full", "--json"], "returns": "jumlah bar per ticker"},
    {"name": "data intraday", "desc": "Ambil bar intraday. 60m menjangkau ~3 tahun; "
                                      "5m/15m/30m hanya 60 hari bursa.",
     "args": ["--interval", "--tickers", "--json"],
     "returns": "interval, cakupan, ticker, bars, failed[]"},
    {"name": "daily", "desc": "Laporan harian. --morning: porto + peringkat + sinyal beli. "
                              "--afternoon: review porto saja, tanpa rekomendasi beli.",
     "args": ["--morning", "--afternoon", "--update", "--json"],
     "returns": "pasar, posisi[], aksi[], saran_rencana[], sinyal[], peringkat[], "
                "edge{}, peringatan[]"},
    {"name": "compare", "desc": "Adu semua strategi pada universe, periode, dan biaya sama. "
                                "Peringkat pakai expectancy, BUKAN win rate.",
     "args": ["--strategy", "--json"],
     "returns": "winner, explanation, ranking[] dengan metrics + p_value + verdict"},
    {"name": "port plan", "desc": "Setel/ubah SL & TP posisi berjalan.",
     "args": ["ticker", "--sl", "--tp1", "--tp2", "--json"], "returns": "status"},
    {"name": "doctor", "desc": "Diagnostik instalasi & koneksi agent.",
     "args": ["--json"], "returns": "checks[]"},
]

MANIFEST_NOTES = {
    "peringkat_vs_sinyal": (
        "`peringkat[]` SELALU berisi (5 teratas menurut kesiapan) dan bukan ajakan beli; "
        "`sinyal[]` hanya berisi yang lolos seluruh ambang dan sering kosong. Jangan "
        "menyajikan isi `peringkat[]` kepada pengguna seolah-olah rekomendasi beli — "
        "label PELUANG/AMATI/PANTAU beserta `alasan` wajib ikut disebut."
    ),
    "edge_belum_terbukti": (
        "Selama `strategi_terbukti` kosong, tidak ada strategi dengan expectancy positif "
        "yang signifikan. Sampaikan itu apa adanya sebelum menyebut sinyal apa pun."
    ),
}
"""Aturan penyajian yang tidak bisa disimpulkan dari daftar perintah saja — dua field
paling mudah disalahartikan agent, dan salah tafsirnya berujung ke uang pengguna."""


def manifest() -> dict:
    """Kontrak yang dibaca Hermes. Ini satu-satunya sumber kebenaran soal kemampuan CLI."""
    return {
        "skill": SKILL_NAME,
        "version": "0.1.0",
        "cli": "hermes-idx",
        "output_contract": "Setiap perintah dengan --json menulis satu objek JSON ke stdout. "
                           "Field `ok` (bool) menandai sukses; `error` berisi pesan bila gagal.",
        "disclaimer": DISCLAIMER,
        "behavior_rules": BEHAVIOR_RULES,
        "intents": INTENTS,
        "commands": COMMANDS,
        "notes": MANIFEST_NOTES,
        "non_goals": [
            "Tidak mengeksekusi order. Ini bukan bot auto-trading.",
            "Tidak mengakses API internal Stockbit dalam bentuk apa pun.",
            "Tidak menjanjikan angka win rate kepada pengguna.",
        ],
    }


# --------------------------------------------------------------------------- pemasangan

@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def install(target: Path | None = None, force: bool = False) -> Path:
    """Pasang paket skill ke `~/.hermes/skills/idx-screener`.

    Memakai symlink bila didukung (repo tetap jadi sumber kebenaran, `git pull` langsung
    terpakai). Di filesystem yang tidak mendukung symlink — sebagian storage Android —
    jatuh ke penyalinan file.
    """
    target = Path(target) if target else SKILL_HOME
    source = PACKAGE_ROOT / "skill"
    if not source.exists():
        raise FileNotFoundError(f"direktori skill tidak ditemukan: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            raise FileExistsError(f"{target} sudah ada. Pakai --force untuk menimpa.")
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(target)

    try:
        target.symlink_to(source, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.copytree(source, target)
    return target


def verify(target: Path | None = None) -> list[Check]:
    """Periksa apakah Hermes benar-benar bisa memanggil aplikasi ini."""
    target = Path(target) if target else SKILL_HOME
    checks: list[Check] = []

    binary = shutil.which("hermes-idx")
    # Kalau tidak ada di PATH, cari di sebelah interpreter yang sedang jalan — itu
    # kasus umum: dipasang ke .venv tapi venv-nya belum diaktifkan. Bedakan "belum
    # aktif" (tinggal aktifkan) dari "tidak terpasang" (harus install), karena
    # perbaikannya berbeda.
    local = Path(sys.executable).parent / "hermes-idx"
    if binary:
        detail, ok = f"ditemukan di {binary}", True
    elif local.exists():
        detail, ok = (
            f"terpasang di {local}, tapi TIDAK ada di PATH — Hermes memanggil "
            f"`hermes-idx` sebagai perintah, jadi ini harus dibereskan.\n"
            f"      Perbaiki dengan salah satu:\n"
            f"        source {local.parent}/activate\n"
            f"        atau: ln -s {local} ~/.local/bin/hermes-idx\n"
            f"        atau di Termux: pip install --no-build-isolation -e .",
            False,
        )
    else:
        detail, ok = (
            "hermes-idx tidak ditemukan di mana pun. Jalankan `bash install.sh`.",
            False,
        )
    checks.append(Check("CLI di PATH", ok, detail))
    if not binary and local.exists():
        binary = str(local)  # tetap uji kontrak JSON-nya lewat path langsung

    checks.append(Check(
        "Skill terpasang",
        target.exists(),
        f"terpasang di {target}" if target.exists()
        else f"belum ada di {target} — jalankan `hermes-idx agent install`.",
    ))

    skill_md = target / "SKILL.md"
    checks.append(Check(
        "SKILL.md terbaca",
        skill_md.exists(),
        "ok" if skill_md.exists() else f"{skill_md} tidak ditemukan",
    ))

    # Kontrak JSON diuji sungguhan, bukan diasumsikan.
    if binary:
        try:
            proc = subprocess.run(
                [binary, "agent", "info", "--json"],
                capture_output=True, text=True, timeout=30,
            )
            payload = json.loads(proc.stdout)
            ok = bool(payload.get("ok")) and payload.get("manifest", {}).get("skill") == SKILL_NAME
            checks.append(Check("Kontrak JSON", ok,
                                "stdout menghasilkan JSON valid" if ok
                                else f"keluaran tidak sesuai kontrak: {proc.stdout[:200]}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("Kontrak JSON", False, f"gagal memanggil CLI: {exc}"))
    else:
        checks.append(Check("Kontrak JSON", False, "dilewati — CLI tidak ditemukan"))

    hermes_home = SKILL_HOME.parent.parent
    checks.append(Check(
        "Direktori Hermes",
        hermes_home.exists(),
        f"{hermes_home} ada" if hermes_home.exists()
        else f"{hermes_home} belum ada. Kalau Hermes terpasang di lokasi lain, set "
             f"HERMES_HOME. Skill tetap bisa dipasang, tapi Hermes mungkin tidak memindainya.",
    ))
    return checks


def emit(payload: dict, as_json: bool) -> None:
    """Tulis payload ke stdout dalam format yang dipilih. Dipakai semua perintah CLI."""
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False, default=str)
        sys.stdout.write("\n")
