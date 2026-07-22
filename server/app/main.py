"""FX Sentinel API 서버 — 판정 권위를 서버로 옮긴 계층.

데모 HTML 은 이 서버 없이도 그대로 돈다(단일 파일 이식성 보존). 이 서버는 그 위에
실서비스 요건을 얹는다: 서버 재검증 · 실인증 · 서버 RBAC · 해시체인 감사 · 제재 조회.

무엇이 연동 안 됐는지는 숨기지 않는다 — `GET /v1/integrations` 가 전부 나열한다.

기동:
    cd temp/KB/server
    set DEV_JWT_SECRET=dev-only-secret        (Windows: $env:DEV_JWT_SECRET="...")
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import alerts
from . import elig as elig_mod
from . import engine as engine_mod
from . import hedging
from . import lifecycle
from . import limits as limits_mod
from . import portfolio
from . import screening as screening_mod
from . import suitability as suit_mod
from .adapters import UnavailableAdapter, UnconnectedLedger, status_report
from .audit import get_log
from .catalog import get_catalog
from .auth import Principal, current_principal, require
from .config import settings
from .schemas import (AckRequest, AckResponse, AlertEvalRequest, AlertEvalResponse,
                      AlertOut, AssessRequest, AssessResponse, ConsumerInput, DealCreate,
                      DealOut, DealTransition, EligDecision, HedgeOp, HedgeSchedule,
                      KeyFactsRequest, KeyFactsResponse, LimitInputModel, LimitOut,
                      MarketResponse, PortfolioLeg,
                      PortfolioRequest, PortfolioResponse, ProductOut, ProductsResponse,
                      RuleCreate, RuleOut, SalesGateOut, ScreeningRequest,
                      ScreeningResponse, TicketRequest, TicketResponse)

log = logging.getLogger("fx_sentinel.api")

DISCLAIMER = ("정보 제공이며 계약 효력은 KB 영업점 확인 후 확정됩니다. "
              "환율을 예측하지 않으며, 이탈확률은 회사 기준환율·포지션 기준의 정량 리스크 번역입니다. "
              "시세는 KB 고시환율이 아닙니다.")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.auth_configured:
        log.warning("인증 미설정 — 보호된 엔드포인트가 503 을 반환합니다.")
    elif settings.dev_hs256_secret and not settings.oidc_jwks_url:
        # 이걸 조용히 두면 개발용 대칭키가 운영까지 따라간다.
        log.warning("개발용 HS256 대칭키 모드입니다. 운영 배포에는 OIDC_JWKS_URL 을 설정하십시오.")
    get_log()          # 감사 DB 초기화를 기동 시점에 끝낸다
    yield


app = FastAPI(
    title="FX Sentinel API",
    version="0.1.0",
    description="수출입 금융 AI 코파일럿 — 서버 권위 계층 (파일럿 전 단계)",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 헬스 · 경계 공개 ────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    idx = screening_mod.get_index()
    return {
        "ok": True,
        "engine_version": settings.engine_version,
        "auth_configured": settings.auth_configured,
        "sanctions_loaded": idx.loaded,
        "sanctions_entries": idx.count,
    }


@app.get("/v1/integrations")
def integrations() -> dict:
    """무엇이 진짜 붙었고 무엇이 아직인지. 인증 없이 공개한다 — 숨길 이유가 없다."""
    rows = status_report()
    return {
        "connected": [r.system for r in rows if r.connected],
        "not_connected": [
            {"system": r.system, "blocker": r.blocker, "substitute": r.substitute}
            for r in rows if not r.connected
        ],
        "note": ("미연동 항목은 가짜 값을 만들지 않고 '미확인'으로 강등해 표시합니다. "
                 "연동은 코딩 속도가 아니라 KB 계약·보안심사·망분리에 달려 있습니다."),
    }


def _consumer_profile(c, trade) -> suit_mod.ConsumerProfile:
    """ConsumerInput(선택) + 거래 폼 → 적정성 판정 입력.

    `cash`(현금 여유)는 거래 폼에 이미 있으므로 거기서 승계한다. 같은 사실을 두 번
    입력받으면 두 값이 갈라지고, 갈라지면 어느 쪽이 맞는지 아무도 모른다.
    """
    if c is None:
        # 미제출 = 확인 안 됨. 보수적 기본값이라 파생 권유가 막힌다(의도).
        return suit_mod.ConsumerProfile(biz=trade.biz, cash=trade.cash)
    return suit_mod.ConsumerProfile(
        biz=trade.biz, cash=trade.cash,
        deriv_exp=c.deriv_exp, prior_loss=c.prior_loss,
        loss_tolerance_krw=c.loss_tolerance_krw,
        understands=c.understands, pro_declared=c.pro_declared)


def _sheet_hash(sheet: dict) -> str:
    """핵심설명서 본문 해시 — 무엇을 확인했는지 고정한다."""
    import hashlib
    import json as _json
    canon = _json.dumps(sheet, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


# ── 진단 ────────────────────────────────────────────────────────────
@app.post("/v1/assess", response_model=AssessResponse)
def assess(req: AssessRequest,
           p: Principal = Depends(current_principal)) -> AssessResponse:
    """리스크 진단 + 자격 판정.

    화면이 계산해 보낸 BBP·자격 결과는 **받지도 않는다.** 입력만 받고 서버가 판정한다.
    """
    # 시장상태 주입은 RM 이상만. 고객이 sigma 를 낮춰 BBP 를 예쁘게 만드는 걸 막는다.
    if req.market is not None and not p.has("rm", "branch", "compliance", "admin"):
        raise HTTPException(status_code=403, detail="시장상태 주입은 RM 이상 권한이 필요합니다")

    result, source = engine_mod.assess(req.trade, req.market)

    facts = elig_mod.Facts(
        pos=req.trade.pos, cert=req.trade.cert, credit=req.trade.credit,
        cash=req.trade.cash, biz=req.trade.biz, settle=req.trade.settle,
        party=req.trade.party, name=req.trade.name,
    )
    decisions = elig_mod.evaluate_all(facts)
    blocks = list(elig_mod.guardrails(facts))

    # 분할 체결 일정 — 목표비율은 엔진 값을 그대로 넘긴다(여기서 재계산하지 않는다).
    schedule = hedging.build_schedule(
        amount=req.trade.amount,
        horizon_bd=req.trade.horizon,
        target_ratio=result["hedge_ratio"])

    # 여신 한도는 원장에서 확인해야 하지만 미연동이다. 고객 자기신고를 사실로 승격하지 않는다.
    try:
        UnconnectedLedger().credit_line(p.subject)
    except UnavailableAdapter as e:
        blocks.append(f"여신 한도 미확인 — {e.system} 미연동. 고객 신고값 기준이며 RM 원장 확인 필요")

    # ── 금소법 판매프로세스 (F1) ──────────────────────────────────
    # ELIG 통과분을 받아 '이 고객에게 권유해도 되는가'를 다시 본다.
    # consumer 를 안 보내면 보수적 기본값(경험 없음·이해 미확인)이라 파생이 막힌다 —
    # 정보가 없을 때 통과시키는 게 아니라 막는 쪽이 소비자보호의 기본값이다.
    m_state, _ = engine_mod.market_state(req.market)
    gate = suit_mod.sales_gate(
        eligible_keys=[d["key"] for d in decisions if d["eligible"]],
        p=_consumer_profile(req.consumer, req.trade),
        amount=req.trade.amount,
        spot=m_state.spot,
        sigma_ann=m_state.sigma_ann,
        horizon_bd=req.trade.horizon,
        credit_exec_days=(req.consumer.credit_exec_days if req.consumer else None))

    # 적정성으로 막힌 건 blocked 에도 올린다 — 화면이 sales_gate 를 안 읽어도 보이게.
    for w in gate["withheld"]:
        blocks.append(f"{w['key']} 권유 보류 — {w['reason']}")
    blocks.extend(gate["kickback_flags"])

    # ── 여신 한도 소진 (F3) ────────────────────────────────────────
    # 소진율은 명목 기준(고객 약정 방식), 여신 계상액은 CEE(은행 내부). 둘 다 낸다.
    # spot 은 CEE 를 원화로 보여주기 위해서만 쓴다(스냅샷이며 고시환율 아님).
    li = (limits_mod.LimitInput(limit_notional=req.limits.limit_notional,
                                used_notional=req.limits.used_notional)
          if req.limits else None)
    lim = limits_mod.assess_limit(li, req.trade.amount, req.trade.horizon,
                                  spot=m_state.spot)
    lim["fallback_keys"] = limits_mod.fallback_keys(lim["exceeds"], req.trade.pos)
    if lim["exceeds"]:
        blocks.append("선물환 계열 한도 초과 — " + lim["message"])

    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="assess",
        payload={
            "trade": req.trade.model_dump(),
            "market_override": req.market.model_dump() if req.market else None,
            "market_source": source,
            "output": {
                "bbp_pct": result["BBP_pct"],
                "es_total_krw": result["ES_total_krw"],
                "hedge_ratio": result["hedge_ratio"],
                "instrument": result["instrument"],
            },
            "eligibility": decisions,
            "blocked": blocks,
            # 판매프로세스 판정도 감사 대상이다 — "왜 그때 권유했나/왜 막았나"에 답해야 한다.
            "limit": {"known": lim["known"], "cee_krw": lim["cee_krw"],
                      "cee_notional": lim["cee_notional"],
                      "util_after": lim["util_after"], "exceeds": lim["exceeds"],
                      "status": lim["status"]},
            "sales_gate": {
                "consumer_type": gate["consumer_type"],
                "suitability_met": gate["suitability"]["met"],
                "suitability_verdict": gate["suitability"]["verdict"],
                "suitability_exempt": gate["suitability_exempt"],
                "advisable_keys": gate["advisable_keys"],
                "withheld": [w["key"] for w in gate["withheld"]],
                "kickback_flags": gate["kickback_flags"],
            },
        },
        engine_ver=settings.engine_version)

    return AssessResponse(
        bbp_pct=result["BBP_pct"],
        es_per_unit=result["ES_per_usd"],
        es_total_krw=result["ES_total_krw"],
        loss_alert=result["LossAlert"],
        hedge_ratio=result["hedge_ratio"],
        hedge_amount=result["hedge_amount_usd"],
        gauge_grade=result["gauge_grade"],
        hedge_schedule=HedgeSchedule(**schedule),
        instrument=result["instrument"],
        rationale=result["rationale"],
        eligibility=[EligDecision(**d) for d in decisions],
        blocked=blocks,
        sales_gate=SalesGateOut(**gate),
        limit=LimitOut(**lim),
        engine_version=settings.engine_version,
        market_asof=settings.snapshot_date,
        market_source=source,
        audit_id=audit_id,
        disclaimer=DISCLAIMER,
    )


# ── 설명의무 (F1) ───────────────────────────────────────────────────
@app.post("/v1/keyfacts", response_model=KeyFactsResponse)
def keyfacts(req: KeyFactsRequest,
             p: Principal = Depends(current_principal)) -> KeyFactsResponse:
    """핵심설명서 생성 — 상품별 위험 문안 + 불리 시나리오.

    이 문서가 '설명 못 들었다'는 주장에 은행이 내놓을 유일한 물증이다. 그래서 본문을
    해시로 고정해 돌려주고, 이해확인(/v1/keyfacts/ack)이 그 해시를 되보낸다.
    """
    m_state, _ = engine_mod.market_state(None)
    profile = _consumer_profile(req.consumer, req.trade)
    ctype, _reason = suit_mod.consumer_type(profile)

    scenario = suit_mod.loss_scenarios(
        req.instrument, req.trade.amount, m_state.spot,
        m_state.sigma_ann, req.trade.horizon, req.band_pct)
    sheet = suit_mod.key_facts_sheet(req.instrument, scenario, ctype)
    h = _sheet_hash(sheet)

    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="keyfacts.issue",
        payload={"instrument": req.instrument, "consumer_type": ctype,
                 "sheet_hash": h, "scenario": scenario,
                 "trade_ref": {"name": req.trade.name, "amount": req.trade.amount,
                               "horizon": req.trade.horizon}},
        engine_ver=settings.engine_version)

    return KeyFactsResponse(
        instrument=req.instrument, consumer_type=ctype, risks=sheet["risks"],
        scenario=scenario, scenario_summary=sheet["scenario_summary"],
        margin_call=sheet["margin_call"], explain_duty=sheet["explain_duty"],
        sheet_hash=h, note=sheet["note"], audit_id=audit_id)


@app.post("/v1/keyfacts/ack", response_model=AckResponse)
def keyfacts_ack(req: AckRequest,
                 p: Principal = Depends(current_principal)) -> AckResponse:
    """고객 이해확인 기록.

    '확인함' 만 남기지 않고 **어떤 문서를** 확인했는지(sheet_hash) 함께 남긴다.
    문안이 나중에 바뀌어도 그때 그 문서를 특정할 수 있어야 하기 때문이다.
    """
    if not req.understood:
        # 미이해를 조용히 넘기지 않는다 — 그것도 사실이므로 기록한다.
        audit_id = get_log().append(
            actor=p.subject, role=p.role, event="keyfacts.ack.declined",
            payload={"instrument": req.instrument, "sheet_hash": req.sheet_hash,
                     "customer": req.customer_name},
            engine_ver=settings.engine_version)
        raise HTTPException(
            status_code=409,
            detail=("이해확인이 되지 않았습니다 — 설명의무 미이행 상태로는 진행할 수 없습니다. "
                    f"(기록됨: {audit_id})"))

    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="keyfacts.ack",
        payload={"instrument": req.instrument, "sheet_hash": req.sheet_hash,
                 "customer": req.customer_name, "understood": True},
        engine_ver=settings.engine_version)

    return AckResponse(
        recorded=True, instrument=req.instrument, sheet_hash=req.sheet_hash,
        recorded_at=_now(), audit_id=audit_id,
        note=("설명의무 이행 기록이 감사로그에 고정됐습니다. 계약 체결이 아니며, "
              "최종 조건·계약은 KB 영업점에서 확정됩니다."))


# ── 시세 ────────────────────────────────────────────────────────────
@app.get("/v1/market", response_model=MarketResponse)
def market(p: Principal = Depends(current_principal)) -> MarketResponse:
    m, source = engine_mod.market_state(None)
    return MarketResponse(
        asof=m.date, spot=m.spot, sigma_ann=m.sigma_ann, fx_ewi=m.fx_ewi, iz=m.iz,
        source=source, is_kb_official=False,
        note="KB 고시환율 어댑터 미연동 — 기준일 스냅샷입니다. 거래 체결 기준으로 쓸 수 없습니다.")


# ── 티켓 ────────────────────────────────────────────────────────────
@app.post("/v1/tickets", response_model=TicketResponse)
def create_ticket(req: TicketRequest,
                  p: Principal = Depends(current_principal)) -> TicketResponse:
    """RM 상담 티켓 생성.

    RM 업무함/CRM 은 미연동이므로 티켓은 이 서버에만 남는다. 그 사실을 응답에 적는다 —
    '접수됐다'고만 하면 고객은 RM 이 봤다고 믿는다.
    """
    facts = elig_mod.Facts(
        pos=req.trade.pos, cert=req.trade.cert, credit=req.trade.credit,
        cash=req.trade.cash, biz=req.trade.biz, settle=req.trade.settle,
        party=req.trade.party, name=req.trade.name)
    blocks = elig_mod.guardrails(facts)

    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="ticket.create",
        payload={"trade": req.trade.model_dump(), "note": req.note,
                 "assess_audit_id": req.assess_audit_id, "blocked": blocks},
        engine_ver=settings.engine_version)

    return TicketResponse(
        ticket_id="TK-" + audit_id[:12].upper(),
        status="접수(서버 보관)" if not blocks else "접수 보류 — 실수요 확정 필요",
        created_at=_now(),
        audit_id=audit_id,
        delivery="RM 업무함 미연동 — 이 티켓은 서버에만 저장되며 실제 RM 에게 전달되지 않습니다",
    )


# ── 포트폴리오 ──────────────────────────────────────────────────────
@app.post("/v1/portfolio/assess", response_model=PortfolioResponse)
def portfolio_assess(req: PortfolioRequest,
                     p: Principal = Depends(current_principal)) -> PortfolioResponse:
    """다건 거래 합산 — 자연헤지(네팅)·만기 캘린더·가중 BBP.

    거래 한 건만 보면 같은 달에 USD 를 받고 또 지급하는 기업에게 양쪽 모두 헤지하라고
    말하게 된다. 상계는 **같은 통화·같은 만기 구간** 안에서만 인정한다.
    """
    if req.market is not None and not p.has("rm", "branch", "compliance", "admin"):
        raise HTTPException(status_code=403, detail="시장상태 주입은 RM 이상 권한이 필요합니다")

    legs_out, legs_in = [], []
    source = ""
    for i, t in enumerate(req.trades):
        result, source = engine_mod.assess(t, req.market)
        ref = f"L{i + 1}"
        legs_in.append(portfolio.Leg(
            ref=ref, pos=t.pos, currency=t.currency, amount=t.amount,
            horizon_bd=t.horizon, bbp=result["BBP"],
            es_total_krw=result["ES_total_krw"]))
        legs_out.append(PortfolioLeg(
            ref=ref, name=t.name or "-", pos=t.pos, currency=t.currency,
            amount=t.amount, horizon_bd=t.horizon,
            bucket=portfolio.bucket_of(t.horizon),
            bbp_pct=result["BBP_pct"], es_total_krw=result["ES_total_krw"],
            hedge_ratio=result["hedge_ratio"]))

    agg = portfolio.aggregate(legs_in)

    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="portfolio.assess",
        payload={"trade_count": len(req.trades),
                 "weighted_bbp_pct": agg["weighted_bbp_pct"],
                 "total_es_krw": agg["total_es_krw"],
                 "gross_exposure": agg["gross_exposure"],
                 "market_source": source},
        engine_ver=settings.engine_version)

    return PortfolioResponse(
        legs=legs_out,
        trade_count=agg["trade_count"],
        weighted_bbp_pct=agg["weighted_bbp_pct"],
        total_es_krw=agg["total_es_krw"],
        by_currency=agg["by_currency"],
        maturity_calendar=agg["maturity_calendar"],
        gross_exposure=agg["gross_exposure"],
        net_after_bucket_netting=agg["net_after_bucket_netting"],
        offset_within_bucket=agg["offset_within_bucket"],
        timing_mismatch=agg["timing_mismatch"],
        notes=agg["notes"],
        market_asof=settings.snapshot_date,
        market_source=source,
        audit_id=audit_id,
        disclaimer=DISCLAIMER,
    )


# ── 알림 구독 ───────────────────────────────────────────────────────
def _rule_out(r) -> RuleOut:
    return RuleOut(rule_id=r.rule_id, kind=r.kind, days_before=r.days_before,
                   threshold_pct=r.threshold_pct, min_grade=r.min_grade,
                   min_hedge_ratio=r.min_hedge_ratio, cooldown_days=r.cooldown_days,
                   enabled=r.enabled, created_at=r.created_at)


@app.post("/v1/alerts/rules", response_model=RuleOut)
def create_rule(req: RuleCreate,
                p: Principal = Depends(current_principal)) -> RuleOut:
    try:
        r = alerts.get_store().add(
            owner=p.subject, kind=req.kind, days_before=req.days_before,
            threshold_pct=req.threshold_pct, min_grade=req.min_grade,
            min_hedge_ratio=req.min_hedge_ratio, cooldown_days=req.cooldown_days)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    get_log().append(actor=p.subject, role=p.role, event="alert.rule.create",
                     payload={"rule_id": r.rule_id, "kind": r.kind},
                     engine_ver=settings.engine_version)
    return _rule_out(r)


@app.get("/v1/alerts/rules")
def list_rules(p: Principal = Depends(current_principal)) -> dict:
    return {"rules": [_rule_out(r).model_dump() for r in alerts.get_store().list(p.subject)]}


@app.delete("/v1/alerts/rules/{rule_id}")
def delete_rule(rule_id: str, p: Principal = Depends(current_principal)) -> dict:
    ok = alerts.get_store().delete(rule_id, p.subject)
    if not ok:
        raise HTTPException(status_code=404, detail="규칙을 찾을 수 없습니다")
    return {"deleted": rule_id}


@app.post("/v1/alerts/evaluate", response_model=AlertEvalResponse)
def evaluate_alerts(req: AlertEvalRequest,
                    p: Principal = Depends(current_principal)) -> AlertEvalResponse:
    """구독 규칙을 현재 거래 상태에 대해 평가.

    실제 발송은 하지 않는다 — 발송 채널이 미연동이다. 그 사실을 응답에 적는다.
    """
    rules = alerts.get_store().list(p.subject)
    signals = []
    for i, t in enumerate(req.trades):
        result, _ = engine_mod.assess(t, None)
        signals.append(alerts.Signal(
            ref=f"L{i + 1}", horizon_bd=t.horizon, bbp_pct=result["BBP_pct"],
            hedge_ratio=result["hedge_ratio"], gauge_grade=result["gauge_grade"],
            name=t.name))

    fired = alerts.evaluate(rules, signals)
    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="alert.evaluate",
        payload={"rules": len(rules), "trades": len(signals), "fired": len(fired)},
        engine_ver=settings.engine_version)

    return AlertEvalResponse(
        alerts=[AlertOut(**a.__dict__) for a in fired],
        evaluated_rules=len(rules), evaluated_trades=len(signals),
        delivery_note=alerts.DELIVERY_NOTE, audit_id=audit_id)


# ── 거래 라이프사이클 ───────────────────────────────────────────────
def _deal_out(d, warnings=None) -> DealOut:
    return DealOut(
        deal_id=d.deal_id, state=d.state,
        real_demand_amount=d.real_demand_amount, hedged_amount=d.hedged_amount,
        unhedged_amount=d.unhedged_amount,
        allowed_transitions=list(lifecycle.TRANSITIONS.get(d.state, ())),
        history=list(d.history), warnings=list(warnings or []))


@app.post("/v1/deals", response_model=DealOut)
def create_deal(req: DealCreate,
                p: Principal = Depends(current_principal)) -> DealOut:
    import uuid as _uuid
    did = req.deal_ref or ("D-" + _uuid.uuid4().hex[:10].upper())
    if lifecycle.get_store().get(did):
        raise HTTPException(status_code=409, detail="이미 존재하는 거래 ID 입니다")
    d = lifecycle.get_store().create(did, p.subject, req.real_demand_amount)
    get_log().append(actor=p.subject, role=p.role, event="deal.create",
                     payload={"deal_id": did, "real_demand": req.real_demand_amount},
                     engine_ver=settings.engine_version)
    return _deal_out(d)


def _own_deal(deal_id: str, p: Principal):
    d = lifecycle.get_store().get(deal_id)
    if not d:
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다")
    # 고객은 자기 건만. RM 이상은 조회·처리 가능.
    if d.owner != p.subject and not p.has("rm", "branch", "compliance", "admin"):
        raise HTTPException(status_code=403, detail="다른 고객의 거래입니다")
    return d


@app.get("/v1/deals/{deal_id}", response_model=DealOut)
def get_deal(deal_id: str, p: Principal = Depends(current_principal)) -> DealOut:
    return _deal_out(_own_deal(deal_id, p))


@app.post("/v1/deals/{deal_id}/transition", response_model=DealOut)
def transition_deal(deal_id: str, req: DealTransition,
                    p: Principal = Depends(current_principal)) -> DealOut:
    d = _own_deal(deal_id, p)
    try:
        d.transition(req.to, actor=p.subject, role=p.role, note=req.note)
    except lifecycle.TransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    get_log().append(actor=p.subject, role=p.role, event="deal.transition",
                     payload={"deal_id": deal_id, "to": req.to, "note": req.note},
                     engine_ver=settings.engine_version)
    return _deal_out(d)


@app.post("/v1/deals/{deal_id}/hedge", response_model=DealOut)
def contract_hedge(deal_id: str, req: HedgeOp,
                   p: Principal = Depends(require("rm", "branch", "admin"))) -> DealOut:
    d = _own_deal(deal_id, p)
    try:
        d.contract_hedge(req.amount, actor=p.subject, role=p.role)
    except lifecycle.TransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    get_log().append(actor=p.subject, role=p.role, event="deal.hedge.contract",
                     payload={"deal_id": deal_id, "amount": req.amount},
                     engine_ver=settings.engine_version)
    return _deal_out(d)


@app.post("/v1/deals/{deal_id}/unwind", response_model=DealOut)
def unwind_hedge(deal_id: str, req: HedgeOp,
                 p: Principal = Depends(require("rm", "branch", "admin"))) -> DealOut:
    """부분해지. 보유 헤지를 초과하면 실수요 없는 반대 포지션이 되므로 거부한다."""
    d = _own_deal(deal_id, p)
    try:
        d.unwind_hedge(req.amount, actor=p.subject, role=p.role)
    except lifecycle.TransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    get_log().append(actor=p.subject, role=p.role, event="deal.hedge.unwind",
                     payload={"deal_id": deal_id, "amount": req.amount},
                     engine_ver=settings.engine_version)
    return _deal_out(d)


@app.post("/v1/deals/{deal_id}/reduce", response_model=DealOut)
def reduce_real_demand(deal_id: str, req: HedgeOp,
                       p: Principal = Depends(require("rm", "branch", "admin"))) -> DealOut:
    """원 거래 감액(부분 취소). 헤지가 남으면 초과분 해지 필요를 경고한다."""
    d = _own_deal(deal_id, p)
    try:
        warns = d.reduce_real_demand(req.amount, actor=p.subject, role=p.role)
    except lifecycle.TransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    get_log().append(actor=p.subject, role=p.role, event="deal.real_demand.reduce",
                     payload={"deal_id": deal_id, "to": req.amount, "warnings": warns},
                     engine_ver=settings.engine_version)
    return _deal_out(d, warns)


# ── 상품 마스터 ─────────────────────────────────────────────────────
@app.get("/v1/products", response_model=ProductsResponse)
def products(category: str = "", q: str = "",
             pos: str = "", cert: str = "", credit: str = "",
             cash: str = "", biz: str = "", settle: str = "",
             p: Principal = Depends(current_principal)) -> ProductsResponse:
    """상품 카탈로그.

    거래 조건(pos/cert/credit/cash/biz/settle)을 함께 주면 자격 판정을 붙여 돌려준다.
    판정은 **elig.py 단일 소스**에 위임한다 — 카탈로그가 자격을 다시 쓰면 규칙이 갈라진다.
    """
    cat = get_catalog()
    items = cat.filter(category=category, query=q)

    facts = None
    if pos or cert or credit or cash or biz or settle:
        facts = elig_mod.Facts(
            pos=pos or "export", cert=cert or "confirmed",
            credit=credit or "yes", cash=cash or "ok", biz=biz or "corp",
            settle=settle or "fixed")

    out = []
    for it in items:
        row = ProductOut(
            code=it.code, category=it.category, name=it.name, source=it.source,
            description=it.description, requirement=it.requirement,
            documents=it.documents, channels=it.channels, kb_path=it.kb_path,
            reference=it.reference, eligibility_key=it.eligibility_key,
            rm_checks=list(it.rm_checks), not_a_hedge=it.not_a_hedge)
        if facts is not None and it.eligibility_key:
            ok = elig_mod.eligible(it.eligibility_key, facts)
            row.eligible = ok
            row.ineligible_reason = "" if ok else elig_mod.deny_reason(it.eligibility_key, facts)
        out.append(row)

    return ProductsResponse(
        catalog_version=cat.version,
        rate_policy=cat.rate_policy,
        categories=list(cat.categories),
        products=out,
        note=("요율·한도는 제공하지 않습니다 — KB 상품/약관 DB 미연동. "
              "상품 후보 제시까지이며 조건은 RM 견적으로 확정됩니다."),
    )


# ── 제재 조회 ───────────────────────────────────────────────────────
@app.post("/v1/screening", response_model=ScreeningResponse)
def screening(req: ScreeningRequest,
              p: Principal = Depends(require("rm", "branch", "compliance", "admin"))
              ) -> ScreeningResponse:
    """제재 스크리닝. 고객에게는 열지 않는다 — 오탐 노출이 명예훼손이 될 수 있다."""
    idx = screening_mod.get_index()
    hits = idx.screen(req.party)
    audit_id = get_log().append(
        actor=p.subject, role=p.role, event="screening",
        payload={"party": req.party, "country": req.country,
                 "hit_count": len(hits),
                 "top_score": hits[0].score if hits else None,
                 "lists_version": idx.versions},
        engine_ver=settings.engine_version)
    return ScreeningResponse(
        query=req.party,
        hits=[{"list_name": h.list_name, "matched_name": h.matched_name,
               "score": h.score, "entity_id": h.entity_id,
               "programs": h.programs or []} for h in hits],
        lists_version=idx.versions,
        checked_at=_now(),
        verdict=screening_mod.verdict_for(hits, idx.loaded),
        audit_id=audit_id,
    )


# ── 감사로그 ────────────────────────────────────────────────────────
@app.get("/v1/audit/verify")
def audit_verify(p: Principal = Depends(require("compliance", "admin"))) -> dict:
    """해시체인 전수 재계산. 중간이 바뀌었으면 어디서 깨졌는지 알려준다."""
    return get_log().verify()


@app.get("/v1/audit")
def audit_tail(limit: int = 50,
               p: Principal = Depends(require("compliance", "admin"))) -> dict:
    return {"records": get_log().tail(limit=min(limit, 500))}


@app.get("/v1/audit/mine")
def audit_mine(limit: int = 50,
               p: Principal = Depends(current_principal)) -> dict:
    """자기 기록만. 고객이 남의 감사로그를 보면 안 된다."""
    return {"records": get_log().tail(limit=min(limit, 200), actor=p.subject)}
