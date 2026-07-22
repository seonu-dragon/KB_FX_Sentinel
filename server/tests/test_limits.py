"""F3 여신 한도 소진 — 단위 + API 테스트.

이 테스트가 지키는 불변식:
  · 소진율은 **명목 기준**(고객 약정 방식), 여신 계상액은 **CEE**(은행 내부). 둘 다 낸다.
  · CEE 는 명목의 일부다 — 파생은 명목 전액이 여신으로 잡히지 않는다.
  · 한도 미입력은 '초과'가 아니라 **'미확인'** 이다. 모르는 것과 없는 것은 다르다.
  · 초과 시 수출은 여신 불요 대안(K-SURE)이 남는다. 수입은 대안이 없고, 없는 상품을
    지어내지 않는다.
  · 요율·수수료는 어디서도 산출하지 않는다(products.yaml rate_policy).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="fxs_lim_")
os.environ.setdefault("DEV_JWT_SECRET", "test-only-secret")
os.environ.setdefault("AUDIT_DB", os.path.join(_TMP, "audit.sqlite3"))
os.environ.setdefault("SANCTIONS_DIR", os.path.join(_TMP, "sanctions"))

from app import limits as L                 # noqa: E402
from app.auth import issue_dev_token        # noqa: E402
from app.main import app                    # noqa: E402
from tests.asgi_client import ASGIClient    # noqa: E402

client = ASGIClient(app)

TRADE = {
    "name": "한빛정밀", "party": "Tokyo Precision KK", "pos": "export",
    "cert": "confirmed", "credit": "yes", "cash": "ok", "biz": "corp",
    "budget_rate": 1450, "amount": 500000, "horizon": 63, "currency": "USD",
    "country": "미국",
}
GOOD_CONSUMER = {"deriv_exp": "experienced", "prior_loss": "no",
                 "loss_tolerance_krw": 500_000_000, "understands": "yes"}
SPOT = 1528.8       # config.snapshot_spot 과 같은 값


def hdr(role: str = "rm", sub: str = "lim-user") -> dict:
    return {"Authorization": "Bearer " + issue_dev_token(sub, role)}


# ── 1. 신용환산 ──────────────────────────────────────────────────────
def test_ccf_increases_with_maturity():
    """만기가 길수록 환산율이 크다 — 오래 남을수록 더 움직일 수 있기 때문이다."""
    assert L.ccf_for(63) < L.ccf_for(500) < L.ccf_for(2000)


def test_ccf_band_boundaries():
    assert L.ccf_for(252) == 0.01          # 1년 이하
    assert L.ccf_for(253) == 0.05          # 1년 초과
    assert L.ccf_for(1260) == 0.05
    assert L.ccf_for(1261) == 0.075


def test_cee_is_a_fraction_of_notional():
    """파생은 명목 전액이 여신으로 잡히지 않는다 — 이걸 틀리면 한도를 100배 과대계상한다."""
    cee = L.credit_equivalent(500_000, 63)
    assert cee == pytest.approx(500_000 * 0.01)
    assert cee < 500_000 / 50


def test_cee_guards_bad_input():
    assert L.credit_equivalent(0, 63) == 0.0
    assert L.credit_equivalent(1000, 0) == 0.0


# ── 2. 한도 판정 (명목 기준) ─────────────────────────────────────────
def test_unknown_limit_is_not_exceeded():
    """한도를 모르는 고객 전원을 차단하면 안 된다."""
    r = L.assess_limit(None, 500_000, 63, spot=SPOT)
    assert r["known"] is False
    assert r["exceeds"] is False
    assert r["status"] == "미확인"
    # 소진 여부는 몰라도 여신 계상액은 계산해 준다
    assert r["cee_notional"] > 0 and r["cee_krw"] > 0


def test_zero_limit_treated_as_unknown():
    r = L.assess_limit(L.LimitInput(0, 0), 500_000, 63, spot=SPOT)
    assert r["known"] is False and r["exceeds"] is False


def test_utilisation_is_notional_based():
    """소진율은 CEE 가 아니라 명목으로 센다 — 고객 약정이 명목으로 나가기 때문."""
    li = L.LimitInput(limit_notional=1_000_000, used_notional=320_000)
    r = L.assess_limit(li, 500_000, 63, spot=SPOT)
    assert r["util_before"] == pytest.approx(0.32)
    assert r["util_after"] == pytest.approx(0.82)      # (320k+500k)/1M
    assert r["exceeds"] is False


def test_both_numbers_reported():
    """고객이 묻는 숫자(명목 소진)와 여신부가 묻는 숫자(CEE)를 함께 낸다."""
    li = L.LimitInput(1_000_000, 320_000)
    r = L.assess_limit(li, 500_000, 63, spot=SPOT)
    assert r["notional"] == 500_000
    assert r["cee_notional"] == pytest.approx(5_000)          # 명목의 1%
    assert r["cee_krw"] == pytest.approx(5_000 * SPOT, rel=0.01)


def test_warn_band_before_full():
    """80% 를 넘으면 아직 초과는 아니지만 경고한다 — 후속 거래 여력 문제."""
    li = L.LimitInput(1_000_000, 320_000)
    r = L.assess_limit(li, 500_000, 63, spot=SPOT)
    assert r["status"] == "임박" and r["exceeds"] is False
    assert r["util_after"] >= L.UTIL_WARN


def test_room_available():
    li = L.LimitInput(1_000_000, 100_000)
    r = L.assess_limit(li, 200_000, 63, spot=SPOT)
    assert r["status"] == "여유" and r["exceeds"] is False
    assert r["util_after"] == pytest.approx(0.3)


def test_exceeds_when_notional_pushes_over():
    """데모 동선 — 금액을 올리면 한도가 넘는다."""
    li = L.LimitInput(1_000_000, 320_000)
    r = L.assess_limit(li, 860_000, 63, spot=SPOT)
    assert r["exceeds"] is True and r["status"] == "초과"
    assert r["util_after"] == pytest.approx(1.18)
    assert "K-SURE" in r["message"] or "여신 증액" in r["message"]


def test_available_never_negative():
    li = L.LimitInput(1_000_000, 5_000_000)
    r = L.assess_limit(li, 100, 63, spot=SPOT)
    assert r["available_notional"] == 0


def test_longer_maturity_costs_more_credit():
    """같은 명목이라도 만기가 길면 여신 계상액이 커진다 (소진율은 같다)."""
    li = L.LimitInput(1_000_000, 0)
    short = L.assess_limit(li, 500_000, 63, spot=SPOT)
    long = L.assess_limit(li, 500_000, 500, spot=SPOT)
    assert long["cee_notional"] > short["cee_notional"]
    assert long["util_after"] == short["util_after"]


def test_note_declares_self_reported_and_demo_ccf():
    """자기신고·데모 환산율임을 응답이 스스로 밝힌다."""
    r = L.assess_limit(None, 1000, 63)
    assert "자기신고" in r["note"] and "데모 설계값" in r["note"]


def test_note_declines_to_price():
    """요율·수수료는 산출하지 않는다(rate_policy)."""
    r = L.assess_limit(None, 1000, 63)
    assert "RM 견적" in r["note"]
    assert "금리" in r["note"]


# ── 3. 대안 라우팅 ───────────────────────────────────────────────────
def test_fallback_only_when_exceeding():
    assert L.fallback_keys(False, "export") == []


def test_export_fallback_is_credit_free():
    """K-SURE 는 공적보험이라 은행 여신을 쓰지 않는다 → 한도와 무관하게 살아남는다."""
    fb = L.fallback_keys(True, "export")
    assert "환변동보험" in fb


def test_import_has_no_fallback():
    """수입 + 한도초과는 대안이 없다. 없는 상품을 지어내지 않는다."""
    assert L.fallback_keys(True, "import") == []


# ── 4. API ───────────────────────────────────────────────────────────
def test_assess_returns_limit_unknown_by_default():
    r = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr())
    assert r.status_code == 200
    lim = r.json()["limit"]
    assert lim["known"] is False and lim["exceeds"] is False
    assert lim["cee_krw"] > 0


def test_assess_limit_room():
    r = client.post("/v1/assess", json={
        "trade": TRADE, "consumer": GOOD_CONSUMER,
        "limits": {"limit_notional": 2_000_000, "used_notional": 100_000}}, headers=hdr())
    lim = r.json()["limit"]
    assert lim["known"] is True and lim["exceeds"] is False and lim["status"] == "여유"


def test_assess_limit_warn_matches_demo_preset():
    """데모 동선 2번 출발점 — 한빛정밀 한도 100만불 중 32만 기사용, 이 건 50만 → 82%."""
    r = client.post("/v1/assess", json={
        "trade": TRADE, "consumer": GOOD_CONSUMER,
        "limits": {"limit_notional": 1_000_000, "used_notional": 320_000}}, headers=hdr())
    lim = r.json()["limit"]
    assert lim["util_after"] == pytest.approx(0.82)
    assert lim["status"] == "임박" and lim["exceeds"] is False


def test_assess_limit_exceeded_blocks_and_offers_fallback():
    """데모 동선 2번 — 금액을 올리면 118% 로 넘고 K-SURE 로 넘어간다."""
    t = dict(TRADE, amount=860_000)
    r = client.post("/v1/assess", json={
        "trade": t, "consumer": GOOD_CONSUMER,
        "limits": {"limit_notional": 1_000_000, "used_notional": 320_000}}, headers=hdr())
    body = r.json()
    assert body["limit"]["exceeds"] is True
    assert body["limit"]["util_after"] == pytest.approx(1.18)
    assert any("한도 초과" in b for b in body["blocked"])
    assert "환변동보험" in body["limit"]["fallback_keys"]


def test_import_exceeded_has_no_fallback_via_api():
    t = dict(TRADE, pos="import", amount=860_000)
    r = client.post("/v1/assess", json={
        "trade": t, "limits": {"limit_notional": 1_000_000, "used_notional": 320_000}},
        headers=hdr())
    body = r.json()
    assert body["limit"]["exceeds"] is True
    assert body["limit"]["fallback_keys"] == []


def test_cee_krw_uses_spot_conversion():
    """CEE 원화 표시는 스냅샷 환율 환산 — 빼먹으면 1528배 틀린다."""
    r = client.post("/v1/assess", json={
        "trade": TRADE, "limits": {"limit_notional": 5_000_000, "used_notional": 0}},
        headers=hdr())
    lim = r.json()["limit"]
    assert lim["cee_notional"] == pytest.approx(5_000)
    assert lim["cee_krw"] == pytest.approx(5_000 * SPOT, rel=0.01)


def test_limit_is_audited():
    client.post("/v1/assess", json={
        "trade": TRADE, "limits": {"limit_notional": 1_000_000, "used_notional": 1_000}},
        headers=hdr(sub="lim-audit"))
    tail = client.get("/v1/audit/mine?limit=5", headers=hdr(sub="lim-audit")).json()
    assert "cee_krw" in str(tail)
