"""
FX Sentinel — BBP 모델검증 확장 리포트 (Tier 1)
================================================
캘리브레이션(calibrate_bbp.py) 위에, 심사가 물을 6개를 무누수로 검증한다:
 1) 만기별(1M/3M/6M) 캘리브레이션 — 만기가 길어져도 정합한가
 2) 수출/수입 대칭 — 양방향에서 균형적인가
 3) ν(자유도) 민감도 — Student-t ν=5 가정이 결과를 얼마나 바꾸나
 4) σ 추정 비교 — 엔진 σ(EWMA)가 실현변동성과 정합한가(상관·편의)
 5) ES(기대손실) 꼬리검증 — 예측 ES 평균 vs 실제 꼬리손실 평균
 6) 실패사례 — BBP 낮았는데 큰 손실 난 케이스(모델 한계 정직 공개)

산출: state/bbp_validation.json + 콘솔 요약.
"""
from __future__ import annotations
import os, json, sys
import numpy as np
import pandas as pd

from bbp import budget_breach_probability, expected_shortfall

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LOOKBACK = 252


def _load():
    d = pd.read_csv(os.path.join(STATE, "fx_ewi_timeseries.csv"),
                    index_col=0, parse_dates=True).dropna(subset=["sigma_hat"])
    return d


def _ece(p, y, k=10):
    p = np.asarray(p); y = np.asarray(y, float)
    edges = np.linspace(0, 1, k + 1); ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum() == 0: continue
        ece += (m.sum() / len(p)) * abs(p[m].mean() - y[m].mean())
    return float(ece)


def _collect(close, sig, N, nu=5.0):
    """(pred, y, pos) 수집. K=직전 1Y 평균, 만기 N, 무누수."""
    P, Y, POS = [], [], []
    n = len(close)
    for i in range(LOOKBACK, n - N):
        K = float(np.mean(close[i - LOOKBACK:i])); spot = float(close[i]); s = float(sig[i])
        settle = float(close[i + N])
        if not (np.isfinite(K) and np.isfinite(s) and s > 0): continue
        P.append(budget_breach_probability(spot, K, s, N, "export", nu)); Y.append(1 if settle < K else 0); POS.append("E")
        P.append(budget_breach_probability(spot, K, s, N, "import", nu)); Y.append(1 if settle > K else 0); POS.append("I")
    return np.array(P), np.array(Y, float), np.array(POS)


