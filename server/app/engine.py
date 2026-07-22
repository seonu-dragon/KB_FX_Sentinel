"""리스크 엔진 어댑터 — fx_sentinel.bbp 를 **감싸기만** 한다.

BBP 수식은 이미 두 곳(fx_sentinel/bbp.py, 데모 HTML 의 JS)에 있다. 세 번째를 만들면
drift 가 3방향으로 늘어난다. 그래서 이 파일에는 수식이 없다 — 매핑과 호출뿐이다.

`server/tests/test_bbp_golden.py` 가 Python 엔진과 데모 JS 의 BBP 를 같은 입력으로
돌려 대조한다(골든벡터). 둘이 갈라지면 테스트가 죽는다.
"""
from __future__ import annotations

from typing import Optional

# config 를 **먼저** import 해야 한다 — sys.path 에 KB 루트를 얹는 부트스트랩이 거기 있고,
# 그게 돌기 전에는 fx_sentinel 을 찾을 수 없다. (import 순서가 곧 의존성이다.)
from .config import settings
from .schemas import MarketOverride, TradeInput

from fx_sentinel import bbp as engine  # noqa: E402  (config 부트스트랩 이후여야 함)


def market_state(override: Optional[MarketOverride] = None) -> tuple[engine.MarketState, str]:
    """시장상태 조달. 반환값 두 번째는 출처 문자열(감사로그·응답에 박힌다).

    KB 고시환율 어댑터는 미연동이므로(adapters 참조) 기준일 스냅샷을 쓴다.
    이걸 '현재 시세'나 '고시환율'이라고 부르지 않는 것이 중요하다.
    """
    m = engine.MarketState(
        date=settings.snapshot_date,
        spot=settings.snapshot_spot,
        sigma_ann=settings.snapshot_sigma_ann,
        fx_ewi=settings.snapshot_fx_ewi,
        iz=settings.snapshot_iz,
    )
    source = "기준일 스냅샷 (KB 고시환율 아님)"

    if override is not None:
        touched = []
        if override.spot is not None:
            m.spot = override.spot; touched.append("spot")
        if override.sigma_ann is not None:
            m.sigma_ann = override.sigma_ann; touched.append("sigma_ann")
        if override.fx_ewi is not None:
            m.fx_ewi = override.fx_ewi; touched.append("fx_ewi")
        if override.iz is not None:
            m.iz = override.iz; touched.append("iz")
        if touched:
            # 주입 사실을 숨기지 않는다 — 검증용 수치가 실측으로 오해되면 안 된다.
            source = "RM 주입 시장상태 (" + ",".join(touched) + ") · 실측 아님"
    return m, source


def to_profile(t: TradeInput) -> engine.CompanyProfile:
    return engine.CompanyProfile(
        name=t.name or "미입력",
        position=t.pos,
        budget_rate=t.budget_rate,
        amount_usd=t.amount,
        horizon_days=t.horizon,
        certainty=t.cert,
        has_credit_line=(t.credit == "yes"),
        cash_constrained=(t.cash == "tight"),
        sector="-",
        currency=t.currency,
    )


def assess(t: TradeInput, override: Optional[MarketOverride] = None) -> tuple[dict, str]:
    """엔진 진단 실행. (결과 dict, 시장상태 출처) 반환."""
    m, source = market_state(override)
    result = engine.assess(to_profile(t), m, nu=settings.nu)
    return result, source
