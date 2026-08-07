"""Antarmuka CLI (PRD §7). Setiap perintah punya `--json` untuk dikonsumsi Hermes agent."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import (agent, config as cfgmod, data as datamod, db, legacy, portfolio,
               screen, strategies as strat, universe)

app = typer.Typer(add_completion=False, help="Screener saham IDX untuk Termux.")
data_app = typer.Typer(help="Kelola data OHLCV.")
port_app = typer.Typer(help="Portofolio & rekomendasi jual.")
agent_app = typer.Typer(help="Integrasi Hermes Agent.")
app.add_typer(data_app, name="data")
app.add_typer(port_app, name="port")
app.add_typer(agent_app, name="agent")

console = Console()
HOME_OPT = typer.Option(None, "--home", help="Direktori data (default ~/.hermes-idx).")
JSON_OPT = typer.Option(False, "--json", help="Keluarkan JSON untuk konsumsi agent.")


def _ctx(home: Optional[Path]):
    cfg = cfgmod.load(home)
    return cfg, db.connect(cfgmod.db_path(home))


def _fail(message: str, as_json: bool) -> None:
    if as_json:
        agent.emit({"ok": False, "error": message}, True)
    else:
        console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _disclaimer() -> None:
    console.print(f"\n[dim]{agent.DISCLAIMER}[/dim]")


# --------------------------------------------------------------------------- setup

@app.command()
def init(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Buat konfigurasi & database."""
    cfg = cfgmod.load(home)
    cfg.save()
    conn = db.connect(cfgmod.db_path(home))
    conn.close()
    payload = {"ok": True, "config": str(cfg.path), "database": str(cfgmod.db_path(home))}
    agent.emit(payload, as_json)
    if not as_json:
        console.print(f"[green]Siap.[/green] Config: {cfg.path}\nDatabase: {cfgmod.db_path(home)}")