def run():
    d = _load(); close = d["Close"].values; sig = d["sigma_hat"].values
    out = {}

    # 1) 만기별
    hor = {"1M": 21, "3M": 63, "6M": 126}
    out["by_horizon"] = {}
    for name, N in hor.items():
        P, Y, _ = _collect(close, sig, N)
        out["by_horizon"][name] = {"N": N, "n": len(P), "ece": round(_ece(P, Y), 4),
                                   "brier": round(float(np.mean((P - Y) ** 2)), 4)}

    # 2) 수출/수입 대칭 (N=63)
    P, Y, POS = _collect(close, sig, 63)
    out["symmetry"] = {
        "export": {"n": int((POS == "E").sum()), "ece": round(_ece(P[POS == "E"], Y[POS == "E"]), 4),
                   "breach_rate": round(float(Y[POS == "E"].mean()), 3)},
        "import": {"n": int((POS == "I").sum()), "ece": round(_ece(P[POS == "I"], Y[POS == "I"]), 4),
                   "breach_rate": round(float(Y[POS == "I"].mean()), 3)},
    }

    # 3) ν 민감도 (N=63, 전체 ECE)
    out["nu_sensitivity"] = {}
    for nu in [3.0, 5.0, 8.0, 30.0]:
        Pn, Yn, _ = _collect(close, sig, 63, nu)
        out["nu_sensitivity"][str(int(nu))] = round(_ece(Pn, Yn), 4)

    # 4) σ 추정 비교: 엔진 σ_hat vs 실현 63일 전방 변동성
    logret = np.diff(np.log(close))
    N = 63; sh, rv = [], []
    for i in range(LOOKBACK, len(close) - N - 1):
        rvol = float(np.std(logret[i:i + N]) * np.sqrt(252))
        if np.isfinite(sig[i]) and np.isfinite(rvol) and sig[i] > 0:
            sh.append(float(sig[i])); rv.append(rvol)
    sh, rv = np.array(sh), np.array(rv)
    out["sigma_check"] = {"n": len(sh), "corr": round(float(np.corrcoef(sh, rv)[0, 1]), 3),
                          "mean_sigma_hat": round(float(sh.mean()), 4), "mean_realized": round(float(rv.mean()), 4),
                          "mean_bias": round(float((sh - rv).mean()), 4)}

    # 5) ES 꼬리검증 (N=63): 예측 ES 평균 vs 실제 꼬리손실(원/달러) 평균 — 수출 예시
    N = 63; pes, res = [], []
    for i in range(LOOKBACK, len(close) - N):
        K = float(np.mean(close[i - LOOKBACK:i])); spot = float(close[i]); s = float(sig[i]); settle = float(close[i + N])
        if not (np.isfinite(K) and s > 0): continue
        pes.append(expected_shortfall(spot, K, s, N, "export"))     # 예측 기대손실(원/달러)
        res.append(max(K - settle, 0.0))                            # 실제 불리초과(원/달러)
    pes, res = np.array(pes), np.array(res)
    out["es_check"] = {"n": len(pes), "mean_pred_es": round(float(pes.mean()), 2),
                       "mean_realized_loss": round(float(res.mean()), 2),
                       "ratio": round(float(pes.mean() / max(res.mean(), 1e-9)), 3)}

    # 6) 실패사례: BBP<0.2(예측 안전)인데 실제 이탈 + 큰 손실(초과>예측ES의 2배)
    fails = 0; total_safe = 0; examples = []
    N = 63
    for i in range(LOOKBACK, len(close) - N):
        K = float(np.mean(close[i - LOOKBACK:i])); spot = float(close[i]); s = float(sig[i]); settle = float(close[i + N])
        if not (np.isfinite(K) and s > 0): continue
        p = budget_breach_probability(spot, K, s, N, "export", 5.0)
        if p < 0.2:
            total_safe += 1
            es = expected_shortfall(spot, K, s, N, "export")
            excess = max(K - settle, 0.0)
            if excess > 2 * es and excess > 0:
                fails += 1
                if len(examples) < 3:
                    examples.append({"date": str(d.index[i].date()), "bbp": round(p, 3),
                                     "excess_krw_per_usd": round(excess, 1)})
    out["failure_analysis"] = {"safe_n": total_safe, "fail_n": fails,
                               "fail_rate": round(fails / max(total_safe, 1), 4), "examples": examples}

    with open(os.path.join(STATE, "bbp_validation.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    o = run()
    print("① 만기별 캘리브레이션(ECE 낮을수록 정합)")
    for k, v in o["by_horizon"].items():
        print(f"   {k}(N={v['N']}) n={v['n']:,} ECE {v['ece']} Brier {v['brier']}")
    s = o["symmetry"]
    print(f"② 수출/수입 대칭: 수출 ECE {s['export']['ece']}(이탈률 {s['export']['breach_rate']}) · 수입 ECE {s['import']['ece']}(이탈률 {s['import']['breach_rate']})")
    print(f"③ ν 민감도 ECE: " + " · ".join(f"ν{k}={v}" for k, v in o["nu_sensitivity"].items()))
    sc = o["sigma_check"]
    print(f"④ σ 정합: corr {sc['corr']} · 엔진σ평균 {sc['mean_sigma_hat']} vs 실현 {sc['mean_realized']} (편의 {sc['mean_bias']:+})")
    ec = o["es_check"]
    print(f"⑤ ES 꼬리: 예측평균 {ec['mean_pred_es']}원/$ vs 실제 {ec['mean_realized_loss']}원/$ (비율 {ec['ratio']})")
    fa = o["failure_analysis"]
    print(f"⑥ 실패사례: BBP<20% {fa['safe_n']:,}건 중 큰손실 {fa['fail_n']}건({fa['fail_rate']:.1%}) — 예: {fa['examples']}")
