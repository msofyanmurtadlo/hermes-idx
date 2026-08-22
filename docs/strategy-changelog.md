
# Strategy Changelog — 2026-08-23

Universe full IDX (303 saham likuid) | data 5 tahun. Level SL/TP TETAP (SL -2%, R:R 1:2 — aturan user).

## breakout
- Champion: {} → exp -0.735R PF 0.40 (trades 1470, p=1.000)
- Best sweep: {"lookback": 15, "vol_mult": 2.0, "adx_min": 25} → exp -0.656R PF 0.45 (delta +0.079R, trades 1260, p=1.000, konsistensi 0.0)
- Verdict: ❌ belum layak

## momentum_rs
- Champion: {} → exp -0.871R PF 0.32 (trades 2059, p=1.000)
- Best sweep: {"rs_percentile": 95} → exp -0.751R PF 0.40 (delta +0.120R, trades 916, p=1.000, konsistensi 0.0)
- Verdict: ❌ belum layak

## v3score
- Champion: {} → exp -0.924R PF 0.28 (trades 10946, p=1.000)
- Best sweep: {"threshold": 6} → exp -0.901R PF 0.29 (delta +0.023R, trades 15634, p=1.000, konsistensi 0.0)
- Verdict: ❌ belum layak

## trio
- Champion: {} → exp -0.736R PF 0.39 (trades 881, p=1.000)
- Best sweep: {"rsi2_max": 8, "mfi_max": 30} → exp -0.732R PF 0.40 (delta +0.004R, trades 781, p=1.000, konsistensi 0.0)
- Verdict: ❌ belum layak

## pullback
- Skip: tanpa knob yang bisa di-sweep (semua hardcoded)

## mean_reversion
- Skip: knob atr_mult/tp_r mati sejak level FIX (SL -2%, R:R 1:2)