@app.command()
def config(key: str = typer.Argument(None), value: str = typer.Argument(None),
           home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Baca atau ubah konfigurasi. Tanpa argumen: tampilkan semua."""
    cfg = cfgmod.load(home)
    if key is None:
        agent.emit({"ok": True, "config": cfg.data}, as_json)
        if not as_json:
            console.print_json(data=cfg.data)
        return
    if value is None:
        agent.emit({"ok": True, "key": key, "value": cfg.get(key)}, as_json)
        if not as_json:
            console.print(f"{key} = {cfg.get(key)}")
        return
    parsed: object = value
    try:
        parsed = int(value)
    except ValueError:
        try:
            parsed = float(value)
        except ValueError:
            if value.lower() in ("true", "false"):
                parsed = value.lower() == "true"
    cfg.set(key, parsed)
    cfgmod._validate(cfg)
    cfg.save()
    agent.emit({"ok": True, "key": key, "value": parsed}, as_json)
    if not as_json:
        console.print(f"[green]{key} = {parsed}[/green]")


# --------------------------------------------------------------------------- data

@data_app.command("update")
def data_update(full: bool = typer.Option(False, "--full", help="Full refresh, bukan incremental."),
                tickers: Optional[str] = typer.Option(None, "--tickers", help="Daftar dipisah koma."),
                home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Ambil bar baru dari sumber data."""
    cfg, conn = _ctx(home)
    source = datamod.YahooSource(cfg.data["data"]["rate_limit_per_sec"])
    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        rows = conn.execute("SELECT ticker FROM emiten WHERE is_active = 1").fetchall()
        symbols = [r["ticker"] for r in rows]
        if not symbols:
            _fail("Daftar emiten kosong. Isi dulu dengan `hermes-idx data seed`.", as_json)

    # Emiten yang SEDANG DIPEGANG wajib ikut, walau di luar universe. Tanpa ini posisi
    # non-bluechip tidak punya data harga sama sekali — tidak bisa dinilai SL/TP-nya dan
    # hilang dari review harian. Posisi tak terpantau lebih berbahaya daripada posisi rugi.
    held = [r["ticker"] for r in conn.execute("SELECT ticker FROM posisi WHERE lot > 0")]
    symbols = list(dict.fromkeys([cfg.data["data"]["benchmark"], *symbols, *held]))

    results = datamod.update(conn, symbols, source, cfg.data["data"]["history_years"], full)
    failed = [t for t, n in results.items() if n < 0]
    payload = {"ok": True, "updated": len(results), "failed": failed,
               "bars": sum(n for n in results.values() if n > 0)}
    agent.emit(payload, as_json)
    if not as_json:
        console.print(f"[green]{payload['bars']} bar[/green] dari {len(results)} ticker."
                      + (f" [red]{len(failed)} gagal.[/red]" if failed else ""))


@data_app.command("seed-bluechip")
def data_seed_bluechip(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Isi daftar emiten dari universe bluechip kurasi (dari script v3)."""
    _, conn = _ctx(home)
    count = universe.seed(conn)
    agent.emit({"ok": True, "emiten": count}, as_json)
    if not as_json:
        console.print(f"[green]{count} emiten bluechip[/green] tercatat. "
                      f"Lanjut: `hermes-idx data update`.")


@data_app.command("seed-idx")
def data_seed_idx(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Isi daftar emiten dari SELURUH papan IDX (TradingView Scanner, ~843 emiten)."""
    _, conn = _ctx(home)
    try:
        rows = universe.fetch_all()
    except Exception as exc:  # noqa: BLE001 - jaringan/format berubah, jangan bikin traceback
        _fail(f"gagal mengambil daftar emiten IDX: {exc}", as_json)
        return
    if not rows:
        _fail("daftar emiten IDX kosong — endpoint scanner mungkin berubah.", as_json)
        return
    count = universe.seed(conn, rows)
    agent.emit({"ok": True, "emiten": count}, as_json)
    if not as_json:
        console.print(f"[green]{count} emiten IDX[/green] tercatat. "
                      f"Lanjut: `hermes-idx data update` (butuh ~30 menit untuk 5 tahun "
                      f"riwayat semua emiten).")


@port_app.command("import-legacy")
def port_import_legacy(
    scripts_dir: Optional[Path] = typer.Option(None, "--from",
                                               help="Default ~/.hermes/scripts"),
    home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Impor portfolio.json & riwayat sinyal dari script Hermes v3."""
    cfg, conn = _ctx(home)
    paths = legacy.legacy_paths(scripts_dir)
    payload: dict = {"ok": True, "imported": 0, "notes": [], "legacy_stats": None}

    if paths["portfolio"].exists():
        count, notes = legacy.import_portfolio(conn, paths["portfolio"], cfg.fees)
        payload["imported"] = count
        payload["notes"] = notes
    else:
        payload["notes"].append(f"tidak ditemukan: {paths['portfolio']}")

    if paths["signal_history"].exists():
        payload["legacy_stats"] = legacy.import_signal_history(conn, paths["signal_history"])

    agent.emit(payload, as_json)
    if as_json:
        return
    console.print(f"[green]{payload['imported']} posisi[/green] diimpor.")
    for note in payload["notes"]:
        console.print(f"[yellow]⚠ {note}[/yellow]")
    if payload["legacy_stats"]:
        console.print("\n[dim]Akurasi versi lama (disimpan sebagai catatan, BUKAN dilebur "
                      "ke statistik):[/dim]")
        for action, node in payload["legacy_stats"].items():
            if node["total"]:
                console.print(f"  {action}: {node['benar']}/{node['total']} "
                              f"({node['akurasi_pct']}%)")
        console.print("[dim]Diukur sebagai arah harga keesokan hari — tidak "
                      "memperhitungkan SL kena maupun biaya. Lebih optimis dari "
                      "expectancy sungguhan.[/dim]")


@data_app.command("seed")
def data_seed(file: Path = typer.Argument(..., help="CSV berkolom ticker,nama,sektor,papan."),
              home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Isi daftar emiten dari CSV (DL-1 sementara — sumber IDX resmi belum ada, issue #4)."""
    import csv

    _, conn = _ctx(home)
    if not file.exists():
        _fail(f"file tidak ditemukan: {file}", as_json)
    count = 0
    with file.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            conn.execute(
                "INSERT INTO emiten (ticker, nama, sektor, papan) VALUES (?,?,?,?)"
                " ON CONFLICT(ticker) DO UPDATE SET nama=excluded.nama, sektor=excluded.sektor",
                (ticker, row.get("nama"), row.get("sektor"), row.get("papan")),
            )
            count += 1
    conn.commit()
    agent.emit({"ok": True, "emiten": count}, as_json)
    if not as_json:
        console.print(f"[green]{count} emiten[/green] tercatat.")


@data_app.command("intraday")
def data_intraday(interval: str = typer.Option("60m", "--interval",
                                               help="1m, 5m, 15m, 30m, atau 60m."),
                  tickers: Optional[str] = typer.Option(None, "--tickers",
                                                        help="Daftar dipisah koma."),
                  home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Ambil bar intraday (untuk strategi & backtest intraday).

    Kedalaman ditentukan Yahoo, bukan kita: 5m/15m/30m hanya 60 hari bursa, 60m sampai
    ~3 tahun. Interval kecil karena itu TIDAK cukup untuk menyimpulkan edge.
    """
    cfg, conn = _ctx(home)
    if interval not in datamod.INTRADAY_LIMITS:
        _fail(f"interval '{interval}' tidak didukung "
              f"(pilih: {', '.join(datamod.INTRADAY_LIMITS)})", as_json)
    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    else:
        rows = conn.execute("SELECT ticker FROM emiten WHERE is_active = 1").fetchall()
        symbols = [r["ticker"] for r in rows]
        if not symbols:
            _fail("Daftar emiten kosong. Jalankan `hermes-idx data seed-bluechip`.", as_json)
    held = [r["ticker"] for r in conn.execute("SELECT ticker FROM posisi WHERE lot > 0")]
    symbols = list(dict.fromkeys([cfg.data["data"]["benchmark"], *symbols, *held]))

    results = datamod.update_intraday(conn, symbols, interval)
    failed = [t for t, n in results.items() if n < 0]
    bars = sum(n for n in results.values() if n > 0)
    payload = {"ok": True, "interval": interval, "cakupan": datamod.INTRADAY_LIMITS[interval],
               "ticker": len(results), "failed": failed, "bars": bars}
    agent.emit(payload, as_json)
    if not as_json:
        console.print(f"[green]{bars:,} bar {interval}[/green] dari {len(results)} ticker "
                      f"(cakupan maksimum {datamod.INTRADAY_LIMITS[interval]})."
                      + (f" [red]{len(failed)} gagal.[/red]" if failed else ""))
        if interval != "60m":
            console.print("[yellow]Catatan: interval ini hanya menjangkau 60 hari bursa — "
                          "terlalu pendek untuk menyimpulkan edge. Pakai 60m untuk "
                          "backtest yang bisa dipercaya.[/yellow]")


@data_app.command("status")
def data_status(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Umur data, jumlah emiten, cakupan."""
    cfg, conn = _ctx(home)
    row = conn.execute(
        "SELECT COUNT(DISTINCT ticker) AS t, COUNT(*) AS bars, MAX(date) AS last FROM ohlcv"
    ).fetchone()
    age = datamod.data_age_days(conn)
    payload = {"ok": True, "tickers": row["t"], "bars": row["bars"], "last_date": row["last"],
               "data_age_days": age, "stale_after_days": cfg.data["data"]["stale_after_days"]}
    agent.emit(payload, as_json)
    if not as_json:
        console.print(f"{row['t']} ticker · {row['bars']:,} bar · terakhir {row['last']} "
                      f"({age} hari lalu)" if row["last"] else "Database kosong.")


# --------------------------------------------------------------------------- screening

@app.command()
def scan(strategy: Optional[str] = typer.Option(None, "--strategy", help="Dipisah koma."),
         min_score: Optional[float] = typer.Option(None, "--min-score"),
         top: Optional[int] = typer.Option(None, "--top"),
         home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Screening seluruh universe."""
    cfg, conn = _ctx(home)
    names = [s.strip() for s in strategy.split(",")] if strategy else None
    try:
        found, warnings = screen.scan(conn, cfg, names, min_score, top)
    except KeyError as exc:
        _fail(str(exc), as_json)
        return

    payload = {"ok": True, "count": len(found), "warnings": warnings,
               "disclaimer": agent.DISCLAIMER,
               "signals": [s.as_row() for s in found]}
    agent.emit(payload, as_json)
    if as_json:
        return

    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")
    if not found:
        console.print("Tidak ada sinyal yang lolos ambang skor hari ini.")
        return
    for sig in found:
        console.print(Panel(_signal_text(sig), title=f"{sig.ticker} · SKOR {sig.score:.0f}/100",
                            border_style="cyan"))
    _disclaimer()


def _signal_text(sig) -> str:
    return (
        f"[bold]Strategi[/bold] {sig.strategy}\n\n"
        f"Entry      Rp {sig.entry_price:,}  (zona {sig.entry_zone_low:,}–"
        f"{sig.entry_zone_high:,}, {sig.entry_type})\n"
        f"Stop Loss  Rp {sig.stop_loss:,}  ({sig.sl_pct:.1f}%)\n"
        f"TP 1       Rp {sig.tp1:,}  (R:R {sig.rr_tp1:.1f})  → jual {sig.tp1_size:.0%}\n"
        f"TP 2       Rp {sig.tp2:,}  (R:R {sig.rr_tp2:.1f})  → jual {sig.tp2_size:.0%}\n\n"
        f"Size       {sig.position_lot} lot · risiko Rp {sig.risk_rp:,.0f}\n"
        f"Valid s/d  {sig.valid_until}\n\n"
        f"[dim]{sig.notes}[/dim]"
    )


@app.command()
def analyze(ticker: str, explain: bool = typer.Option(False, "--explain"),
            home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Analisis satu emiten. Berlaku untuk emiten apa pun, termasuk yang tidak likuid."""
    cfg, conn = _ctx(home)
    ticker = ticker.upper()
    frame = datamod.load_ohlcv(conn, ticker)
    if frame.empty:
        _fail(f"tidak ada data untuk {ticker}", as_json)

    ctx = screen.context(conn, cfg)
    prepared = strat.REGISTRY["breakout"].prepare(frame, ctx)
    last = prepared.iloc[-1]
    eligible, _ = datamod.universe(conn, cfg)
    triggered = []
    for s in strat.get(None):
        prepared_s = s.prepare(frame, ctx)
        # momentum_rs butuh rs_rank (cross-sectional) — tidak tersedia untuk single ticker.
        if "rs_rank" not in prepared_s.columns:
            prepared_s["rs_rank"] = float("nan")
        if bool(s.entry_signal(prepared_s).iloc[-1]):
            triggered.append(s.name)
    payload = {
        "ok": True, "ticker": ticker, "date": str(prepared.index[-1].date()),
        "close": float(last["close"]),
        "ema20": _num(last["ema20"]), "ema50": _num(last["ema50"]), "ema200": _num(last["ema200"]),
        "rsi14": _num(last["rsi14"]), "atr14": _num(last["atr14"]),
        "in_universe": ticker in eligible,
        "universe_note": None if ticker in eligible else
        "Tidak lolos filter likuiditas — sistem tidak akan merekomendasikan beli untuk "
        "emiten ini, meski analisisnya tetap ditampilkan.",
        "strategies_triggered": triggered,
        "disclaimer": agent.DISCLAIMER,
    }
    agent.emit(payload, as_json)
    if as_json:
        return
    table = Table(title=f"{ticker} — {payload['date']}")
    table.add_column("Metrik")
    table.add_column("Nilai", justify="right")
    for key in ("close", "ema20", "ema50", "ema200", "rsi14", "atr14"):
        table.add_row(key, f"{payload[key]:,.1f}" if payload[key] is not None else "—")
    console.print(table)
    if payload["universe_note"]:
        console.print(f"[yellow]⚠ {payload['universe_note']}[/yellow]")
    console.print(f"Strategi ter-trigger: {', '.join(triggered) or 'tidak ada'}")
    _disclaimer()


def _num(value):
    import math
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)


@app.command()
def daily(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT,
          update: bool = typer.Option(False, "--update", help="Update data dulu."),
          morning: bool = typer.Option(False, "--morning", help="Mode pagi: porto + rekomendasi beli."),
          afternoon: bool = typer.Option(False, "--afternoon", help="Mode sore: review porto saja, tanpa rekomendasi.")):
    """Laporan harian lengkap: rezim pasar, porto, aksi, sinyal. Untuk cron/Hermes."""
    from . import daily as dailymod

    cfg, conn = _ctx(home)
    if update:
        source = datamod.YahooSource(cfg.data["data"]["rate_limit_per_sec"])
        rows = conn.execute("SELECT ticker FROM emiten WHERE is_active = 1").fetchall()
        # Saham yang DIPEGANG wajib ikut, sama seperti `data update` — tanpa ini posisi di
        # luar tabel emiten (WIDI, BMTR) tidak punya bar baru justru di perintah yang
        # paling sering dipanggil cron, lalu SL/TP-nya dinilai dari data basi.
        held = [r["ticker"] for r in conn.execute("SELECT ticker FROM posisi WHERE lot > 0")]
        symbols = list(dict.fromkeys(
            [cfg.data["data"]["benchmark"], *(r["ticker"] for r in rows), *held]))
        datamod.update(conn, symbols, source, cfg.data["data"]["history_years"])

    mode = "morning" if morning else ("afternoon" if afternoon else "full")
    report = dailymod.build(conn, cfg)
    # Mode sore = review saja. Dulu `mode` hanya dipakai render teks, jadi agent yang
    # membaca --json tetap menerima daftar sinyal beli di sore hari.
    if mode == "afternoon":
        report["sinyal"] = []
    report["mode"] = mode
    agent.emit({"ok": True, **report}, as_json)
    if not as_json:
        console.print(dailymod.render_text(report, mode=mode))


@app.command("compare")
def compare_cmd(strategy: Optional[str] = typer.Option(None, "--strategy",
                                                       help="Dipisah koma. Default: semua."),
                home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Adu semua strategi pada universe, periode, dan biaya yang sama."""
    from . import compare as cmp

    cfg, conn = _ctx(home)
    names = [s.strip() for s in strategy.split(",")] if strategy else None
    try:
        entries = cmp.run(conn, cfg, names)
    except KeyError as exc:
        _fail(str(exc), as_json)
        return
    winner, explanation = cmp.recommendation(entries)

    agent.emit({"ok": True, "winner": winner, "explanation": explanation,
                "ranking": [e.as_dict() for e in entries],
                "disclaimer": agent.DISCLAIMER}, as_json)
    if as_json:
        return

    table = Table(title="Adu strategi — peringkat pakai expectancy, BUKAN win rate")
    for col in ("STRATEGI", "TRADE", "WIN%", "EXPECT", "PF", "MAX DD", "p", "SKOR", "VONIS"):
        table.add_column(col, justify="left" if col in ("STRATEGI", "VONIS") else "right")
    for e in entries:
        m = e.metrics
        if not m.get("total_trades"):
            table.add_row(e.strategy, "0", "—", "—", "—", "—", "—", "—", e.verdict)
            continue
        table.add_row(
            e.strategy, str(m["total_trades"]), f"{m['win_rate']:.0f}",
            f"{m['expectancy']:+.3f}", str(m["profit_factor"]), f"{m['max_dd']:.1f}%",
            str(e.p_value), f"{e.composite:.2f}", e.verdict,
        )
    console.print(table)
    console.print(f"\n[bold]{explanation}[/bold]")
    console.print("[dim]Win rate ditampilkan sebagai informasi, bukan dasar peringkat: "
                  "win rate tinggi mudah dibuat dengan TP dekat + SL lebar, dan itu "
                  "justru merugi setelah biaya.[/dim]")
    _disclaimer()


@app.command()
def backtest(strategy: str = typer.Option(..., "--strategy"),
             home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Rolling out-of-sample backtest (lihat catatan metodologi di backtest.py)."""
    from . import backtest as bt

    cfg, conn = _ctx(home)
    try:
        result = screen.run_backtest(conn, cfg, strategy)
    except KeyError as exc:
        _fail(str(exc), as_json)
        return

    metrics = result.metrics(cfg.risk_pct)
    payload = {"ok": True, "strategy": strategy, "method": result.method,
               "period": [str(result.period_start), str(result.period_end)],
               "metrics": metrics, "p_value": bt.bootstrap_pvalue(result.trades),
               "folds": bt.fold_consistency(result.trades), "warnings": result.warnings}
    agent.emit(payload, as_json)
    if as_json:
        return
    if not metrics.get("total_trades"):
        console.print("[yellow]Tidak ada trade pada periode ini.[/yellow]")
        return
    table = Table(title=f"{strategy} — {result.method} ({result.period_start} s/d {result.period_end})")
    table.add_column("Metrik")
    table.add_column("Nilai", justify="right")
    for key, value in metrics.items():
        table.add_row(key, str(value))
    if payload["p_value"] is not None:
        table.add_row("p_value", str(payload["p_value"]))
    console.print(table)
    if metrics.get("unfilled_ara"):
        console.print(f"[yellow]{metrics['unfilled_ara']} sinyal tidak bisa dieksekusi "
                      f"karena ARA — sudah dikeluarkan dari hasil.[/yellow]")
    if payload["p_value"] is not None and payload["p_value"] > 0.05:
        console.print("[yellow]TIDAK SIGNIFIKAN — sampel belum cukup untuk menyimpulkan "
                      "ada edge.[/yellow]")
    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")


# --------------------------------------------------------------------------- portofolio

@port_app.command("add")
def port_add(ticker: str, lot: int = typer.Option(..., "--lot"),
             price: float = typer.Option(..., "--price"),
             date: Optional[str] = typer.Option(None, "--date"),
             home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Catat pembelian."""
    cfg, conn = _ctx(home)
    when = dt.date.fromisoformat(date) if date else dt.date.today()
    try:
        portfolio.record(conn, ticker.upper(), when, "BUY", lot, price, cfg.fees)
    except ValueError as exc:
        _fail(str(exc), as_json)
    agent.emit({"ok": True, "ticker": ticker.upper(), "lot": lot, "price": price}, as_json)
    if not as_json:
        console.print(f"[green]BUY {ticker.upper()} {lot} lot @ {price:,.0f}[/green]")


@port_app.command("sell")
def port_sell(ticker: str, lot: int = typer.Option(..., "--lot"),
              price: float = typer.Option(..., "--price"),
              date: Optional[str] = typer.Option(None, "--date"),
              home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Catat penjualan."""
    cfg, conn = _ctx(home)
    when = dt.date.fromisoformat(date) if date else dt.date.today()
    try:
        portfolio.record(conn, ticker.upper(), when, "SELL", lot, price, cfg.fees)
    except ValueError as exc:
        _fail(str(exc), as_json)
    agent.emit({"ok": True, "ticker": ticker.upper(), "lot": lot, "price": price}, as_json)
    if not as_json:
        console.print(f"[green]SELL {ticker.upper()} {lot} lot @ {price:,.0f}[/green]")


@port_app.command("plan")
def port_plan(ticker: str, stop_loss: Optional[float] = typer.Option(None, "--sl"),
              tp1: Optional[float] = typer.Option(None, "--tp1"),
              tp2: Optional[float] = typer.Option(None, "--tp2"),
              home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """PT-10: lengkapi SL/TP untuk posisi yang belum punya rencana."""
    _, conn = _ctx(home)
    portfolio.set_plan(conn, ticker.upper(), stop_loss, tp1, tp2)
    agent.emit({"ok": True, "ticker": ticker.upper()}, as_json)
    if not as_json:
        console.print(f"[green]Rencana {ticker.upper()} diperbarui.[/green]")


@port_app.command("show")
def port_show(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Posisi terbuka + unrealized P/L."""
    cfg, conn = _ctx(home)
    rows = []
    for pos in portfolio.positions(conn):
        frame = datamod.load_ohlcv(conn, pos.ticker)
        last = float(frame["close"].iloc[-1]) if not frame.empty else pos.avg_price
        pnl = (last - pos.avg_price) * pos.lot * 100
        rows.append({"ticker": pos.ticker, "lot": pos.lot, "avg_price": pos.avg_price,
                     "last": last, "pnl_rp": round(pnl, 0),
                     "pnl_pct": round((last / pos.avg_price - 1) * 100, 2),
                     "stop_loss": pos.stop_loss, "no_stop_loss": pos.stop_loss is None})
    agent.emit({"ok": True, "positions": rows,
                "total_pnl_rp": round(sum(r["pnl_rp"] for r in rows), 0)}, as_json)
    if as_json:
        return
    if not rows:
        console.print("Belum ada posisi.")
        return
    table = Table()
    for column in ("TICKER", "LOT", "AVG", "LAST", "P/L", "%"):
        table.add_column(column, justify="right" if column != "TICKER" else "left")
    for row in rows:
        flag = " ⚠" if row["no_stop_loss"] else ""
        table.add_row(row["ticker"] + flag, str(row["lot"]), f"{row['avg_price']:,.0f}",
                      f"{row['last']:,.0f}", f"{row['pnl_rp']:,.0f}", f"{row['pnl_pct']:+.1f}%")
    console.print(table)
    if any(r["no_stop_loss"] for r in rows):
        console.print("[yellow]⚠ = TANPA STOP LOSS. Isi dengan `port plan <TICKER> --sl <harga>`.[/yellow]")


@port_app.command("review")
def port_review(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Rekomendasi jual untuk semua posisi."""
    cfg, conn = _ctx(home)
    actions = []
    for pos in portfolio.positions(conn):
        frame = datamod.load_ohlcv(conn, pos.ticker)
        if frame.empty:
            continue
        act = portfolio.review(pos, frame, cfg)
        actions.append(act.__dict__)
    agent.emit({"ok": True, "actions": actions, "disclaimer": agent.DISCLAIMER}, as_json)
    if as_json:
        return
    if not actions:
        console.print("Belum ada posisi untuk direview.")
        return
    table = Table()
    for column in ("TICKER", "AKSI", "URGENSI", "P/L", "CATATAN"):
        table.add_column(column)
    for act in actions:
        style = "red" if act["action"] == "CUT_LOSS" else ""
        table.add_row(act["ticker"], f"[{style}]{act['action']}[/{style}]" if style else act["action"],
                      act["urgency"], f"{act['pnl_pct']:+.1f}%", act["reason"])
    console.print(table)
    _disclaimer()


@port_app.command("stats")
def port_stats(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Statistik personal, dengan cakupan data eksplisit (PT-11)."""
    _, conn = _ctx(home)
    result = portfolio.stats(conn)
    agent.emit({"ok": True, **result}, as_json)
    if not as_json:
        for key, value in result.items():
            console.print(f"{key}: {value}")


# --------------------------------------------------------------------------- agent

@agent_app.command("info")
def agent_info(as_json: bool = JSON_OPT):
    """Manifest kemampuan yang dibaca Hermes."""
    agent.emit({"ok": True, "manifest": agent.manifest()}, as_json)
    if not as_json:
        console.print_json(data=agent.manifest())


@agent_app.command("install")
def agent_install(force: bool = typer.Option(False, "--force"),
                  target: Optional[Path] = typer.Option(None, "--target"),
                  as_json: bool = JSON_OPT):
    """Pasang skill ke ~/.hermes/skills/idx-screener."""
    try:
        path = agent.install(target, force)
    except (FileExistsError, FileNotFoundError) as exc:
        _fail(str(exc), as_json)
        return
    agent.emit({"ok": True, "installed_to": str(path)}, as_json)
    if not as_json:
        console.print(f"[green]Skill terpasang di {path}[/green]\n"
                      f"Verifikasi dengan `hermes-idx agent verify`.")


@agent_app.command("verify")
def agent_verify(as_json: bool = JSON_OPT):
    """Periksa apakah Hermes benar-benar bisa memanggil aplikasi ini."""
    checks = agent.verify()
    ok = all(c.ok for c in checks)
    agent.emit({"ok": ok, "checks": [c.as_dict() for c in checks]}, as_json)
    if as_json:
        raise typer.Exit(0 if ok else 1)
    for check in checks:
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def doctor(home: Optional[Path] = HOME_OPT, as_json: bool = JSON_OPT):
    """Diagnostik: dependensi, data, database, dan koneksi agent."""
    import sys

    checks = [agent.Check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])]
    for module in ("pandas", "numpy", "httpx", "yaml", "typer", "rich"):
        try:
            __import__(module)
            checks.append(agent.Check(f"modul {module}", True, "ok"))
        except ImportError as exc:
            checks.append(agent.Check(f"modul {module}", False, str(exc)))

    try:
        cfg, conn = _ctx(home)
        age = datamod.data_age_days(conn)
        checks.append(agent.Check("database", True, str(cfgmod.db_path(home))))
        checks.append(agent.Check(
            "kesegaran data",
            age is not None and age <= cfg.data["data"]["stale_after_days"],
            f"{age} hari" if age is not None else "database kosong — jalankan `data update`",
        ))
        universe, warnings = datamod.universe(conn, cfg)
        checks.append(agent.Check("universe", bool(universe),
                                  f"{len(universe)} emiten lolos filter"))
        for warning in warnings:
            checks.append(agent.Check("catatan data", True, warning))

        # MCP TradingView (opsional) — cek ketersediaan server kalau dikonfigurasi.
        mcp_cmd = cfg.get("mcp.command", "")
        if mcp_cmd:
            from . import mcp as mcpmod
            quotes = mcpmod.fetch_quotes(["BBCA"], mcp_cmd, timeout=30)
            checks.append(agent.Check(
                "MCP TradingView", bool(quotes),
                f"harga BBCA via MCP: {quotes['BBCA']['price']:,.0f}" if quotes
                else "server tidak merespons — pastikan tradingview-mcp terpasang",
            ))
        else:
            checks.append(agent.Check(
                "MCP TradingView", True,
                "tidak dikonfigurasi (mcp.command kosong) — harga cadangan mati",
            ))
    except Exception as exc:  # noqa: BLE001
        checks.append(agent.Check("database", False, str(exc)))

    checks.extend(agent.verify())
    ok = all(c.ok for c in checks)
    agent.emit({"ok": ok, "checks": [c.as_dict() for c in checks]}, as_json)
    if as_json:
        return
    for check in checks:
        mark = "[green]✓[/green]" if check.ok else "[red]✗[/red]"
        console.print(f"{mark} {check.name}: {check.detail}")


if __name__ == "__main__":
    app()
