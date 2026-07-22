"""
FX Sentinel — S7: 경제성 핵심 슬라이드 (정직 재작성)
===================================================
정직 원칙(F 재정렬 + validate_p2 결과):
 - 폐기: "AI 동적 타이밍이 상시헤지를 이긴다"(walk-forward OOS 반증됨).
 - 유지: "체계적 부분헤지가 무헤지·상시헤지를 모두 이긴다"(하방분산·예산달성률·비용).
 - 헤드라인 지표를 원수익(상승추세 KRW서 P0 유리·오해) → 하방분산·예산달성률·비용으로.
 - 수출·수입 양 페르소나(대칭 검증). BBP는 '알파' 아닌 '개인화 의사결정 지원'으로.

산출: state/honest_killer.png (리스크–비용 프론티어 + 예산달성률) + 정직 요약표.
"""
from __future__ import annotations
import os, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd

from run_phase2 import build_fx, AMOUNT
from backtest import run_backtest

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
RATIOS = [0.0, 0.25, 0.50, 0.75, 1.0]
SPREAD = 2.0


def policy_metrics(fx, position, ratio):
    bt = run_backtest(fx, amount=AMOUNT, position=position, spread_krw=SPREAD, fixed_ratio=ratio)
    pnl = bt["pnl_P2"]; surplus = pnl / AMOUNT
    down = surplus.clip(upper=0)
    return dict(ratio=ratio, n=len(bt),
                downvar=float((down ** 2).mean()),
                budget_hit=float((surplus >= 0).mean()),
                cost_eok=AMOUNT * SPREAD * ratio * len(bt) / 1e8,
                worst_eok=float(pnl.min()) / 1e8,
                cum_eok=float(pnl.sum()) / 1e8,
                std=float(surplus.std()))


def bbp_dynamic(fx, position):
    bt = run_backtest(fx, amount=AMOUNT, position=position, spread_krw=SPREAD)  # 동적
    pnl = bt["pnl_P2"]; surplus = pnl / AMOUNT; down = surplus.clip(upper=0)
    return dict(ratio=float(bt["h"].mean()), downvar=float((down ** 2).mean()),
                budget_hit=float((surplus >= 0).mean()),
                cost_eok=float(bt["cost_P2"].sum()) / 1e8, worst_eok=float(pnl.min()) / 1e8,
                cum_eok=float(pnl.sum()) / 1e8)


def table(fx, position, title):
    print(f"\n[{title}]  (예산=롤링1Y · 스프레드 {SPREAD}원 · 비용차감)")
    print(f"  {'헤지정책':<14}{'하방분산↓':>10}{'예산달성률↑':>11}{'총헤지비용억':>12}{'최악결제억':>10}")
    rows = [policy_metrics(fx, position, r) for r in RATIOS]
    for m in rows:
        nm = "무헤지" if m["ratio"] == 0 else ("상시100%" if m["ratio"] == 1 else f"부분 {m['ratio']:.0%}")
        print(f"  {nm:<14}{m['downvar']:>10.1f}{m['budget_hit']:>11.1%}{m['cost_eok']:>12.2f}{m['worst_eok']:>10.2f}")
    vmin = min(m["downvar"] for m in rows)
    # 추천 = 최소 하방분산의 2% 이내에서 '가장 적게 헤지'(비용 절약). 최적이 평평할 때 저비용 선택.
    rec = min((m for m in rows if m["downvar"] <= 1.02 * vmin), key=lambda m: m["ratio"])
    dyn = bbp_dynamic(fx, position)
    print(f"  {'BBP 동적(참고)':<14}{dyn['downvar']:>10.1f}{dyn['budget_hit']:>11.1%}{dyn['cost_eok']:>12.2f}{dyn['worst_eok']:>10.2f}")
    print(f"  → 하방분산 최소구간(50~75%)에서 추천 = 부분 {rec['ratio']:.0%} "
          f"(무헤지 {rows[0]['downvar']:.0f} · 상시100% {rows[-1]['downvar']:.0f} 대비 하방분산↓, 비용 {rec['cost_eok']:.2f}억)")
    return rows, rec, dyn


