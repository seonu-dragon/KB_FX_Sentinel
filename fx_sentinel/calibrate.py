"""
FX Sentinel — 예측력 구제 실험 (A): 지도학습 walk-forward 캘리브레이션
=====================================================================
질문: 규칙가중 대신 '학습'하고 라벨을 재설계하면, FX-EWI 성분(V·J·C)이
      레벨/GARCH 베이스라인을 무누수 OOS에서 이기는가? (D§6.1 증분가치의 진짜 시험)

설계:
 - Walk-forward: 확장창, step마다 재학습 → 다음 블록 OOS 예측 풀링 → AUC.
 - 라벨 3종: level(향후RV 상위분위) / jump(향후 점프발생) / exp(변동성 팽창).
 - 피처셋: LEVEL(ewma) / GARCH(cond vol) / VJC(성분) / VJC+LVL.
 - 판정: 어떤 라벨에서든 VJC가 LEVEL·GARCH를 OOS서 유의하게 이기면 구제 성공.
주의: C의 GARCH는 전표본 적합(C에 유리한 누수). 그런데도 VJC가 못 이기면 결론 강건.
"""
from __future__ import annotations
import sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

from data_loader import load_usdkrw
from components import component_V, component_J, component_C, ewma_vol, garch_cond_var
from labels import forward_rv

N = 10


def build_features_labels():
    df = load_usdkrw(); r = df["r"]
    V = component_V(df); J = component_J(r)
    C, _, _ = component_C(r, V)
    lvl = (ewma_vol(r) * np.sqrt(252)).rename("lvl")           # 레벨 베이스라인
    gvar, _ = garch_cond_var(r)
    gv = (np.sqrt(gvar) * np.sqrt(252)).rename("garch")         # GARCH 조건부변동성
    feats = pd.concat([V, J, C, lvl, gv], axis=1)

    rv = forward_rv(r, N)
    # 라벨 A: 향후RV 상위분위 (레벨 라벨) — 임계 무누수
    q = rv.shift(N).rolling(252, min_periods=126).quantile(0.8)
    y_level = (rv > q).astype(float); y_level[q.isna()] = np.nan
    # 라벨 JUMP: 향후 N일 내 점프(|u|>3) 발생
    u = (r / ewma_vol(r)).abs()
    jump = (u > 3).astype(float)
    y_jump = jump.shift(-1).rolling(N, min_periods=N).max().shift(-(N - 1))
    # 라벨 EXP: 향후RV > 1.25 × 과거20일RV (팽창)
    rv_past = (r ** 2).rolling(20).sum().pow(0.5)
    y_exp = (rv > rv_past * 1.25).astype(float)
    labels = {"level": y_level, "jump": y_jump, "exp": y_exp}
    return feats, labels


def wf_auc(X: pd.DataFrame, y: pd.Series, min_train=600, step=21) -> tuple[float, int, float]:
    """확장창 walk-forward → 풀링 OOS AUC. 반환 (auc, n, 양성률)."""
    idx = X.index
    preds = pd.Series(index=idx, dtype=float)
    for start in range(min_train, len(idx), step):
        Xtr, ytr = X.iloc[:start], y.iloc[:start]
        Xte = X.iloc[start:start + step]
        m = Xtr.notna().all(axis=1) & ytr.notna()
        if m.sum() < 100 or ytr[m].nunique() < 2:
            continue
        sc = StandardScaler().fit(Xtr[m])
        lr = LogisticRegression(max_iter=300, C=1.0)
        lr.fit(sc.transform(Xtr[m]), ytr[m].astype(int))
        mte = Xte.notna().all(axis=1)
        if mte.sum() == 0:
            continue
        preds.loc[Xte.index[mte]] = lr.predict_proba(sc.transform(Xte[mte]))[:, 1]
    d = pd.concat([preds.rename("p"), y.rename("y")], axis=1).dropna()
    if len(d) < 50 or d["y"].nunique() < 2:
        return np.nan, len(d), np.nan
    return roc_auc_score(d["y"], d["p"]), len(d), float(d["y"].mean())


FEATURE_SETS = {
    "LEVEL(ewma)": ["lvl"],
    "GARCH": ["garch"],
    "VJC": ["V", "J", "C"],
    "VJC+LVL": ["V", "J", "C", "lvl"],
}


def main():
    feats, labels = build_features_labels()
    print("=" * 72)
    print("FX-EWI 예측력 구제 실험 — 지도학습 walk-forward OOS AUC")
    print("=" * 72)
    print(f"{'라벨':<8}{'양성률':>7} | " + "".join(f"{k:>14}" for k in FEATURE_SETS))
    print("-" * 72)
    results = {}
    for lname, y in labels.items():
        row = {}
        pos = np.nan
        for fs, cols in FEATURE_SETS.items():
            auc, n, pos = wf_auc(feats[cols], y)
            row[fs] = auc
        results[lname] = row
        print(f"{lname:<8}{pos:>7.2f} | " +
              "".join(f"{row[k]:>14.3f}" for k in FEATURE_SETS))
    print("-" * 72)
    # 판정: VJC가 LEVEL·GARCH를 이긴 라벨
    print("\n[판정] VJC가 레벨·GARCH를 OOS서 이긴 라벨:")
    rescued = False
    for lname, row in results.items():
        base = max(row["LEVEL(ewma)"], row["GARCH"])
        vjc = row["VJC"]
        margin = vjc - base
        win = np.isfinite(vjc) and np.isfinite(base) and vjc > base and vjc > 0.5
        flag = "✓ 구제신호" if win and margin > 0.02 else ("근소" if win else "✗")
        if win and margin > 0.02:
            rescued = True
        print(f"    {lname:<8}: VJC {vjc:.3f} vs max(base) {base:.3f}  (Δ{margin:+.3f})  {flag}")
    print(f"\n[결론] {'구제 신호 있음 → 타이밍 재검증 가치 有' if rescued else '구제 실패 → (B) 주장전환 확정 권장'}")


if __name__ == "__main__":
    main()
