"""
FX Sentinel — BBP 엔진 (예산환율 이탈확률 · 히어로 지표)
=========================================================
v2 재정렬(F 문서)의 자체 정량 기여. 환율을 '예측'하지 않고, 이미 알려진
회사의 예산환율·포지션·결제만기를 **정량 리스크 + 헤지 의사결정**으로 번역한다.

핵심 출력:
  BBP  예산환율 이탈확률 P(예산환율 K가 향후 N일 내 불리하게 깨질 확률)
       — Student-t 팻테일 + 정책개입(IZ) 비대칭 절단 + drift=0(무예측)
  ES   기대 이탈손실(이탈 시 예산 대비 평균 손실, 원/달러·총액)
  권고  헤지 수단 트리아지(확실성×여신×현금, 청사진 §6.4) + 헤지비율(LossAlert)

설계 근거: A §4.3(방향층) · 청사진 §6.4(트리아지) · F §3.1(BBP 정의)
"""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field
import numpy as np
from scipy import stats

TRADING = 252


# ──────────────────────────────────────────── 회사 프로필 입력
@dataclass
class CompanyProfile:
    name: str = "가상 수출기업"
    position: str = "export"          # export(달러 롱) / import(달러 숏)
    budget_rate: float = 1300.0       # K, 사업계획 손익분기 환율
    amount_usd: float = 500_000.0     # 결제 1건 규모(USD)
    horizon_days: int = 63            # 결제 만기까지 영업일(≈3M)
    certainty: str = "confirmed"      # provisional(PO·취소가능) / confirmed(인보이스·확정)
    has_credit_line: bool = True      # 선물환용 여신 한도 유무
    cash_constrained: bool = False    # 옵션 프리미엄 현금부담 여부
    sector: str = "전자부품"
    currency: str = "USD"             # 결제 통화(다통화 확장, fx.py CURRENCIES 키)

    @staticmethod
    def from_json(path: str) -> "CompanyProfile":
        with open(path, encoding="utf-8") as f:
            return CompanyProfile(**json.load(f))

    def validate(self):
        assert self.position in ("export", "import")
        assert self.certainty in ("provisional", "confirmed")
        assert self.budget_rate > 0 and self.amount_usd > 0 and self.horizon_days > 0


# ──────────────────────────────────────────── 시장 상태
@dataclass
class MarketState:
    date: str
    spot: float                       # 현재 원/달러
    sigma_ann: float                  # 연율 변동성 예측(σ_fwd, FX-EWI vol-head/레벨)
    fx_ewi: float = 50.0              # 위험 게이지 0~100 (XAI)
    iz: int = 0                       # 정책개입 국면 플래그


# ──────────────────────────────────────────── BBP (예산환율 이탈확률)
def budget_breach_probability(spot: float, K: float, sigma_ann: float, horizon_bd: int,
                              position: str = "export", nu: float = 5.0, iz: int = 0) -> float:
    """P(예산환율 불리이탈). Student-t CDF(팻테일), drift=0. IZ 시 비대칭 절단.
    수출: 불리 = S_settle < K (원화강세). 수입: 불리 = S_settle > K.
    """
    tau = horizon_bd / TRADING
    denom = sigma_ann * np.sqrt(tau)
    if denom <= 0 or not np.isfinite(denom):
        return float("nan")
    z = (np.log(K) - np.log(spot)) / denom
    if position == "export":
        p = float(stats.t.cdf(z, df=nu))          # P(S<K)
        if iz:                                     # 개입: 원화 추가강세(S↓) 저지 → 하방이탈 축소
            p *= 0.7
    else:
        p = float(stats.t.sf(z, df=nu))            # P(S>K)
        if iz:                                     # 개입: 원화 추가약세(S↑) 저지
            p *= 0.7
    return float(np.clip(p, 0, 1))


