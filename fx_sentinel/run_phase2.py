"""
FX Sentinel — Phase 2 러너: 경제성 P&L 백테스트 (핵심 슬라이드)
================================================================
산출: state/killer_pnl.png (P0/P1/P2 누적 P&L, 2022 음영) + 요약표 + 스프레드 스윕 + 취소 시나리오.
설계 근거: C §2·§6. 주의: 헤지비율 임계는 고정(사전등록/walk-forward는 다음 단계, D§⑤).
"""
from __future__ import annotations
import os, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd

from data_loader import load_usdkrw
from fx_ewi import compute_fx_ewi
from rates import load_rates
from backtest import run_backtest, metrics

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state"); os.makedirs(STATE, exist_ok=True)
AMOUNT = 500_000


def build_fx():
    df = load_usdkrw()
    res = compute_fx_ewi(df, N=10)
    rt = load_rates(df.index)
    fx = df[["Close", "r"]].join(res[["FX_EWI", "sigma_hat", "IZ"]]).join(rt)
    return fx.dropna(subset=["FX_EWI", "sigma_hat"])


def main():
    fx = build_fx()
    print("=" * 68)
    print(f"FX Sentinel Phase 2 — 경제성 P&L 백테스트 (수출 SME, USD {AMOUNT:,}/월)")
    print(f"기간 {fx.index.min().date()}~{fx.index.max().date()} | 예산환율=롤링1Y | 리드 3M(63bd)")
    print("=" * 68)

    # (기본) 취소 없음
    bt = run_backtest(fx, amount=AMOUNT, spread_krw=2.0, p_cancel=0.0)
    m = metrics(bt, AMOUNT)
    print(f"\n[기본 시나리오] 결제 {len(bt)}건 | P2 평균헤지비율 {bt['h'].mean():.2f} | "
          f"경보발동(h>0) {int((bt['h']>0).sum())}건")
    show = m[["누적PnL_억","표준편차_원","하방분산","Sharpe","예산달성률","최악결제_억","총헤지비용_억","평균헤지비율","분산감소_효율"]]
    print(show.round(3).to_string())

    # 핵심 비교 문장
    p0, p1, p2 = m.loc["P0"], m.loc["P1"], m.loc["P2"]
    print(f"\n[핵심] P2 vs P1: 헤지비용 {(1-p2['총헤지비용_억']/p1['총헤지비용_억'])*100:+.0f}% "
          f"({p1['총헤지비용_억']:.2f}→{p2['총헤지비용_억']:.2f}억) | "
          f"하방분산 P0 {p0['하방분산']:.4f}→P2 {p2['하방분산']:.4f} | "
          f"누적PnL P1 {p1['누적PnL_억']:.1f}→P2 {p2['누적PnL_억']:.1f}억")

    # ★ 정직성 검증: P2 우위가 '타이밍' 때문인가 '덜 헤지' 때문인가 (D§①)
    #   동일 평균비율 고정헤지(P2s)와 비교 → P2가 P2s보다 하방분산 낮아야 타이밍 가치 성립
    hs = float(bt["h"].mean())
    eff_P2s = hs * (bt["F"] - 2.0) + (1 - hs) * bt["S1"]
    pnl_P2s = np.where(bt["cancelled"] == 1, bt["unwind_P2"], AMOUNT * (eff_P2s - bt["K"]))
    sur_P2s = pd.Series(pnl_P2s, index=bt.index) / AMOUNT
    dvar_P2s = float((sur_P2s.clip(upper=0) ** 2).mean())
    verdict = "타이밍 가치 有 ✓" if p2["하방분산"] < dvar_P2s else "타이밍 가치 없음(덜헤지 효과)"
    print(f"\n[정직성검증] 동일비율({hs:.2f}) 고정헤지 vs FX-EWI 동적:")
    print(f"    고정{hs:.0%}헤지 하방분산 {dvar_P2s:.1f} | FX-EWI 동적 하방분산 {p2['하방분산']:.1f} → {verdict}")

    # (스윕) SME 스프레드 20/35/50 pips ≈ 2/3.5/5 KRW (D§⑥)
    print("\n[스프레드 민감도 스윕] P2 우위 생존성 (D§⑥)")
    print(f"{'스프레드(원)':>10} {'P1누적억':>9} {'P2누적억':>9} {'P2-P1억':>9} {'P2헤지비용억':>11}")
    for sp in (2.0, 3.5, 5.0):
        b = run_backtest(fx, amount=AMOUNT, spread_krw=sp, p_cancel=0.0)
        mm = metrics(b, AMOUNT)
        print(f"{sp:>10.1f} {mm.loc['P1','누적PnL_억']:>9.1f} {mm.loc['P2','누적PnL_억']:>9.1f} "
              f"{mm.loc['P2','누적PnL_억']-mm.loc['P1','누적PnL_억']:>9.1f} {mm.loc['P2','총헤지비용_억']:>11.2f}")

    # (취소 시나리오) 가결제 취소 15% — 선물환 반대매매 노출 (D§③)
    print("\n[취소 시나리오] 가결제 15% 취소 시 선물환 반대매매 노출 (D§③, 확실성 축 근거)")
    bc = run_backtest(fx, amount=AMOUNT, spread_krw=2.0, p_cancel=0.15, seed=7)
    mc = metrics(bc, AMOUNT)
    print(f"    무취소:  P2 최악결제 {m.loc['P2','최악결제_억']:.2f}억 | 표준편차 {m.loc['P2','표준편차_원']:.2f}원")
    print(f"    15%취소: P2 최악결제 {mc.loc['P2','최악결제_억']:.2f}억 | 표준편차 {mc.loc['P2','표준편차_원']:.2f}원")
    print(f"    → 선물환은 취소 시 반대매매 손실 노출 확대 = 가결제엔 옵션형(칼라/보험) 필요 근거")

    _plot(bt, m)
    bt.to_csv(os.path.join(STATE, "backtest_settles.csv"))
    print(f"\n[저장] state/backtest_settles.csv, state/killer_pnl.png")


def _plot(bt: pd.DataFrame, m: pd.DataFrame):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 1, figsize=(12, 6))
    colors = {"P0": "#95a5a6", "P1": "#2980b9", "P2": "#c0392b"}
    labels = {"P0": "P0 No hedge", "P1": "P1 Always hedge", "P2": "P2 FX-EWI dynamic"}
    for p in ["P0", "P1", "P2"]:
        cum = bt[f"pnl_{p}"].cumsum() / 1e8
        ax.plot(bt.index, cum, color=colors[p], lw=1.6, label=labels[p])
    ax.axvspan(pd.Timestamp("2022-06-01"), pd.Timestamp("2022-11-30"), color="red", alpha=0.07)
    ax.axhline(0, color="k", lw=0.5)
    ax.set_ylabel("Cumulative P&L vs budget (100M KRW)")
    ax.set_title("FX Sentinel — Hedging P&L: No-hedge vs Always vs FX-EWI Dynamic (cost-adj, 2022 shaded)")
    ax.legend(loc="upper left")
    plt.tight_layout()
    plt.savefig(os.path.join(STATE, "killer_pnl.png"), dpi=110)


if __name__ == "__main__":
    main()
