"""
FX Sentinel — Phase 2: 경제성 P&L 백테스트 (킬러 슬라이드)
==========================================================
설계 근거: FX_Sentinel_C_검증_경제성설계.md §1·§2 / A §4.3(방향층)

정책:
  P0 무헤지(Never)          — 전량 현물 노출
  P1 상시 100% 헤지(Always) — 매 익스포저를 만기맞춤 선물환 전량 (실무 나이브 베이스라인)
  P2 FX-EWI 동적 선물환      — 헤지비율 = f(LossAlert), 난류×불리방향 클수록 커버리지↑

목적함수(D§①, 확정):
  주지표  U = E[surplus] − λ·DownsideVar[surplus]   (P1은 분산0이나 상방 전량포기)
  보조    비용당 하방분산 감소효율 = ΔDownsideVar / ΔHedgeCost  (vs P0)

방향층(A §4.3): LossAlert = (FX_EWI/100) × P_breach
  P_breach = 예산환율 K 불리이탈 확률 (수출기업: P(S_settle<K)), Student-t(팻테일), drift=0(무예측).
  개입국면(IZ) 시 비대칭 절단(A §3.6): 개입 방향 꼬리 축소.

비용(§1.1·§1.7): 선물환 F=CIP 스왑포인트, 은행 스프레드 차감(SME 스윕 §1.7·D§⑥).
취소(D§③): 가결제 취소 시 선물환 반대매매 비용(유계 아님) — 확실성 축 검증.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from bbp import budget_breach_probability as p_breach, hedge_ratio_from as hedge_ratio

TRADING = 252


# -------------------------------------------------------- 헤지비용 (CIP)
def cip_forward(S: float, r_d: float, r_f: float, tau: float) -> float:
    """커버드 금리평가 선물환율. F = S·(1+r_d·τ)/(1+r_f·τ). §1.1"""
    return S * (1 + r_d * tau) / (1 + r_f * tau)

# 방향층(BBP)·헤지비율은 bbp.py 단일 소스에서 import (F 재정렬: BBP가 히어로 지표).


# -------------------------------------------------------- 백테스트 엔진
def month_end_settles(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """각 월 마지막 영업일(수출대금 유입일)."""
    s = pd.Series(index, index=index)
    return s.groupby([index.year, index.month]).last().values


def run_backtest(fx: pd.DataFrame, amount: float = 500_000, lead_bd: int = 63,
                 position: str = "export", spread_krw: float = 2.0,
                 budget: str = "roll1y", nu: float = 5.0,
                 ratio_thresholds=(0.15, 0.35), fixed_ratio: float | None = None,
                 collar_band: float | None = None,
                 p_cancel: float = 0.0, seed: int = 7) -> pd.DataFrame:
    """월별 결제 시뮬. fx=compute_fx_ewi 출력(+ Close,r,IZ,FX_EWI,sigma_hat).
    반환: 결제별 정책 P&L 테이블.
    """
    rng = np.random.default_rng(seed)
    idx = fx.index
    close = fx["Close"]
    rollK = close.rolling(TRADING, min_periods=TRADING // 2).mean()  # 롤링 예산환율(사업계획 근사)

    settles = pd.DatetimeIndex(month_end_settles(idx))
    rows = []
    for t1 in settles:
        pos = idx.get_indexer([t1])[0]
        if pos - lead_bd < TRADING:   # 워밍업(롤z·롤K) 확보
            continue
        t0 = idx[pos - lead_bd]
        S0, S1 = close.loc[t0], close.loc[t1]
        r_d, r_f = fx.loc[t0, "r_d"], fx.loc[t0, "r_f"]
        if not np.isfinite([S0, S1, r_d, r_f]).all():
            continue
        tau = lead_bd / TRADING
        F = cip_forward(S0, r_d, r_f, tau)
        K = rollK.loc[t0] if budget == "roll1y" else float(budget)
        ewi = fx.loc[t0, "FX_EWI"]; sig = fx.loc[t0, "sigma_hat"]; iz = int(fx.loc[t0, "IZ"])
        if not np.isfinite([ewi, sig, K]).all():
            continue
        pb = p_breach(S0, K, sig, lead_bd, position, nu, iz)
        loss_alert = (ewi / 100.0) * pb
        h = hedge_ratio(loss_alert, ratio_thresholds)
        if fixed_ratio is not None:      # 정적 부분헤지(S7 정직 비교)
            h = fixed_ratio

        sp = spread_krw
        # 수출: 달러 매도(F−sp 불리) / 수입: 달러 매수(F+sp 불리)
        hedged_rate = (F - sp) if position == "export" else (F + sp)
        eff_P0 = S1
        eff_P1 = hedged_rate
        eff_P2 = h * hedged_rate + (1 - h) * S1

        # 범위선물환(제로코스트 칼라): 밴드[F(1±band)] 안은 시장 참여, 밖은 밴드가로 정산.
        # 밴드폭은 옵션가격에 좌우되므로 '가정'이다(요율 미연동) — 상단 이익 일부 유지 구조만 검증.
        eff_PR = None
        if collar_band is not None:
            floor, cap = F * (1.0 - collar_band), F * (1.0 + collar_band)
            base_R = min(max(S1, floor), cap)
            eff_PR = (base_R - sp) if position == "export" else (base_R + sp)

        # 취소 이벤트(가결제) — 선물환 반대매매(유계 아님)
        cancelled = rng.random() < p_cancel
        unwind_P1 = unwind_P2 = 0.0
        if cancelled:
            tc = idx[pos - lead_bd // 2]
            Stc = close.loc[tc]
            # 네이키드 선물 청산: (F − Stc) 반대매매 + 스프레드 (수출대금은 사라짐)
            unwind_P1 = amount * ((F - Stc) - sp)      # P1 100% 헤지분
            unwind_P2 = amount * h * ((F - Stc) - sp)  # P2 h 헤지분

        row = dict(
            t0=t0, t1=t1, S0=S0, S1=S1, F=F, K=K, r_diff=r_d - r_f,
            FX_EWI=ewi, sigma=sig, IZ=iz, P_breach=pb, LossAlert=loss_alert, h=h,
            eff_P0=eff_P0, eff_P1=eff_P1, eff_P2=eff_P2, cancelled=int(cancelled),
            unwind_P1=unwind_P1, unwind_P2=unwind_P2)
        if eff_PR is not None:
            row["eff_PR"] = eff_PR
        rows.append(row)
    bt = pd.DataFrame(rows).set_index("t1")
    # 결제별 예산 대비 P&L (KRW). 수출: amount×(eff−K), 수입: amount×(K−eff). 취소분은 반대매매 손익.
    sgn = 1.0 if position == "export" else -1.0
    for p in ["P0", "P1", "P2"]:
        base = sgn * amount * (bt[f"eff_{p}"] - bt["K"])
        if p == "P0":
            bt[f"pnl_{p}"] = np.where(bt["cancelled"] == 1, 0.0, base)  # 무헤지: 취소시 손익0
        else:
            bt[f"pnl_{p}"] = np.where(bt["cancelled"] == 1, bt[f"unwind_{p}"], base)
    # 헤지비용(스프레드 지출, KRW)
    bt["cost_P1"] = amount * spread_krw
    bt["cost_P2"] = amount * spread_krw * bt["h"]
    # 범위선물환(제로코스트 칼라) — 프리미엄 0, 실행 스프레드만(확정 거래 가정: 취소분 손익 0)
    if "eff_PR" in bt.columns:
        base_R = sgn * amount * (bt["eff_PR"] - bt["K"])
        bt["pnl_PR"] = np.where(bt["cancelled"] == 1, 0.0, base_R)
        bt["cost_PR"] = amount * spread_krw
    return bt


# -------------------------------------------------------- 성과지표
def metrics(bt: pd.DataFrame, amount: float, lam: float = 1e-9) -> pd.DataFrame:
    """정책별 목적함수·위험·비용 (D§① 목적함수)."""
    out = {}
    for p in ["P0", "P1", "P2"]:
        pnl = bt[f"pnl_{p}"]
        surplus = pnl / amount              # 단위당(원/달러) 초과
        downside = surplus.clip(upper=0)    # 하방(예산 미달)만
        dvar = float((downside ** 2).mean())
        cum = float(pnl.sum())
        cost = float(bt[f"cost_{p}"].sum()) if p != "P0" else 0.0
        out[p] = dict(
            누적PnL_억=cum / 1e8,
            평균초과_원=float(surplus.mean()),
            표준편차_원=float(surplus.std()),
            하방분산=dvar,
            Sharpe=float(surplus.mean() / surplus.std()) if surplus.std() else np.nan,
            예산달성률=float((surplus >= 0).mean()),
            최악결제_억=float(pnl.min()) / 1e8,
            총헤지비용_억=cost / 1e8,
            평균헤지비율=float(bt["h"].mean()) if p == "P2" else (1.0 if p == "P1" else 0.0),
            효용U_억=(cum - lam * dvar * amount * len(bt)) / 1e8,
        )
    m = pd.DataFrame(out).T
    # 보조지표: 비용당 하방분산 감소효율 (vs P0)
    d0 = m.loc["P0", "하방분산"]
    for p in ["P1", "P2"]:
        dd = d0 - m.loc[p, "하방분산"]
        cc = m.loc[p, "총헤지비용_억"] * 1e8
        m.loc[p, "분산감소_효율"] = (dd * amount * len(bt)) / cc if cc else np.nan
    return m