def expected_shortfall(spot: float, K: float, sigma_ann: float, horizon_bd: int,
                       position: str = "export", nu: float = 5.0,
                       n: int = 40_000, seed: int = 7) -> float:
    """기대 이탈손실(원/달러) = E[(K−S_T)+] (수출) — Student-t 몬테카를로."""
    tau = horizon_bd / TRADING
    sd = sigma_ann * np.sqrt(tau)
    rng = np.random.default_rng(seed)
    t = rng.standard_t(nu, n)
    scale = sd / np.sqrt(nu / (nu - 2))            # t를 목표 표준편차로 스케일
    S_T = spot * np.exp(scale * t)                  # drift=0
    loss = np.maximum(K - S_T, 0) if position == "export" else np.maximum(S_T - K, 0)
    return float(loss.mean())


def hedge_ratio_from(loss_alert: float, thresholds=(0.15, 0.35)) -> float:
    lo, hi = thresholds
    return 0.0 if loss_alert < lo else (0.5 if loss_alert < hi else 1.0)


# ──────────────────────────────────────────── 헤지 수단 트리아지 (청사진 §6.4)
def recommend_instrument(p: CompanyProfile) -> tuple[str, str]:
    """확실성 × 여신 × 현금 → 실행가능 수단."""
    if p.certainty == "provisional":
        return ("제로코스트 칼라 / 환변동보험",
                "가결제(취소가능) → 비대칭 청산(최대손실=프리미엄). 선물환 금지: 취소 시 반대매매 손실 노출")
    if not p.has_credit_line:
        return ("K-SURE 환변동보험 + 여신창출 체인",
                "여신 사각지대 → 공적보증으로 KB 선물환 특별한도 자동창출(가정·확인 필요), 한도는 실수요 내")
    if p.cash_constrained:
        return ("제로코스트 칼라",
                "여신 있음 + 프리미엄 현금부담 → 풋매수=콜매도 상쇄로 선납비용 0")
    return ("선물환", "여신 있음 + 현금 여유 → 표준 최저비용 헤지")


def grade_of(ewi: float) -> str:
    return "정상" if ewi < 40 else ("주의" if ewi < 60 else ("경계" if ewi < 80 else "심각"))


# ──────────────────────────────────────────── 종합 진단
def assess(profile: CompanyProfile, market: MarketState, nu: float = 5.0) -> dict:
    profile.validate()
    bbp = budget_breach_probability(market.spot, profile.budget_rate, market.sigma_ann,
                                    profile.horizon_days, profile.position, nu, market.iz)
    es = expected_shortfall(market.spot, profile.budget_rate, market.sigma_ann,
                            profile.horizon_days, profile.position, nu)
    loss_alert = (market.fx_ewi / 100.0) * bbp
    h = hedge_ratio_from(loss_alert)
    instrument, rationale = recommend_instrument(profile)
    es_total = es * profile.amount_usd
    return {
        "profile": asdict(profile),
        "market": asdict(market),
        "BBP": bbp,
        "BBP_pct": round(bbp * 100, 1),
        "ES_per_usd": round(es, 2),
        "ES_total_krw": round(es_total),
        "LossAlert": round(loss_alert, 3),
        "hedge_ratio": h,
        "hedge_amount_usd": round(profile.amount_usd * h),
        "gauge_grade": grade_of(market.fx_ewi),
        "instrument": instrument,
        "rationale": rationale,
    }


