"""
FX Sentinel — 다통화 확장 레이어 (설계 A §7)
============================================
USD/KRW 기준 파이프라인을 통화쌍별로 복제(모듈형). BBP 수식은 통화-불가지론적이라
통화별 (현재가·연율변동성 σ·자국/상대국 3M 금리)만 주입하면 그대로 성립한다.

- USD/KRW: 2018~2026 실데이터 기반 σ·현재가 + FRED 금리(r_home=KRW 2.82%, r_f=USD 3.74%).
- EUR·JPY·CNY·VND: **대표 스냅샷 파라미터** — 통화별 실히스토리 파이프라인 복제는
  데이터 확보 시 동일 코드로 확장(§7). 파라미터 출처·시점을 source 라벨에 명시.

BBP/ES 계산은 bbp.py의 결정론 엔진을 그대로 재사용한다(단일 소스).
CIP 선물환도 통화별 상대국 금리로 산출한다.
"""
from __future__ import annotations
from dataclasses import dataclass

from bbp import budget_breach_probability, expected_shortfall

TRADING = 252
R_HOME = 0.0282  # 원화 3M 자국 금리 — FRED 최근(rates.csv)


@dataclass
class Currency:
    code: str
    label: str        # 표시명
    unit: int         # 호가 단위(1 또는 100)
    spot: float       # 현재가 (원 / 단위)
    sigma_ann: float  # 연율 변동성
    r_foreign: float  # 상대국 3M 금리(소수)
    source: str       # 데이터 출처/시점


# 원 / (단위) 기준. JPY·VND는 100단위 호가(가독성).
CURRENCIES: dict[str, Currency] = {
    "USD": Currency("USD", "미국 달러 (USD/KRW)",       1, 1528.8,  0.098, 0.0374, "USD/KRW 2018–2026 실데이터 · FRED"),
    "EUR": Currency("EUR", "유로 (EUR/KRW)",            1, 1662.0,  0.086, 0.0245, "대표 스냅샷 — 실히스토리 확장 예정"),
    "JPY": Currency("JPY", "일본 엔 (JPY100/KRW)",    100,  982.0,  0.112, 0.0055, "대표 스냅샷 — 실히스토리 확장 예정"),
    "CNY": Currency("CNY", "중국 위안 (CNY/KRW)",       1,  210.5,  0.058, 0.0190, "대표 스냅샷 — 실히스토리 확장 예정"),
    "VND": Currency("VND", "베트남 동 (VND100/KRW)",  100,  6.20,   0.049, 0.0460, "대표 스냅샷 — 실히스토리 확장 예정"),
}


def cip_forward(spot: float, r_home: float, r_foreign: float, tau: float) -> float:
    """커버드 금리평가 선물환. F = S·(1+r_home·τ)/(1+r_foreign·τ)."""
    return spot * (1 + r_home * tau) / (1 + r_foreign * tau)


def assess_currency(code: str, budget_rate: float, amount_units: float, horizon_days: int,
                    position: str = "export", sigma_override: float | None = None,
                    iz: int = 0, nu: float = 5.0) -> dict:
    """통화별 예산환율 이탈확률·기대손실·CIP 선물환 산출(엔진은 bbp.py 재사용)."""
    c = CURRENCIES[code]
    sig = c.sigma_ann if sigma_override is None else sigma_override
    tau = horizon_days / TRADING
    bbp = budget_breach_probability(c.spot, budget_rate, sig, horizon_days, position, nu, iz)
    es_unit = expected_shortfall(c.spot, budget_rate, sig, horizon_days, position, nu)  # 원/단위
    fwd = cip_forward(c.spot, R_HOME, c.r_foreign, tau)
    return {
        "code": code, "label": c.label, "unit": c.unit, "spot": c.spot, "sigma_ann": sig,
        "budget_rate": budget_rate, "position": position, "horizon_days": horizon_days,
        "BBP": bbp, "BBP_pct": round(bbp * 100, 1),
        "ES_per_unit": round(es_unit, 6), "ES_total_krw": round(es_unit * amount_units),
        "forward": round(fwd, 4), "swap_point": round(fwd - c.spot, 4),
        "rate_diff_pct": round((R_HOME - c.r_foreign) * 100, 2),
        "source": c.source,
    }


def _demo():
    """같은 KRW 노셔널·같은 상대적 쿠션(수출 3%)에서 통화별 이탈확률·기대손실 비교.
    현재가·금리가 달라도 BBP를 가르는 핵심은 통화별 변동성 σ임을 보인다."""
    notional_krw = 150_000_000  # 통화 무관 동일 노셔널로 ES 비교 공정화
    print("동일 노셔널 1.5억원 · 수출(3% 쿠션) · 만기 63영업일 기준")
    print(f"{'통화':22} {'현재가':>10} {'σ':>6} {'이탈확률':>7} {'기대손실':>9} {'선물환':>10} {'금리차':>6}")
    print("─" * 78)
    for code in ["JPY", "USD", "EUR", "CNY", "VND"]:
        c = CURRENCIES[code]
        K = round(c.spot * 0.97, 6)          # 수출 불리=원화강세(S<K). 3% 쿠션.
        units = notional_krw / c.spot
        a = assess_currency(code, K, units, 63, "export")
        es_man = f"{round(a['ES_total_krw']/10000):,}만원"
        print(f"{c.label:22} {c.spot:>10,.4g} {a['sigma_ann']*100:>5.1f}% "
              f"{a['BBP_pct']:>6.1f}% {es_man:>9} {a['forward']:>10,.4g} {a['rate_diff_pct']:>+5.2f}%")
    print("─" * 78)
    print("※ USD=실데이터, 그 외=대표 스냅샷. σ가 클수록 같은 쿠션이라도 이탈확률·기대손실↑")


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    _demo()
