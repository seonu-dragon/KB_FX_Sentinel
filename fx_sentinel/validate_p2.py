"""
FX Sentinel — Phase 2 검증 부채 상환: walk-forward + PBO/DSR로 P2 우위 굳히기
=============================================================================
설계 근거: C §2.4(동적헤지 과최적화 검증) · D§⑤(사전등록) · D§③(PBO/DSR 귀속처)

검증 대상 = 헤지비율 임계 (lo, hi) — P2 정책의 유일한 튜닝 자유도.
  ① PBO(CSCV): IS 최고 임계가 OOS 중앙값 미만일 확률. 0.5↑=과최적화. (AUTO_BUY_SELL 하네스 재사용)
  ② DSR: 선택 임계의 Sharpe가 N회 시험의 '운' 상한을 넘는지.
  ③ Walk-forward(확장창): 과거로만 임계를 고른 OOS P2가 P1·고정헤지를 이기나 (무누수 핵심).

성과 언어: 헤지는 하방위험 방어가 본질 → Sortino(하방편차 기준). PBO는 하네스 Sharpe 재사용(D§③).
주의: 71개 월별 결제 = 표본 얇음(단일 페르소나). 데모 수준 정량화, 표본확대는 다음.
"""
from __future__ import annotations
import os, sys, json, importlib.util
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
import pandas as pd

from run_phase2 import build_fx, AMOUNT
from backtest import run_backtest

# --- AUTO_BUY_SELL 과최적화 하네스 재사용 (패키지명 충돌 회피: 파일 직접 로드) ---
HARNESS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                          "..", "..", "..", "AUTO_BUY_SELL", "backtest", "overfitting.py"))
_spec = importlib.util.spec_from_file_location("abs_overfitting", HARNESS)
ovf = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(ovf)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state"); os.makedirs(STATE, exist_ok=True)

LOS = [0.05, 0.10, 0.15, 0.20, 0.25]
HIS = [0.30, 0.35, 0.40, 0.45, 0.50]
SELECTED = (0.15, 0.35)   # 사전등록 주 스펙 (D§⑤)


def sortino(surplus: pd.Series) -> float:
    """하방편차 기준 위험조정 성과 (헤지 목적함수에 부합)."""
    s = surplus.dropna()
    if len(s) < 3: return np.nan
    downside = np.sqrt((s.clip(upper=0) ** 2).mean())
    return float(s.mean() / downside) if downside > 0 else np.nan


def downside_var(surplus: pd.Series) -> float:
    s = surplus.dropna()
    return float((s.clip(upper=0) ** 2).mean())


