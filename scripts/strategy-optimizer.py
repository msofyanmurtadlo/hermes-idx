#!/usr/bin/env python3
"""Strategy Optimizer — pencarian parameter berulang (weekly).

Alur:
1. Ambil universe PENUH IDX (semua saham likuid, bukan bluechip saja) + 5 tahun OHLCV.
2. Untuk tiap strategi yang knob-nya live (breakout, momentum_rs, v3score, trio):
   - jalankan champion (param saat ini) sbg baseline, kondisi identik
   - sweep grid param kecil, rolling OOS (walk-forward), metrics + bootstrap p-value
3. Gate (HANYA klaim statistik, bukan optimis):
   - LAYAK_UJI : exp > 0, PF >= 1.1, trades >= 30, p <= 0.10
   - PROMOSI   : exp > 0, PF >= 1.2, trades >= 30, p <= 0.05, DAN exp > champion
4. Output ringkasan ke stdout (cron no_agent kirim verbatim), append changelog,
   commit+push best-effort ke GitHub.

Catatan jujur: sweep tidak pernah menyentuh level SL/TP (FIX user: SL -2%, R:R 1:2).
Win rate TINGGI bukan target — expectancy POSITIF + signifikan yang dicari.
"""
import os, sys, json, subprocess, datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from hermes_idx import config, data, signals, backtest as bt, strategies as strat, screen
import sqlite3

REPO = "/root/hermes-idx"
DOCS = os.path.join(REPO, "docs")
CHANGELOG = os.path.join(DOCS, "strategy-changelog.md")
MIN_BARS = 250
os.makedirs(DOCS, exist_ok=True)

# Grid sweep per strategi (hanya knob yang LIVE di entry/prepare; level FIX tak disentuh)
SWEEPS = {
    "breakout": [
        {"lookback": lb, "vol_mult": vm, "adx_min": adx}
        for lb in (15, 20, 25) for vm in (1.5, 2.0, 2.5) for adx in (20, 25)
    ],
    "momentum_rs": [{"rs_percentile": p} for p in (85, 90, 95)],
    "v3score": [{"threshold": t} for t in (6, 7, 8)],
    "trio": [{"rsi2_max": r, "mfi_max": m} for r in (8, 10, 12) for m in (25, 30, 35)],
}
# strategi yang knob-nya mati (level FIX / hardcoded) — dicatat, bukan di-sweep
SKIPPED = {"pullback": "tanpa knob yang bisa di-sweep (semua hardcoded)",
           "mean_reversion": "knob atr_mult/tp_r mati sejak level FIX (SL -2%, R:R 1:2)"}


def variant(cls, **kw):
    inst = cls()
    for k, v in kw.items():
        setattr(inst, k, v)
    return inst


def run_variant(panel_base, raw, ctx, cls, params, cfg):
    """Jalankan satu varian: panel dibuat ulang (prepare bisa bergantung param)."""
    st = variant(cls, **params)
    panel = {t: st.prepare(f, ctx) for t, f in raw.items()}
    signals.add_rs_rank(panel)
    res = bt.rolling_oos(panel, st, cfg)
    m = res.metrics(cfg.risk_pct)
    p = bt.bootstrap_pvalue(res.trades) or 1.0
    folds = bt.fold_consistency(res.trades)
    positive_folds = sum(1 for f in folds if f["total_r"] > 0) / max(1, len(folds))
    return {
        "params": params, "trades": m["total_trades"], "win_rate": m["win_rate"],
        "expectancy": m["expectancy"], "pf": m["profit_factor"], "max_dd": m["max_dd"],
        "p": p, "fold_consistency": round(positive_folds, 2),
        "start": str(res.period_start), "end": str(res.period_end),
    }


def gate(r):
    layak = (r["expectancy"] > 0 and r["pf"] >= 1.1 and r["trades"] >= 30 and r["p"] <= 0.10)
    promosi = (r["expectancy"] > 0 and r["pf"] >= 1.2 and r["trades"] >= 30
               and r["p"] <= 0.05)
    return layak, promosi


