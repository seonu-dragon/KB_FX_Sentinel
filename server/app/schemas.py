"""API 스키마 (pydantic v2).

필드명은 데모 HTML 의 폼 필드(f.pos, f.credit, f.cert ...)와 **의도적으로 같게** 맞췄다.
이름이 갈라지면 매핑 코드가 생기고, 매핑 코드는 반드시 어딘가에서 틀린다.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Position = Literal["export", "import"]
Certainty = Literal["confirmed", "provisional"]
Credit = Literal["yes", "no"]
Cash = Literal["ok", "tight"]
BizType = Literal["corp", "sole"]
SettleTiming = Literal["fixed", "window"]


class TradeInput(BaseModel):
    """고객 화면이 보내는 거래 정보. 서버는 이것만 믿고, 판정 결과는 믿지 않는다."""

    name: str = Field(default="", max_length=120, description="기업명")
    party: str = Field(default="", max_length=200, description="거래상대방")
    pos: Position = "export"
    cert: Certainty = "confirmed"
    credit: Credit = "yes"
    cash: Cash = "ok"
    biz: BizType = "corp"
    settle: SettleTiming = "fixed"

    budget_rate: float = Field(gt=0, le=100_000, description="회사 기준환율 K (원)")
    amount: float = Field(gt=0, le=1_000_000_000, description="결제 금액")
    horizon: int = Field(gt=0, le=3 * 252, description="결제 만기까지 영업일")
    currency: str = Field(default="USD", max_length=3)
    country: str = Field(default="", max_length=60)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        v = (v or "USD").upper()
        # 다통화는 실측 개방된 것만 허용. CNY 는 소스 오염(100배)으로 데모에서 차단됐고,
        # 서버도 같은 판단을 유지한다 — 화면만 막고 API 가 열려 있으면 막은 게 아니다.
        if v not in ("USD", "EUR", "JPY"):
            raise ValueError("지원 통화는 USD·EUR·JPY 입니다 (CNY 는 시세 소스 검증 전까지 차단)")
        return v


class MarketOverride(BaseModel):
    """RM/검증용 시장상태 주입. 고객 역할에는 허용하지 않는다(auth 에서 차단)."""

    spot: Optional[float] = Field(default=None, gt=0)
    sigma_ann: Optional[float] = Field(default=None, gt=0, le=5)
    fx_ewi: Optional[float] = Field(default=None, ge=0, le=100)
    iz: Optional[int] = Field(default=None, ge=0, le=1)


class AssessRequest(BaseModel):
    trade: TradeInput
    market: Optional[MarketOverride] = None


class EligDecision(BaseModel):
    key: str
    eligible: bool
    reason: str = ""


class TrancheOut(BaseModel):
    seq: int
    ratio: float
    amount: float
    execute_at_bd: int
    remaining_bd: int
    cumulative_ratio: float


class HedgeSchedule(BaseModel):
    """분할 체결 일정. 목표비율은 엔진이 정한 값이며 여기서 바꾸지 않는다."""
    target_ratio: float
    step: float
    tranches: list[TrancheOut] = Field(default_factory=list)
    unhedged_ratio: float
    note: str


class AssessResponse(BaseModel):
    # 리스크 번역 (엔진 산출)
    bbp_pct: float
    es_per_unit: float
    es_total_krw: int
    loss_alert: float
    hedge_ratio: float
    hedge_amount: float
    gauge_grade: str
    hedge_schedule: HedgeSchedule

    # 라우팅 (서버 판정)
    instrument: str
    rationale: str
    eligibility: list[EligDecision]
    blocked: list[str] = Field(default_factory=list)

    # 추적성
    engine_version: str
    market_asof: str
    market_source: str
    audit_id: str

    # 정직성 고지 — 응답에 박아 둔다. 화면이 빼먹어도 API 소비자는 본다.
    disclaimer: str


class TicketRequest(BaseModel):
    trade: TradeInput
    note: str = Field(default="", max_length=2000)
    assess_audit_id: str = Field(default="", max_length=64,
                                 description="이 티켓의 근거가 된 /v1/assess 감사 ID")


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    created_at: str
    audit_id: str
    # RM 업무함 실연동 전이므로, 티켓은 이 서버에만 존재한다. 숨기지 않고 밝힌다.
    delivery: str


class MarketResponse(BaseModel):
    asof: str
    spot: float
    sigma_ann: float
    fx_ewi: float
    iz: int
    source: str
    is_kb_official: bool
    note: str


class ProductOut(BaseModel):
    code: str
    category: str
    name: str
    source: str
    description: str
    requirement: str
    documents: str
    channels: str
    kb_path: str
    reference: str = ""
    eligibility_key: str = ""
    rm_checks: list[str] = Field(default_factory=list)
    not_a_hedge: bool = False
    # 자격 판정 결과(요청에 거래정보가 있을 때만 채워진다)
    eligible: Optional[bool] = None
    ineligible_reason: str = ""


class ProductsResponse(BaseModel):
    catalog_version: str
    rate_policy: str
    categories: list[str]
    products: list[ProductOut]
    note: str


class PortfolioRequest(BaseModel):
    trades: list[TradeInput] = Field(min_length=1, max_length=200)
    market: Optional[MarketOverride] = None


class PortfolioLeg(BaseModel):
    ref: str
    name: str
    pos: Position
    currency: str
    amount: float
    horizon_bd: int
    bucket: str
    bbp_pct: float
    es_total_krw: int
    hedge_ratio: float


class PortfolioResponse(BaseModel):
    legs: list[PortfolioLeg]
    trade_count: int
    weighted_bbp_pct: float
    total_es_krw: int
    by_currency: list[dict]
    maturity_calendar: list[dict]
    gross_exposure: dict
    net_after_bucket_netting: dict
    offset_within_bucket: dict
    timing_mismatch: dict
    notes: list[str]
    market_asof: str
    market_source: str
    audit_id: str
    disclaimer: str


class RuleCreate(BaseModel):
    kind: Literal["maturity", "bbp_above", "regime", "unhedged"]
    days_before: Optional[int] = Field(default=None, ge=0, le=250)
    threshold_pct: Optional[float] = Field(default=None, ge=0, le=100)
    min_grade: Optional[Literal["정상", "주의", "경계", "심각"]] = None
    min_hedge_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    cooldown_days: Optional[int] = Field(default=None, ge=0, le=90)


class RuleOut(BaseModel):
    rule_id: str
    kind: str
    days_before: int
    threshold_pct: float
    min_grade: str
    min_hedge_ratio: float
    cooldown_days: int
    enabled: bool
    created_at: str


class AlertOut(BaseModel):
    alert_id: str
    rule_id: str
    kind: str
    ref: str
    message: str
    fired_at: str
    delivery: str


class AlertEvalRequest(BaseModel):
    trades: list[TradeInput] = Field(min_length=1, max_length=200)


class AlertEvalResponse(BaseModel):
    alerts: list[AlertOut]
    evaluated_rules: int
    evaluated_trades: int
    delivery_note: str
    audit_id: str


class DealCreate(BaseModel):
    real_demand_amount: float = Field(gt=0)
    deal_ref: str = Field(default="", max_length=64)


class DealTransition(BaseModel):
    to: str
    note: str = Field(default="", max_length=500)


class HedgeOp(BaseModel):
    amount: float = Field(gt=0)


class DealOut(BaseModel):
    deal_id: str
    state: str
    real_demand_amount: float
    hedged_amount: float
    unhedged_amount: float
    allowed_transitions: list[str]
    history: list[dict]
    warnings: list[str] = Field(default_factory=list)


class ScreeningRequest(BaseModel):
    party: str = Field(min_length=1, max_length=200)
    country: str = Field(default="", max_length=60)


class ScreeningHit(BaseModel):
    list_name: str
    matched_name: str
    score: float
    entity_id: str = ""
    programs: list[str] = Field(default_factory=list)


class ScreeningResponse(BaseModel):
    query: str
    hits: list[ScreeningHit]
    lists_version: dict[str, str]
    checked_at: str
    # '정상' 이라고 단정하지 않는다 — 리스트 미탐이 결백의 증명이 아니다.
    verdict: str
    audit_id: str
