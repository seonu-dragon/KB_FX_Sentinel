"""
FX Sentinel — Phase 1: FX-EWI 합성 (2-헤드)
===========================================
설계 근거: FX_Sentinel_A_FX-EWI_수식설계.md §4

 2-헤드 구조(D§②):
   ① 확률 헤드   : score = Σ w_i·x_i (V·J·C·M) → FX-EWI 0~100 + 경보 등급
   ② σ̂ 회귀 헤드 : 향후 N일 변동성 예측 σ̂_{t+N} (σ_fwd·GARCH 직접비교용)

 규율:
   - S(뉴스심리)는 어떤 숫자 지수에도 넣지 않는다(D§④·§3.5). Analyst 정성 XAI 전용.
   - MVP 가중치는 규칙기반 폴백 w=(V,J,C,M)=(0.33,0.22,0.28,0.17), M 미연결 시 재정규화.
   - 지도학습 캘리브레이션(로지스틱)은 Phase 2에서 라벨로 교체.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from components import (component_V, component_J, component_C, component_M,
                        intervention_flag, ewma_vol, LOGISTIC, _roll_z)

W_DEFAULT = {"V": 0.33, "J": 0.22, "C": 0.28, "M": 0.17}  # A §4.1 사전가중
GRADES = [(0, 40, "정상"), (40, 60, "주의"), (60, 80, "경계"), (80, 101, "심각")]


def grade_of(ewi: float) -> str:
    for lo, hi, name in GRADES:
        if lo <= ewi < hi:
            return name
    return "심각"


def compute_fx_ewi(df: pd.DataFrame, weights: dict | None = None,
                   N: int = 10, calendar: dict | None = None) -> pd.DataFrame:
    """FX-EWI 전체 파이프라인. 성분 → 합성 → 0~100·등급 + σ̂ 헤드."""
    r = df["r"]
    w = dict(weights or W_DEFAULT)

    # --- 성분 (V·J·C·M) ---
    V = component_V(df)
    J = component_J(r)
    C, garch_params, C_raw = component_C(r, V)
    M = component_M(df.index, calendar)
    IZ = intervention_flag(df)

    comp = pd.concat([V, J, C, M], axis=1)

    # M 미연결(전부 0)이면 가중 재정규화
    if M.abs().sum() == 0:
        w.pop("M", None)
    wsum = sum(w.values())
    w = {k: v / wsum for k, v in w.items()}

    # --- ① 확률 헤드: score → FX-EWI 0~100 ---
    score = sum(w[k] * comp[k] for k in w)
    ewi = 100.0 * LOGISTIC(_roll_z(score, 252))
    grade = ewi.apply(lambda x: grade_of(x) if pd.notna(x) else np.nan)

    # --- ② σ̂ 회귀 헤드 (MVP: 연율화 EWMA N일 예측; Phase2에서 학습·GARCH비교) ---
    sigma_d = ewma_vol(r)                       # 일 변동성
    sigma_fwd = (sigma_d * np.sqrt(252)).rename("sigma_hat")  # 연율화 σ̂ (σ_fwd 공급)

    out = pd.concat([comp, IZ["IZ"], score.rename("score"),
                     ewi.rename("FX_EWI"), grade.rename("grade"), sigma_fwd], axis=1)
    out.attrs["weights"] = w
    out.attrs["garch"] = garch_params
    out.attrs["corr_CV"] = float(comp[["C", "V"]].corr().iloc[0, 1])
    return out


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    from data_loader import load_usdkrw
    df = load_usdkrw()
    res = compute_fx_ewi(df)
    print("가중치:", {k: round(v, 3) for k, v in res.attrs["weights"].items()})
    print("GARCH persistence:", round(res.attrs["garch"]["persistence"], 3))
    print("corr(C,V):", round(res.attrs["corr_CV"], 3))
    print("\n[등급 분포]\n", res["grade"].value_counts().reindex(["정상","주의","경계","심각"]))
    print("\n[FX_EWI 통계]\n", res["FX_EWI"].describe().round(1))
