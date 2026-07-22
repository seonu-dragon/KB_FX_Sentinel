"""
FX Sentinel — Phase 1: 예측 라벨 정의
=====================================
설계 근거: FX_Sentinel_A_FX-EWI_수식설계.md §4.1

라벨(예측 대상): 시점 t에서 향후 N영업일 위험 고조 여부.
  Y_t = 1{ RV_[t+1, t+N] > Q_p(RV_forward) }
  - RV_forward = 향후 N일 실현변동성 (t+1..t+N 로그수익률)
  - Q_p = 트레일링 롤링 분위(과거만 사용, 임계 무누수)
  - 사전등록 주 스펙(D§⑤): N=10, RV분위 라벨, p=0.8
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def forward_rv(r: pd.Series, N: int = 10) -> pd.Series:
    """향후 N영업일 실현변동성 (t 시점 라벨용, t+1..t+N)."""
    r2 = r ** 2
    # t+1..t+N 합 → t에 정렬
    fwd = r2.shift(-1).rolling(N, min_periods=N).sum().shift(-(N - 1))
    return np.sqrt(fwd).rename("RV_fwd")


def make_label(r: pd.Series, N: int = 10, p: float = 0.8, qwin: int = 252) -> pd.DataFrame:
    """Y_t = 1{RV_fwd > 트레일링 p분위}. 임계는 과거창에서만(무누수)."""
    rv = forward_rv(r, N)
    # 트레일링 분위: RV_fwd 자체는 미래를 보지만, '임계값'은 과거 실현 RV_fwd 분포에서만.
    # 라벨 정의이므로 RV_fwd는 미래정보 허용(정답), 임계 Q_p만 무누수.
    q = rv.shift(N).rolling(qwin, min_periods=qwin // 2).quantile(p)  # N만큼 밀어 겹침 회피
    Y = (rv > q).astype(float)
    Y[q.isna()] = np.nan
    return pd.DataFrame({"RV_fwd": rv, "Q_p": q, "Y": Y})


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    from data_loader import load_usdkrw
    r = load_usdkrw()["r"]
    for N in (5, 10, 20):
        lab = make_label(r, N=N)
        yr = lab["Y"].dropna()
        print(f"N={N:2d} | 라벨 수={len(yr)} | 양성률={yr.mean():.3f} (목표≈0.20)")
