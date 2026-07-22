"""
FX Sentinel — Phase 1 러너: FX-EWI 산출 + 1차 예측력 확인
=========================================================
핵심 질문(Phase 1): "높은 FX-EWI가 미래 실현변동성 급등을 선행하는가?"
  → 등급별 향후 RV, ROC-AUC, 이벤트 스터디(경계+ vs 평상), Precision/Recall.

주의: 이 결과는 규칙기반 가중·전표본 GARCH의 1차 확인(in-sample 요소 포함).
      무누수 OOS·GARCH 증분가치·경제성은 Phase 2(walk-forward)에서.
출력: state/fx_ewi_timeseries.csv, state/fx_ewi_phase1.png
"""
from __future__ import annotations
import os, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd

from data_loader import load_usdkrw
from fx_ewi import compute_fx_ewi
from labels import make_label

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state"); os.makedirs(STATE, exist_ok=True)
N = 10


def auc_manual(score: np.ndarray, y: np.ndarray) -> float:
    """Mann-Whitney U 기반 ROC-AUC (의존성 없이)."""
    order = np.argsort(score)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(score) + 1)
    # 동점 평균순위 보정
    s = pd.Series(score); ranks = s.rank(method="average").values
    n_pos = y.sum(); n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0: return np.nan
    return (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def main():
    df = load_usdkrw()
    res = compute_fx_ewi(df, N=N)
    lab = make_label(df["r"], N=N)
    D = res.join(lab).dropna(subset=["FX_EWI", "Y", "RV_fwd"])
    D["RV_fwd_ann"] = D["RV_fwd"] / np.sqrt(N) * np.sqrt(252) * 100  # 연율화 %

    print("=" * 64)
    print(f"FX Sentinel Phase 1 — USD/KRW  {df.index.min().date()}~{df.index.max().date()}")
    print(f"가중치 {({k:round(v,3) for k,v in res.attrs['weights'].items()})} | "
          f"GARCH persist={res.attrs['garch']['persistence']:.3f} | corr(C,V)={res.attrs['corr_CV']:.3f}")
    print("=" * 64)

    # (1) 등급별 향후 RV — 단조 증가해야 예측력 있음
    print("\n[1] 경보 등급별 향후 10일 실현변동성(연율%) — 단조↑면 예측력 有")
    g = D.groupby("grade")["RV_fwd_ann"].agg(["mean", "median", "count"]).reindex(["정상","주의","경계","심각"])
    print(g.round(2).to_string())

    # (2) ROC-AUC (FX_EWI vs Y) + 진단: 레벨 베이스라인 & 팽창 라벨
    auc = auc_manual(D["FX_EWI"].values, D["Y"].values.astype(int))
    # 레벨 베이스라인: 현재 변동성 레벨(σ̂)이 '미래 RV 레벨' 라벨을 얼마나 맞히나
    auc_level = auc_manual(D["sigma_hat"].values, D["Y"].values.astype(int))
    # 대안 라벨 Y2 = 변동성 '팽창'(레짐변화): 향후RV > 과거20일RV × 1.25
    rv_past = (df["r"] ** 2).rolling(20).sum().pow(0.5).reindex(D.index)
    Y2 = (D["RV_fwd"] > rv_past * 1.25).astype(int)
    auc_ewi_exp = auc_manual(D["FX_EWI"].values, Y2.values)
    auc_lvl_exp = auc_manual(D["sigma_hat"].values, Y2.values)
    print(f"\n[2] ROC-AUC  (0.5=무작위)")
    print(f"    라벨A '향후RV 레벨 상위분위':  FX_EWI={auc:.3f} | 레벨(σ̂) 베이스라인={auc_level:.3f}")
    print(f"    라벨B '변동성 팽창(레짐변화)':  FX_EWI={auc_ewi_exp:.3f} | 레벨(σ̂) 베이스라인={auc_lvl_exp:.3f}  (양성률 {Y2.mean():.2f})")
    print(f"    → FX-EWI는 레벨을 직교제거했으므로 레벨라벨(A)엔 약하고, 팽창라벨(B)에서 레벨을 이겨야 정체성 성립")

    # (3) 이벤트 스터디: 경계+ 발동 vs 평상
    hi = D[D["FX_EWI"] >= 60]["RV_fwd_ann"]
    base = D[D["FX_EWI"] < 60]["RV_fwd_ann"]
    print(f"\n[3] 이벤트 스터디 — 경보(경계+, EWI≥60) 후 vs 평상")
    print(f"    경계+ 향후RV {hi.mean():.2f}% (n={len(hi)}) | 평상 {base.mean():.2f}% (n={len(base)})"
          f" | 비율 {hi.mean()/base.mean():.2f}x")

    # (4) Precision/Recall @ 경계 임계
    pred = (D["FX_EWI"] >= 60).astype(int); y = D["Y"].astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else np.nan
    rec = tp / (tp + fn) if tp + fn else np.nan
    print(f"\n[4] 경계+ 임계 성능: Precision={prec:.3f} Recall={rec:.3f} "
          f"(base rate={y.mean():.3f})  lift={prec/y.mean():.2f}x")

    # (5) 저장
    D_save = df[["Open","High","Low","Close"]].join(
        res[["V","J","C","IZ","score","FX_EWI","grade","sigma_hat"]]).join(lab[["RV_fwd","Y"]])
    out_csv = os.path.join(STATE, "fx_ewi_timeseries.csv")
    D_save.to_csv(out_csv)
    print(f"\n[저장] {out_csv}  ({len(D_save)}행)")

    _plot(D, res, df)
    return D


def _plot(D: pd.DataFrame, res: pd.DataFrame, df: pd.DataFrame):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(13, 8), gridspec_kw={"height_ratios": [2, 1]})

    # 상단: FX-EWI + 환율
    ax0 = ax[0]; ax0b = ax0.twinx()
    ax0b.plot(df.index, df["Close"], color="#888", lw=0.8, alpha=0.6, label="USD/KRW")
    ax0.plot(res.index, res["FX_EWI"], color="#c0392b", lw=0.9, label="FX-EWI")
    for lo, c in [(40,"#f9e79f"),(60,"#f5b041"),(80,"#e74c3c")]:
        ax0.axhline(lo, color=c, lw=0.6, ls="--", alpha=0.7)
    ax0.axvspan(pd.Timestamp("2022-06-01"), pd.Timestamp("2022-11-30"), color="red", alpha=0.07)
    ax0.set_ylabel("FX-EWI (0-100)"); ax0b.set_ylabel("USD/KRW")
    ax0.set_title("FX Sentinel — FX-EWI vs USD/KRW (2022 King-Dollar shaded)")
    ax0.set_ylim(0, 100); ax0.legend(loc="upper left"); ax0b.legend(loc="upper right")

    # 하단: 등급별 향후 RV (예측력 시각화)
    g = D.groupby("grade")["RV_fwd_ann"].mean().reindex(["정상","주의","경계","심각"])
    ax[1].bar(["Normal","Watch","Alert","Severe"], g.values,
              color=["#2ecc71","#f1c40f","#e67e22","#c0392b"])
    ax[1].set_ylabel("Fwd 10d RV (ann %)")
    ax[1].set_title("Forward realized volatility by alarm grade (monotonic up = predictive)")
    plt.tight_layout()
    png = os.path.join(STATE, "fx_ewi_phase1.png")
    plt.savefig(png, dpi=110); print(f"[저장] {png}")


if __name__ == "__main__":
    main()
