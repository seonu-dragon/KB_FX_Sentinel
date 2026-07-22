"""API · 인증 · RBAC · 감사체인 테스트."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 설정은 import 시점에 굳으므로 앱 import 전에 환경을 잡는다.
_TMP = tempfile.mkdtemp(prefix="fxs_test_")
os.environ["DEV_JWT_SECRET"] = "test-only-secret"
os.environ["AUDIT_DB"] = os.path.join(_TMP, "audit.sqlite3")
os.environ["SANCTIONS_DIR"] = os.path.join(_TMP, "sanctions")

from app.auth import issue_dev_token        # noqa: E402
from app.main import app                    # noqa: E402
from tests.asgi_client import ASGIClient    # noqa: E402

client = ASGIClient(app)

TRADE = {
    "name": "한빛정밀", "party": "Tokyo Precision KK", "pos": "export",
    "cert": "confirmed", "credit": "yes", "cash": "ok", "biz": "corp",
    "budget_rate": 1450, "amount": 500000, "horizon": 63, "currency": "USD",
    "country": "일본",
}


def hdr(role: str, sub: str = "u1") -> dict:
    return {"Authorization": "Bearer " + issue_dev_token(sub, role)}


# ── 인증 ────────────────────────────────────────────────────────────
def test_assess_requires_token():
    r = client.post("/v1/assess", json={"trade": TRADE})
    assert r.status_code == 401


def test_assess_rejects_garbage_token():
    r = client.post("/v1/assess", json={"trade": TRADE},
                    headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_assess_rejects_token_signed_with_wrong_key():
    """passcode 1234 와의 결정적 차이 — 서명이 틀리면 통과할 수 없다."""
    import jwt as pyjwt
    import time
    bad = pyjwt.encode({"sub": "attacker", "role": "admin", "aud": "fx-sentinel",
                        "exp": int(time.time()) + 600},
                       "wrong-secret", algorithm="HS256")
    r = client.post("/v1/assess", json={"trade": TRADE},
                    headers={"Authorization": "Bearer " + bad})
    assert r.status_code == 401


def test_unknown_role_downgrades_to_customer():
    """fail-closed: 모르는 역할은 최저 권한으로 떨어진다."""
    import jwt as pyjwt
    import time
    tok = pyjwt.encode({"sub": "x", "role": "superuser", "aud": "fx-sentinel",
                        "exp": int(time.time()) + 600},
                       os.environ["DEV_JWT_SECRET"], algorithm="HS256")
    r = client.post("/v1/screening", json={"party": "ACME"},
                    headers={"Authorization": "Bearer " + tok})
    assert r.status_code == 403


# ── 진단 ────────────────────────────────────────────────────────────
def test_assess_returns_engine_numbers():
    r = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert 0 <= d["bbp_pct"] <= 100
    assert d["es_total_krw"] >= 0
    assert d["hedge_ratio"] in (0.0, 0.5, 1.0)
    assert d["audit_id"]
    assert "KB 고시환율이 아" in d["disclaimer"] or "고시환율이 아닙니다" in d["disclaimer"]


def test_assess_is_deterministic():
    """같은 입력이면 같은 숫자. ES 몬테카를로가 seed 고정이라 성립한다."""
    a = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    b = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    assert a["bbp_pct"] == b["bbp_pct"]
    assert a["es_total_krw"] == b["es_total_krw"]


def test_customer_cannot_inject_market_state():
    """고객이 sigma 를 낮춰 BBP 를 예쁘게 만드는 경로를 서버가 막는다."""
    r = client.post("/v1/assess",
                    json={"trade": TRADE, "market": {"sigma_ann": 0.001}},
                    headers=hdr("customer"))
    assert r.status_code == 403


def test_rm_can_inject_and_it_is_labeled():
    r = client.post("/v1/assess",
                    json={"trade": TRADE, "market": {"sigma_ann": 0.30}},
                    headers=hdr("rm"))
    assert r.status_code == 200
    assert "실측 아님" in r.json()["market_source"]


def test_provisional_trade_is_blocked():
    """가결제 건은 선물환 자격이 없고 가드레일이 걸린다."""
    t = dict(TRADE, cert="provisional")
    d = client.post("/v1/assess", json={"trade": t}, headers=hdr("customer")).json()
    fwd = [e for e in d["eligibility"] if e["key"] == "선물환"][0]
    assert fwd["eligible"] is False
    assert any("실수요" in b for b in d["blocked"])


def test_credit_line_is_flagged_unverified():
    """원장 미연동이므로 고객 신고 여신을 사실로 승격하지 않는다."""
    d = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    assert any("여신 한도 미확인" in b for b in d["blocked"])


def test_cny_is_rejected():
    """화면만 막고 API 가 열려 있으면 막은 게 아니다."""
    t = dict(TRADE, currency="CNY")
    r = client.post("/v1/assess", json={"trade": t}, headers=hdr("customer"))
    assert r.status_code == 422


def test_negative_amount_rejected():
    t = dict(TRADE, amount=-1)
    r = client.post("/v1/assess", json={"trade": t}, headers=hdr("customer"))
    assert r.status_code == 422


# ── 시세 ────────────────────────────────────────────────────────────
def test_market_never_claims_official():
    d = client.get("/v1/market", headers=hdr("customer")).json()
    assert d["is_kb_official"] is False
    assert "고시환율" in d["note"]


# ── 티켓 ────────────────────────────────────────────────────────────
def test_ticket_admits_it_is_not_delivered():
    r = client.post("/v1/tickets", json={"trade": TRADE, "note": "상담 요청"},
                    headers=hdr("customer"))
    assert r.status_code == 200
    assert "전달되지 않습니다" in r.json()["delivery"]


# ── 포트폴리오 ──────────────────────────────────────────────────────
def test_portfolio_nets_same_bucket():
    exp = dict(TRADE, pos="export", amount=500_000, horizon=63)
    imp = dict(TRADE, pos="import", amount=300_000, horizon=63)
    d = client.post("/v1/portfolio/assess", json={"trades": [exp, imp]},
                    headers=hdr("customer")).json()
    usd = [c for c in d["by_currency"] if c["currency"] == "USD"][0]
    assert usd["offset_within_bucket"] == 300_000
    assert usd["net_exposure"] == 200_000


def test_portfolio_exposes_timing_mismatch():
    exp = dict(TRADE, pos="export", amount=500_000, horizon=10)
    imp = dict(TRADE, pos="import", amount=500_000, horizon=126)
    d = client.post("/v1/portfolio/assess", json={"trades": [exp, imp]},
                    headers=hdr("customer")).json()
    usd = [c for c in d["by_currency"] if c["currency"] == "USD"][0]
    assert usd["offset_within_bucket"] == 0
    assert usd["timing_mismatch"] == 500_000


def test_portfolio_customer_cannot_inject_market():
    r = client.post("/v1/portfolio/assess",
                    json={"trades": [TRADE], "market": {"sigma_ann": 0.001}},
                    headers=hdr("customer"))
    assert r.status_code == 403


def test_portfolio_rejects_empty():
    r = client.post("/v1/portfolio/assess", json={"trades": []}, headers=hdr("customer"))
    assert r.status_code == 422


# ── 알림 ────────────────────────────────────────────────────────────
def test_alert_rule_crud_and_evaluate():
    tok = hdr("customer", "alertuser")
    r = client.post("/v1/alerts/rules", json={"kind": "maturity", "days_before": 90},
                    headers=tok)
    assert r.status_code == 200
    rid = r.json()["rule_id"]

    assert any(x["rule_id"] == rid for x in client.get("/v1/alerts/rules", headers=tok).json()["rules"])

    ev = client.post("/v1/alerts/evaluate", json={"trades": [TRADE]}, headers=tok).json()
    assert ev["alerts"], "만기 90영업일 기준이면 63영업일 건이 울려야 한다"
    assert "전달되지 않습니다" in ev["delivery_note"]

    assert client.delete(f"/v1/alerts/rules/{rid}", headers=tok).status_code == 200


def test_alert_rules_are_isolated_between_users():
    client.post("/v1/alerts/rules", json={"kind": "maturity"}, headers=hdr("customer", "aa"))
    other = client.get("/v1/alerts/rules", headers=hdr("customer", "bb")).json()["rules"]
    assert other == []


def test_invalid_rule_rejected():
    r = client.post("/v1/alerts/rules", json={"kind": "bbp_above", "threshold_pct": 500},
                    headers=hdr("customer"))
    assert r.status_code == 422


# ── 라이프사이클 ────────────────────────────────────────────────────
def test_deal_flow_and_guardrails():
    cust = hdr("customer", "dealer")
    rm = hdr("rm", "rm77")

    did = client.post("/v1/deals", json={"real_demand_amount": 500000},
                      headers=cust).json()["deal_id"]

    assert client.post(f"/v1/deals/{did}/transition", json={"to": "submitted"},
                       headers=cust).status_code == 200
    # 고객이 체결로 건너뛰기 — 거부되어야 한다
    assert client.post(f"/v1/deals/{did}/transition", json={"to": "contracted"},
                       headers=cust).status_code == 409

    for st in ("docs_review", "limit_check", "contracted"):
        assert client.post(f"/v1/deals/{did}/transition", json={"to": st},
                           headers=rm).status_code == 200

    # 실수요 초과 헤지 차단
    assert client.post(f"/v1/deals/{did}/hedge", json={"amount": 400000},
                       headers=rm).status_code == 200
    assert client.post(f"/v1/deals/{did}/hedge", json={"amount": 200000},
                       headers=rm).status_code == 409
    # 보유 초과 해지 차단
    assert client.post(f"/v1/deals/{did}/unwind", json={"amount": 900000},
                       headers=rm).status_code == 409


def test_customer_cannot_contract_hedge():
    cust = hdr("customer", "dealer2")
    did = client.post("/v1/deals", json={"real_demand_amount": 100000},
                      headers=cust).json()["deal_id"]
    assert client.post(f"/v1/deals/{did}/hedge", json={"amount": 1000},
                       headers=cust).status_code == 403


def test_customer_cannot_read_others_deal():
    a = hdr("customer", "owner1")
    did = client.post("/v1/deals", json={"real_demand_amount": 100000},
                      headers=a).json()["deal_id"]
    assert client.get(f"/v1/deals/{did}", headers=hdr("customer", "stranger")).status_code == 403


def test_reduce_real_demand_warns():
    cust = hdr("customer", "dealer3")
    rm = hdr("rm", "rm78")
    did = client.post("/v1/deals", json={"real_demand_amount": 500000},
                      headers=cust).json()["deal_id"]
    client.post(f"/v1/deals/{did}/hedge", json={"amount": 500000}, headers=rm)
    d = client.post(f"/v1/deals/{did}/reduce", json={"amount": 100000}, headers=rm).json()
    assert d["warnings"] and "해지가 필요" in d["warnings"][0]


# ── 분할 헤지 일정 ──────────────────────────────────────────────────
def test_assess_returns_hedge_schedule():
    d = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    s = d["hedge_schedule"]
    assert s["target_ratio"] == d["hedge_ratio"], "분할이 목표비율을 바꿨다"
    assert sum(t["ratio"] for t in s["tranches"]) == pytest.approx(d["hedge_ratio"])


def test_schedule_amounts_match_trade_amount():
    d = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    s = d["hedge_schedule"]
    assert sum(t["amount"] for t in s["tranches"]) == pytest.approx(
        TRADE["amount"] * d["hedge_ratio"], rel=1e-6)


def test_schedule_fits_inside_horizon():
    d = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer")).json()
    for t in d["hedge_schedule"]["tranches"]:
        assert t["execute_at_bd"] < TRADE["horizon"]


# ── 상품 마스터 ─────────────────────────────────────────────────────
def test_products_listed():
    d = client.get("/v1/products", headers=hdr("customer")).json()
    assert d["products"] and d["catalog_version"]
    assert "요율" in d["note"]


def test_products_filtered_by_category():
    d = client.get("/v1/products?category=외환헤지", headers=hdr("customer")).json()
    assert d["products"]
    assert all(p["category"] == "외환헤지" for p in d["products"])


def test_products_carry_no_rate_fields():
    """요율·한도를 응답에 흘리지 않는다 — 미연동 시스템의 값이다."""
    d = client.get("/v1/products", headers=hdr("customer")).json()
    for p in d["products"]:
        for k in p:
            assert "rate" not in k or k == "rate_policy"
            assert "fee" not in k and "limit" not in k


def test_products_eligibility_uses_single_source():
    """가결제 + 무여신이면 선물환 자격이 없어야 한다(elig.py 와 같은 답)."""
    d = client.get("/v1/products?category=외환헤지&pos=export&cert=provisional&credit=no",
                   headers=hdr("customer")).json()
    fwd = [p for p in d["products"] if p["eligibility_key"] == "선물환"]
    assert fwd and fwd[0]["eligible"] is False
    assert "실수요" in fwd[0]["ineligible_reason"]


def test_products_without_trade_context_have_no_verdict():
    """거래 정보가 없으면 자격을 단정하지 않는다."""
    d = client.get("/v1/products", headers=hdr("customer")).json()
    assert all(p["eligible"] is None for p in d["products"])


# ── 제재 ────────────────────────────────────────────────────────────
def test_screening_denied_for_customer():
    r = client.post("/v1/screening", json={"party": "ACME"}, headers=hdr("customer"))
    assert r.status_code == 403


def test_screening_never_says_normal():
    """'정상' 단정 금지 — 미탐은 결백의 증명이 아니다."""
    d = client.post("/v1/screening", json={"party": "존재하지않는거래처1234"},
                    headers=hdr("compliance")).json()
    assert "정상" not in d["verdict"]


# ── 감사 ────────────────────────────────────────────────────────────
def test_audit_chain_verifies():
    client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer", "u9"))
    d = client.get("/v1/audit/verify", headers=hdr("admin")).json()
    assert d["ok"] is True and d["checked"] > 0


def test_audit_list_denied_for_customer():
    r = client.get("/v1/audit", headers=hdr("customer"))
    assert r.status_code == 403


def test_customer_sees_only_own_records():
    client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer", "alice"))
    client.post("/v1/assess", json={"trade": TRADE}, headers=hdr("customer", "bob"))
    recs = client.get("/v1/audit/mine", headers=hdr("customer", "alice")).json()["records"]
    assert recs and all(r["actor"] == "alice" for r in recs)


def test_tampering_breaks_the_chain():
    """해시체인의 존재 이유 — 사후 변경이 탐지되는가."""
    import sqlite3
    from app.audit import AuditLog
    p = os.path.join(_TMP, "tamper.sqlite3")
    log = AuditLog(p)
    for i in range(3):
        log.append(actor="u", role="customer", event="assess",
                   payload={"i": i}, engine_ver="test")
    assert log.verify()["ok"] is True
    log.close()

    con = sqlite3.connect(p)
    con.execute("UPDATE audit SET payload='{\"i\":999}' WHERE seq=2")
    con.commit(); con.close()

    log2 = AuditLog(p)
    v = log2.verify()
    assert v["ok"] is False and v["broken_at_seq"] == 2
    log2.close()


# ── 경계 공개 ───────────────────────────────────────────────────────
def test_integrations_declares_what_is_missing():
    d = client.get("/v1/integrations").json()
    systems = [x["system"] for x in d["not_connected"]]
    assert any("원장" in s for s in systems)
    assert any("SSO" in s for s in systems)
    assert any("제재" in s for s in d["connected"])