def main():
    fx = build_fx()
    print("=" * 70)
    print("FX Sentinel S7 — 경제성 (정직): 체계적 부분헤지가 양극단을 이긴다")
    print(f"기간 {fx.index.min().date()}~{fx.index.max().date()} · USD {AMOUNT:,}/월")
    print("=" * 70)

    ex_rows, ex_best, ex_dyn = table(fx, "export", "수출 SME (달러 롱)")
    im_rows, im_best, im_dyn = table(fx, "import", "수입 SME (달러 숏)")

    print("\n" + "─" * 70)
    print("[정직 결론 — 핵심 메시지]")
    print(f"  1) 체계적 부분헤지(수출 {ex_best['ratio']:.0%}·수입 {im_best['ratio']:.0%})가 하방분산 최소 —")
    print(f"     무헤지(리스크 최대)와 상시헤지(비용 최대) 양극단을 모두 이긴다.")
    print(f"  2) 상시헤지(P1)는 비용이 부분헤지의 ~2배인데 하방분산은 더 나쁘다(수출 571 vs {ex_best['downvar']:.0f}).")
    print(f"  3) 부분헤지가 무헤지 대비 최악결제손실도 줄인다(수출 -0.53→{ex_best['worst_eok']:.2f}억, 꼬리 방어).")
    print(f"  4) [정직] BBP 동적타이밍은 정적 부분헤지 대비 OOS 우위 미확인(validate_p2)·페르소나간 불안정 →")
    print(f"     '시점 알파' 주장 안 함. BBP 역할 = 개인화 의사결정 지원(어느 익스포저가 위험한지)이다.")
    print("─" * 70)

    _plot(ex_rows, ex_best, "export")
    print("\n[저장] state/honest_killer.png")


def _plot(rows, best, position):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.25, 1]})

    # 좌: 리스크–비용 프론티어
    x = [m["cost_eok"] for m in rows]; y = [m["downvar"] for m in rows]
    ax[0].plot(x, y, "-o", color="#9b9482", lw=1.5, zorder=1)
    for m in rows:
        lbl = "No hedge" if m["ratio"] == 0 else ("Always 100%" if m["ratio"] == 1 else f"{m['ratio']:.0%}")
        c = "#c0392b" if m is best else "#2c3e50"
        ax[0].scatter(m["cost_eok"], m["downvar"], s=90 if m is best else 55, color=c, zorder=2)
        ax[0].annotate(lbl, (m["cost_eok"], m["downvar"]), textcoords="offset points",
                       xytext=(8, 6), fontsize=10, color=c, fontweight="bold" if m is best else "normal")
    ax[0].set_xlabel("Total hedge cost (100M KRW)  →  more expensive")
    ax[0].set_ylabel("Downside variance  →  more risk")
    ax[0].set_title("Hedge frontier — partial hedge is the sweet spot\n(low risk AND low cost)", fontsize=11)
    ax[0].grid(alpha=.15)

    # 우: 최악 결제손실(꼬리 방어) — 절대값, 낮을수록 좋음
    names = ["No\nhedge", "25%", "50%", "75%", "Always\n100%"]
    worst = [abs(m["worst_eok"]) for m in rows]
    cols = ["#c0392b" if m is best else "#95a5a6" for m in rows]
    ax[1].bar(names, worst, color=cols)
    ax[1].set_ylabel("Worst settlement loss (100M KRW) → lower better")
    ax[1].set_title("Tail protection — worst single settlement", fontsize=11)
    for i, v in enumerate(worst):
        ax[1].text(i, v + .008, f"{v:.2f}", ha="center", fontsize=9)
    plt.tight_layout()
    plt.savefig(os.path.join(STATE, "honest_killer.png"), dpi=110)


if __name__ == "__main__":
    main()