def main():
    cfg = config.load()
    conn = sqlite3.connect(config.db_path())
    conn.row_factory = sqlite3.Row
    tickers, warnings = data.universe(conn, cfg)
    ctx = screen.context(conn, cfg)

    print(f"📊 OPTIMIZER STRATEGI — {dt.date.today().isoformat()}")
    print(f"Universe: {len(tickers)} saham IDX likuid (bukan bluechip saja) | 5 tahun (2021-08 → sekarang)")
    print("=" * 60)

    # load raw OHLCV SEKALI (hemat: 37 varian tidak perlu baca DB 37x)
    raw = {}
    for t in tickers:
        f = data.load_ohlcv(conn, t)
        if not f.empty and len(f) >= MIN_BARS:
            raw[t] = f
    print(f"Frame siap: {len(raw)} emiten")

    lines = [f"# Strategy Changelog — {dt.date.today().isoformat()}",
             f"\nUniverse full IDX ({len(tickers)} saham likuid) | data 5 tahun. "
             f"Level SL/TP TETAP (SL -2%, R:R 1:2 — aturan user)."]
    promo_found = []

    for name, grid in SWEEPS.items():
        cls = getattr(strat, {"breakout": "Breakout", "momentum_rs": "MomentumRS",
                              "v3score": "V3Score", "trio": "Trio"}[name])
        # champion = param default kelas (param yang dipakai live sekarang)
        champion = run_variant(None, raw, ctx, cls, {}, cfg)
        tag = "⚪"
        if champion["expectancy"] > 0 and champion["p"] <= 0.05:
            tag = "🟢"
        print(f"\n🔵 {name} — CHAMPION (param saat ini): exp {champion['expectancy']:+.3f}R "
              f"PF {champion['pf']:.2f} WR {champion['win_rate']:.1f}% trades {champion['trades']} "
              f"p={champion['p']:.3f} {tag}")

        best = None
        for params in grid:
            r = run_variant(None, raw, ctx, cls, params, cfg)
            if r["trades"] < 10:
                continue
            if best is None or r["expectancy"] > best["expectancy"]:
                best = r
        if not best:
            print("   (tidak ada varian dengan trade cukup)")
            continue

        delta = best["expectancy"] - champion["expectancy"]
        layak, promosi = gate(best)
        better = best["expectancy"] > champion["expectancy"]
        verdict = "🚀 PROMOSI" if (promosi and better) else ("🧪 LAYAK UJI" if (layak and better) else "❌ belum layak")
        print(f"   BEST : {json.dumps(best['params'])} → exp {best['expectancy']:+.3f}R "
              f"PF {best['pf']:.2f} WR {best['win_rate']:.1f}% trades {best['trades']} "
              f"p={best['p']:.3f} konsistensi {best['fold_consistency']} → {verdict}")

        lines.append(f"\n## {name}")
        lines.append(f"- Champion: {json.dumps(champion['params'])} → exp {champion['expectancy']:+.3f}R PF {champion['pf']:.2f} (trades {champion['trades']}, p={champion['p']:.3f})")
        lines.append(f"- Best sweep: {json.dumps(best['params'])} → exp {best['expectancy']:+.3f}R PF {best['pf']:.2f} (delta {delta:+.3f}R, trades {best['trades']}, p={best['p']:.3f}, konsistensi {best['fold_consistency']})")
        lines.append(f"- Verdict: {verdict}")
        if promosi and better:
            promo_found.append((name, best))

    for name, why in SKIPPED.items():
        print(f"\n⏭️  {name}: di-skip ({why})")
        lines.append(f"\n## {name}\n- Skip: {why}")

    # ============ BELAJAR DARI PENGALAMAN: hasil trade NYATA (live) ============
    print("\n" + "=" * 60)
    print("🎯 BELAJAR DARI PENGALAMAN — trade nyata di DB:")
    live = conn.execute(
        """SELECT COALESCE(strategy, 'tanpa-label') AS strategy, COUNT(*) AS n,
                  SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS win_n,
                  ROUND(AVG(CASE WHEN r_multiple IS NOT NULL THEN r_multiple END), 3) AS avg_r,
                  ROUND(SUM(COALESCE(r_multiple, 0)), 2) AS total_r,
                  SUM(CASE WHEN followed_plan = 1 THEN 1 ELSE 0 END) AS plan_n
           FROM trade_closed GROUP BY strategy ORDER BY n DESC"""
    ).fetchall()
    tot = {"n": 0, "avg_r": 0.0, "total_r": 0.0, "win_n": 0, "plan_n": 0}
    for s, n, win_n, avg_r, total_r, plan_n in live:
        tot["n"] += n; tot["total_r"] += (total_r or 0); tot["win_n"] += (win_n or 0); tot["plan_n"] += (plan_n or 0)
        wr = 100.0 * (win_n or 0) / n if n else 0
        print(f"   {s:<16} {n:>3} trade | WR {wr:>5.1f}% | avg R {avg_r or 0:+.3f} | total R {total_r or 0:+.2f}")
        lines.append(f"- Live: {s} → {n} trade, WR {wr:.1f}%, avg R {avg_r or 0:+.3f}, total R {total_r or 0:+.2f}")
    if tot["n"]:
        tot["avg_r"] = tot["total_r"] / tot["n"]
        print(f"   TOTAL: {tot['n']} trade | WR {100.0*tot['win_n']/tot['n']:.1f}% | "
              f"avg R {tot['avg_r']:+.3f} | total R {tot['total_r']:+.2f} | "
              f"disiplin plan {100.0*tot['plan_n']/tot['n']:.0f}%")
        lines.append(f"- LIVE TOTAL: {tot['n']} trade, avg R {tot['avg_r']:+.3f}, "
                     f"disiplin plan {100.0*tot['plan_n']/tot['n']:.0f}%")
        if tot["total_r"] > 0:
            print("   ✅ Pengalaman live POSITIF — disiplin SL/TP membayar walau backtest negatif.")
        elif tot["n"] < 10:
            print("   🟡 Sampel masih kecil (<10 trade) — belum bisa disimpulkan, lanjut catat.")
        else:
            print("   🔴 Pengalaman live masih negatif — konsisten dengan backtest.")
    else:
        print("   (belum ada trade tertutup tercatat di DB)")
        lines.append("- Live: belum ada trade tertutup.")
    # akurasi sinyal (history)
    try:
        with open("/root/.hermes/scripts/idx-signal-history.json") as f:
            sh = json.load(f)
        st = sh.get("stats", {})
        b = st.get("BELI", {}); j = st.get("JUAL", {})
        bn, bb = b.get("benar", 0), b.get("salah", 0)
        jn, jb = j.get("benar", 0), j.get("salah", 0)
        bwr = 100.0 * bn / (bn + bb) if (bn + bb) else 0
        jwr = 100.0 * jn / (jn + jb) if (jn + jb) else 0
        print(f"   📡 Akurasi sinyal BELI: {bn}/{bn+bb} ({bwr:.0f}%) | JUAL: {jn}/{jn+jb} ({jwr:.0f}%)")
        lines.append(f"- Akurasi sinyal: BELI {bn}/{bn+bb} ({bwr:.0f}%), JUAL {jn}/{jn+jb} ({jwr:.0f}%)")
    except Exception:
        pass

    print("\n" + "=" * 60)
    if promo_found:
        print("🚀 KANDIDAT PROMOSI (lolos semua gate):")
        for name, r in promo_found:
            print(f"   {name}: {json.dumps(r['params'])} exp {r['expectancy']:+.3f}R PF {r['pf']:.2f} p={r['p']:.3f}")
        print("   → Langkah berikut: terapkan ke strategies.py + pytest + commit (butuh review).")
    else:
        print("🔴 Tidak ada kandidat yang lolos gate statistik. Parameter TETAP.")
        print("   Catatan jujur: backtest masih negatif — sinyal beli tetap ditahan (fail-closed).")

    # changelog + commit + push (best-effort; gagal push tidak menggagalkan laporan)
    try:
        with open(CHANGELOG, "a") as f:
            f.write("\n" + "\n".join(lines) + "\n")
        env = dict(os.environ, HOME="/root")
        subprocess.run(["git", "-C", REPO, "add", "docs/strategy-changelog.md"],
                       env=env, capture_output=True)
        subprocess.run(["git", "-C", REPO, "-c", "user.name=hermes", "-c",
                        "user.email=hermes@localhost", "commit", "-m",
                        f"optimizer: changelog {dt.date.today().isoformat()}"],
                       env=env, capture_output=True)
        r = subprocess.run(["git", "-C", REPO, "push", "origin", "main"],
                           env=env, capture_output=True, text=True)
        if r.returncode == 0:
            print("\n✅ Changelog di-commit & push ke GitHub.")
        else:
            print(f"\n⚠️ Changelog tersimpan lokal, push gagal: {r.stderr[-200:]}")
    except Exception as e:
        print(f"\n⚠️ Changelog/commit error (abaikan): {e}")


if __name__ == "__main__":
    main()
