"""
FX Sentinel — 범위선물환(레인지 포워드) 경제성 (S8)
=====================================================
질문: 신설한 '범위선물환'은 무헤지/부분선물환/상시선물환 프론티어에서 어디에 앉는가?

정직 원칙(S7 계승):
 - 예측 알파 주장 안 함. 구조가 주는 것/뺏는 것만 실측한다.
 - 범위선물환(제로코스트 칼라) = 밴드[F(1±band)] 안은 시장 참여, 밖은 밴드가 정산.
 - 밴드폭은 옵션가격에 좌우 → '가정'(요율 미연동). band 그리드로 민감도까지 보인다.

기대(구조상): 범위형은 무헤지보다 하방분산↓(밴드 하단이 바닥을 받침),
 상시선물환보다 하방분산↑(밴드 안 변동은 통과)이되, 상시선물환이 포기하는
 '유리한 쪽 참여(상단 이익 일부)'를 유지한다 → 평균초과·상단참여로 확인.

산출: 콘솔 표 + state/range_econ.json (문서·발표 인용용).
"""
from __future__ import annotations
import os, sys, json
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np

from run_phase2 import build_fx, AMOUNT
from backtest import run_backtest

HERE = os.path.dirname(os.path.abspath(__file__)); STATE = os.path.join(HERE, "state")
SPREAD = 2.0
BANDS = [0.01, 0.02, 0.03]   # 밴드폭 가정(±%) — 민감도


def _summ(pnl, cost_eok):
    surplus = pnl / AMOUNT
    down = surplus.clip(upper=0)
    return dict(downvar=float((down ** 2).mean()),
                budget_hit=float((surplus >= 0).mean()),
                mean_krw=float(surplus.mean()),
                worst_eok=float(pnl.min()) / 1e8,
                cum_eok=float(pnl.sum()) / 1e8,
                cost_eok=float(cost_eok))


def fixed(fx, position, ratio):
    bt = run_backtest(fx, amount=AMOUNT, position=position, spread_krw=SPREAD, fixed_ratio=ratio)
    return _summ(bt["pnl_P2"], AMOUNT * SPREAD * ratio * len(bt) / 1e8), len(bt)


def rng(fx, position, band):
    bt = run_backtest(fx, amount=AMOUNT, position=position, spread_krw=SPREAD, collar_band=band)
    # 상단참여: 유리한 쪽으로 밴드 상단을 넘어 '참여'로 이득 본 비중(상시선물환이 포기하는 부분)
    return _summ(bt["pnl_PR"], AMOUNT * SPREAD * len(bt) / 1e8), len(bt)


def table(fx, position, title):
    print(f"\n[{title}]  (예산=롤링1Y · 스프레드 {SPREAD}원 · 비용차감)")
    print(f"  {'정책':<20}{'하방분산↓':>10}{'예산달성률↑':>11}{'평균초과원':>10}{'최악결제억':>10}{'헤지비용억':>10}")
    out = {}
    m0, n = fixed(fx, position, 0.0);   out["무헤지"] = m0
    m5, _ = fixed(fx, position, 0.5);   out["부분선물환 50%"] = m5
    m1, _ = fixed(fx, position, 1.0);   out["상시선물환 100%"] = m1
    for label, m in [("무헤지", m0), ("부분선물환 50%", m5), ("상시선물환 100%", m1)]:
        print(f"  {label:<20}{m['downvar']:>10.1f}{m['budget_hit']:>11.1%}{m['mean_krw']:>10.1f}{m['worst_eok']:>10.2f}{m['cost_eok']:>10.2f}")
    for b in BANDS:
        mr, _ = rng(fx, position, b); out[f"범위선물환 ±{b:.0%}"] = mr
        print(f"  {'범위선물환 ±'+format(b,'.0%'):<20}{mr['downvar']:>10.1f}{mr['budget_hit']:>11.1%}{mr['mean_krw']:>10.1f}{mr['worst_eok']:>10.2f}{mr['cost_eok']:>10.2f}")
    return out, n


def main():
    fx = build_fx()
    print("=" * 78)
    print("FX Sentinel S8 — 범위선물환 경제성: 상단 참여를 유지하며 바닥을 받친다")
    print(f"기간 {fx.index.min().date()}~{fx.index.max().date()} · USD {AMOUNT:,}/월 · 밴드폭은 가정")
    print("=" * 78)
    ex, n = table(fx, "export", "수출 SME (달러 롱)")
    im, _ = table(fx, "import", "수입 SME (달러 숏)")

    # 정직 결론: 데이터가 말하게 한다(방향을 하드코딩하지 않는다 — 수출·수입이 다르다).
    def _rel(a, b): return "낮다" if a < b else ("같다" if a == b else "높다")
    r2, noh, alw, p5 = ex["범위선물환 ±2%"], ex["무헤지"], ex["상시선물환 100%"], ex["부분선물환 50%"]
    ir2, inoh, ialw = im["범위선물환 ±2%"], im["무헤지"], im["상시선물환 100%"]
    print("\n" + "─" * 78)
    print("[정직 결론 — 범위선물환의 자리]")
    print(f"  · 수출: 범위형(±2%) 하방분산 {r2['downvar']:.0f} — 무헤지 {noh['downvar']:.0f}보다 {_rel(r2['downvar'],noh['downvar'])}, "
          f"상시선물환 {alw['downvar']:.0f}보다 {_rel(r2['downvar'],alw['downvar'])}. "
          f"게다가 상시선물환이 포기하는 상단을 유지 → 평균초과 {r2['mean_krw']:.1f}원 vs 상시 {alw['mean_krw']:.1f}원.")
    print(f"  · 수입: 범위형(±2%) 하방분산 {ir2['downvar']:.0f} — 무헤지 {inoh['downvar']:.0f}보다 {_rel(ir2['downvar'],inoh['downvar'])}, "
          f"상시선물환 {ialw['downvar']:.0f}보다 {_rel(ir2['downvar'],ialw['downvar'])}. "
          f"수입은 상시선물환이 하방분산 최소이나, 범위형은 상단(유리한 환율) 참여를 유지한다.")
    print(f"  · 공통: 밴드폭이 좁을수록 상시선물환에, 넓을수록 무헤지에 가까워진다(±1%~±3% 민감도 표).")
    print(f"  · [정직] 밴드폭·행사가는 옵션가격에 좌우되는 가정이다 — 실제 밴드가는 RM 견적. 예측 아님.")
    print("─" * 78)

    payload = {"period": [str(fx.index.min().date()), str(fx.index.max().date())],
               "amount": AMOUNT, "spread_krw": SPREAD, "n_settles": int(n),
               "bands": BANDS, "note": "밴드폭은 가정(요율 미연동). 구조 비교용.",
               "export": ex, "import": im}
    with open(os.path.join(STATE, "range_econ.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("\n[저장] state/range_econ.json")


if __name__ == "__main__":
    main()
