"""
FX Sentinel — BBP 캘리브레이션 백테스트 (신뢰성 검증)
=====================================================
질문: "BBP 60%로 나온 거래가 실제로 60% 근처로 이탈했는가?"
= 예측확률(BBP)과 실제 이탈빈도의 정합(calibration)을 무누수로 검증한다.

방법(무누수):
 - 각 영업일 t: 예산환율 K = 직전 252영업일 종가 평균(사업계획 벤치마크), 만기 N=63영업일.
 - σ_t = 그 시점 sigma_hat(엔진 동일 σ). BBP_t = budget_breach_probability(spot_t, K, σ_t, N, pos).
 - 실제 이탈: 만기 종가 S_{t+N}. 수출 불리=S_{t+N}<K, 수입 불리=S_{t+N}>K (모델 정의=만기 분포).
 - 수출·수입을 함께 풀링(확률 [0,1] 전구간 커버) → 십분위 신뢰도곡선 + Brier + ECE.

지표:
 - Brier = mean((p - y)^2)  (낮을수록 좋음; 무정보 0.25)
 - ECE   = Σ (bin비중)·|평균예측 - 실제빈도|  (0에 가까울수록 정합)
"""
from __future__ import annotations
import os, json, sys
import numpy as np
import pandas as pd

from bbp import budget_breach_probability

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
N_HORIZON = 63
LOOKBACK = 252
NU = 5.0


def run():
    d = pd.read_csv(os.path.join(STATE, "fx_ewi_timeseries.csv"),
                    index_col=0, parse_dates=True).dropna(subset=["sigma_hat"])
    close = d["Close"].values
    sig = d["sigma_hat"].values
    n = len(close)
    preds, ys = [], []
    for i in range(LOOKBACK, n - N_HORIZON):
        K = float(np.mean(close[i - LOOKBACK:i]))     # 예산환율 = 직전 1Y 평균
        spot = float(close[i]); s = float(sig[i])
        settle = float(close[i + N_HORIZON])           # 만기 종가(무누수)
        if not (np.isfinite(K) and np.isfinite(s) and s > 0):
            continue
        # 수출(불리 S<K)
        p_ex = budget_breach_probability(spot, K, s, N_HORIZON, "export", NU)
        preds.append(p_ex); ys.append(1 if settle < K else 0)
        # 수입(불리 S>K)
        p_im = budget_breach_probability(spot, K, s, N_HORIZON, "import", NU)
        preds.append(p_im); ys.append(1 if settle > K else 0)

    p = np.array(preds); y = np.array(ys, dtype=float)
    brier = float(np.mean((p - y) ** 2))
    base = float(y.mean())

    # 십분위 신뢰도곡선
    edges = np.linspace(0, 1, 11)
    bins, ece = [], 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        c = int(m.sum())
        if c == 0:
            bins.append({"lo": round(lo, 1), "hi": round(hi, 1), "n": 0,
                         "pred": None, "real": None}); continue
        pred = float(p[m].mean()); real = float(y[m].mean())
        ece += (c / len(p)) * abs(pred - real)
        bins.append({"lo": round(lo, 1), "hi": round(hi, 1), "n": c,
                     "pred": round(pred, 3), "real": round(real, 3)})

    out = {"n_obs": len(p), "horizon_bd": N_HORIZON, "lookback": LOOKBACK, "nu": NU,
           "brier": round(brier, 4), "ece": round(ece, 4), "base_rate": round(base, 4),
           "bins": bins}
    with open(os.path.join(STATE, "bbp_calibration.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    o = run()
    print(f"관측 {o['n_obs']:,}건 · 만기 {o['horizon_bd']}영업일 · 예산=직전{o['lookback']}일 평균 · ν={o['nu']}")
    print(f"Brier {o['brier']} (무정보 0.25↓ 좋음) · ECE {o['ece']} (0에 가까울수록 정합) · 평균이탈률 {o['base_rate']}")
    print(f"\n{'예측구간':>10} {'건수':>7} {'평균예측':>8} {'실제빈도':>8}  정합")
    print("─" * 52)
    for b in o["bins"]:
        if b["n"] == 0:
            print(f" {b['lo']:.1f}–{b['hi']:.1f} {'':>8} {0:>7} {'-':>8} {'-':>8}"); continue
        gap = abs(b["pred"] - b["real"])
        mark = "◎" if gap < 0.05 else ("○" if gap < 0.10 else "△")
        print(f" {b['lo']:.1f}–{b['hi']:.1f} {b['n']:>10,} {b['pred']:>8.2f} {b['real']:>8.2f}   {mark}")
    print("─" * 52)
    print("◎<0.05 ○<0.10 △≥0.10  (예측=실제에 가까울수록 캘리브레이션 양호)")