def report(a: dict) -> str:
    p, m = a["profile"], a["market"]
    posk = "원화강세(S<K)" if p["position"] == "export" else "원화약세(S>K)"
    lines = [
        "─" * 60,
        f" FX Sentinel — 예산환율 리스크 번역 리포트",
        f" {p['name']} ({p['sector']}, {'수출' if p['position']=='export' else '수입'}) · {m['date']}",
        "─" * 60,
        f" 현재 원/달러      : {m['spot']:,.1f}   (예산환율 K={p['budget_rate']:,.0f}, 만기 {p['horizon_days']}영업일)",
        f" 변동성(연율)      : {m['sigma_ann']*100:.1f}%" + ("   [정책개입 국면]" if m['iz'] else ""),
        "",
        f" ▶ 예산환율 이탈확률(BBP) : {a['BBP_pct']:.1f}%   (불리방향={posk})",
        f" ▶ 이탈 시 기대손실(ES)   : {a['ES_per_usd']:.1f}원/달러  →  총 {a['ES_total_krw']:,}원",
        f" ▶ 위험 게이지(FX-EWI)    : {m['fx_ewi']:.0f}/100  [{a['gauge_grade']}]",
        "",
        f" ▷ 권고 헤지수단 : {a['instrument']}",
        f"   근거          : {a['rationale']}",
        f" ▷ 권고 헤지비율 : {a['hedge_ratio']:.0%}  (헤지 {a['hedge_amount_usd']:,} USD)  · LossAlert={a['LossAlert']}",
        "─" * 60,
        " ※ 정보 제공이며 최종 판단·실행은 KB 영업점·전문 상담. 환율을 예측하지 않으며,",
        "    이탈확률은 회사 예산환율·포지션 기준의 정량 리스크 번역입니다.",
        "─" * 60,
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────── CLI / 데모
def _latest_market(date: str | None = None) -> MarketState:
    """state/fx_ewi_timeseries.csv에서 시장상태 조달(없으면 합리적 기본)."""
    import os, pandas as pd
    csv = os.path.join(os.path.dirname(__file__), "state", "fx_ewi_timeseries.csv")
    if os.path.exists(csv):
        d = pd.read_csv(csv, index_col=0, parse_dates=True).dropna(subset=["FX_EWI", "sigma_hat"])
        row = d.loc[date] if (date and date in d.index) else d.iloc[-1]
        return MarketState(date=str(d.index[-1].date() if not date else date),
                           spot=float(row["Close"]), sigma_ann=float(row["sigma_hat"]),
                           fx_ewi=float(row["FX_EWI"]), iz=int(row["IZ"]))
    return MarketState(date="live", spot=1528.8, sigma_ann=0.09, fx_ewi=55, iz=0)


def main():
    import argparse, sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    ap = argparse.ArgumentParser(description="BBP 예산환율 리스크 번역기")
    ap.add_argument("--profile", help="회사 프로필 JSON 경로")
    ap.add_argument("--name"); ap.add_argument("--position", choices=["export", "import"])
    ap.add_argument("--budget", type=float); ap.add_argument("--amount", type=float)
    ap.add_argument("--horizon", type=int); ap.add_argument("--certainty", choices=["provisional", "confirmed"])
    ap.add_argument("--no-credit", action="store_true"); ap.add_argument("--cash-tight", action="store_true")
    ap.add_argument("--date", help="시장상태 기준일(YYYY-MM-DD)")
    ap.add_argument("--demo", action="store_true", help="예시 프로필 3종 시연")
    args = ap.parse_args()

    if args.demo or not any([args.profile, args.name]):
        m = _latest_market(args.date)
        demos = [
            CompanyProfile("한빛정밀(수출)", "export", 1450, 500_000, 63, "confirmed", True, False),
            CompanyProfile("대성무역(가결제)", "export", 1480, 300_000, 63, "provisional", True, True),
            CompanyProfile("소망전자(무여신)", "export", 1500, 200_000, 42, "confirmed", False, False),
            CompanyProfile("나래상사(수입)", "import", 1500, 400_000, 63, "confirmed", True, False),
        ]
        for pf in demos:
            print(report(assess(pf, m)))
        return

    pf = CompanyProfile.from_json(args.profile) if args.profile else CompanyProfile(
        name=args.name or "입력기업", position=args.position or "export",
        budget_rate=args.budget or 1300, amount_usd=args.amount or 500_000,
        horizon_days=args.horizon or 63, certainty=args.certainty or "confirmed",
        has_credit_line=not args.no_credit, cash_constrained=args.cash_tight)
    print(report(assess(pf, _latest_market(args.date))))


if __name__ == "__main__":
    main()