def main():
    fx = build_fx()

    # --- 전 config별 결제단위 surplus(=pnl_P2/amount) 사전계산 ---
    configs = [(lo, hi) for lo in LOS for hi in HIS if hi > lo]
    names = [f"lo{lo:.2f}_hi{hi:.2f}" for lo, hi in configs]
    surplus_by_cfg = {}
    ratio_by_cfg = {}
    base_bt = None
    for (lo, hi), nm in zip(configs, names):
        bt = run_backtest(fx, amount=AMOUNT, spread_krw=2.0, p_cancel=0.0,
                          ratio_thresholds=(lo, hi))
        surplus_by_cfg[nm] = bt["pnl_P2"] / AMOUNT
        ratio_by_cfg[nm] = bt["h"]
        if (lo, hi) == SELECTED:
            base_bt = bt
    S = pd.DataFrame(surplus_by_cfg)          # (결제 × config)
    sel_name = f"lo{SELECTED[0]:.2f}_hi{SELECTED[1]:.2f}"

    # P1(상시헤지)·P0 surplus (동일 결제 정렬)
    p1_surplus = base_bt["pnl_P1"] / AMOUNT
    p0_surplus = base_bt["pnl_P0"] / AMOUNT
    n_distinct = S.T.drop_duplicates().shape[0]

    print("=" * 70)
    print("FX Sentinel — P2 검증 부채 상환 (walk-forward + PBO/DSR)")
    print(f"결제 {len(S)}건 | 임계 trial {len(configs)}개(구별 {n_distinct}) | 선택 {SELECTED}")
    print("=" * 70)

    # ── ① PBO (CSCV, 하네스 재사용) ──
    pbo = ovf.probability_backtest_overfitting(S, n_partitions=8)
    print(f"\n[①PBO] {pbo.get('pbo')}  ({pbo.get('n_combos')}개 CSCV 조합) "
          f"→ {'과최적화 위험 높음' if (pbo.get('pbo') or 1)>=0.5 else ('주의' if (pbo.get('pbo') or 1)>=0.3 else '양호(운 아님)')}")
    print("      · PBO=IS 최고 임계가 OOS 중앙값 미만일 확률. 0.5↑=선택이 미래에 안 통할 위험.")

    # ── ② DSR (선택 임계) ──
    sr_daily = S.mean() / S.std()
    var_sr = float(sr_daily.var(ddof=1))
    dsr = ovf.deflated_sharpe_ratio(S[sel_name], n_trials=len(configs), var_sr_trials=var_sr)
    verdict = "유의(운 아님)" if (dsr["deflated_sharpe_ratio"] or 0) >= 0.95 else \
              ("애매" if (dsr["deflated_sharpe_ratio"] or 0) >= 0.5 else "유의하지않음")
    print(f"\n[②DSR] 선택 임계 Sharpe(월) {dsr['sharpe_daily']:.3f} vs 기대최대 {dsr['expected_max_sharpe_daily']:.3f} "
          f"→ DSR={dsr['deflated_sharpe_ratio']} ({verdict})")

    # ── ③ Walk-forward (확장창, 무누수) ──
    settles = list(S.index)
    min_train = 24
    oos_p2, oos_p2_dv = {}, {}
    for i in range(min_train, len(settles)):
        train = settles[:i]; test = settles[i]
        # (a) 과거로만 Sortino 최고 임계 선택
        sc_sortino = {nm: sortino(S[nm].loc[train]) for nm in names}
        best_a = max(sc_sortino, key=lambda k: (sc_sortino[k] if np.isfinite(sc_sortino[k]) else -1e9))
        oos_p2[test] = S[best_a].loc[test]
        # (b) 과거로만 하방분산 최소 임계 선택 (목적함수 직접)
        sc_dv = {nm: downside_var(S[nm].loc[train]) for nm in names}
        best_b = min(sc_dv, key=lambda k: sc_dv[k])
        oos_p2_dv[test] = S[best_b].loc[test]
    oos_p2 = pd.Series(oos_p2)
    oos_p2_dv = pd.Series(oos_p2_dv)
    oos_idx = oos_p2.index
    p1_oos = p1_surplus.loc[oos_idx]
    p0_oos = p0_surplus.loc[oos_idx]
    # 고정 30%(평균비율) 벤치마크 — OOS 구간
    hs = base_bt["h"].mean()
    eff_static = hs * (base_bt["F"] - 2.0) + (1 - hs) * base_bt["S1"]
    static_surplus = (AMOUNT * (eff_static - base_bt["K"]) / AMOUNT).loc[oos_idx]

    print(f"\n[③Walk-forward] 확장창(min_train={min_train}) OOS 결제 {len(oos_p2)}건")
    print(f"      {'정책':<20}{'Sortino':>9}{'하방분산':>10}{'누적PnL억':>11}")
    for nm, s in [("P0 무헤지", p0_oos), ("P1 상시헤지", p1_oos),
                  (f"고정{hs:.0%}헤지", static_surplus),
                  ("P2 WF(Sortino선택)", oos_p2), ("P2 WF(하방분산선택)", oos_p2_dv)]:
        print(f"      {nm:<20}{sortino(s):>9.3f}{downside_var(s):>10.1f}{s.sum()*AMOUNT/1e8:>11.2f}")

    wf_win_p1 = downside_var(oos_p2) < downside_var(p1_oos)
    wf_win_static = downside_var(oos_p2) < downside_var(static_surplus)
    print(f"\n[판정] OOS P2 하방분산 < P1: {wf_win_p1} | < 고정헤지: {wf_win_static}"
          f"  → {'무누수에서도 P2 우위 굳힘 ✓' if (wf_win_p1 and wf_win_static) else '우위 일부만/미성립 — 재점검'}")

    payload = {"n_settles": len(S), "n_trials": len(configs), "selected": list(SELECTED),
               "pbo": pbo.get("pbo"), "dsr": dsr["deflated_sharpe_ratio"],
               "wf_oos_n": len(oos_p2),
               "wf_downside_var": {"P0": downside_var(p0_oos), "P1": downside_var(p1_oos),
                                    "static": downside_var(static_surplus), "P2": downside_var(oos_p2)},
               "wf_sortino": {"P0": sortino(p0_oos), "P1": sortino(p1_oos),
                               "static": sortino(static_surplus), "P2": sortino(oos_p2)},
               "wf_beats_p1": bool(wf_win_p1), "wf_beats_static": bool(wf_win_static)}
    with open(os.path.join(STATE, "validation_p2.json"), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print(f"\n[저장] state/validation_p2.json")


if __name__ == "__main__":
    main()
