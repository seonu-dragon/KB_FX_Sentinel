"""F1 금소법 판매프로세스 — 단위 + API 테스트.

이 테스트가 지키는 불변식:
  · 정보가 없으면 **막힌다** (보수적 기본값). 통과가 기본값이면 규제가 아니다.
  · 파생상품에만 적정성이 걸린다 — 보험(K-SURE)·여신까지 막으면 무여신 SME 의
    유일한 헤지 수단이 사라진다.
  · '자격 없음'과 '권유 보류'는 다른 사유로 분리돼 나온다.
  · 이해확인 거부는 409 이고, 거부 사실도 감사로그에 남는다.
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

_TMP = tempfile.mkdtemp(prefix="fxs_suit_")
os.environ.setdefault("DEV_JWT_SECRET", "test-only-secret")
os.environ.setdefault("AUDIT_DB", os.path.join(_TMP, "audit.sqlite3"))
os.environ.setdefault("SANCTIONS_DIR", os.path.join(_TMP, "sanctions"))

from app import suitability as S              # noqa: E402
from app.auth import issue_dev_token          # noqa: E402
from app.main import app                      # noqa: E402
from tests.asgi_client import ASGIClient      # noqa: E402

client = ASGIClient(app)

TRADE = {
    "name": "한빛정밀", "party": "Tokyo Precision KK", "pos": "export",
    "cert": "confirmed", "credit": "yes", "cash": "ok", "biz": "corp",
    "budget_rate": 1450, "amount": 500000, "horizon": 63, "currency": "USD",
    "country": "미국",
}

# 적정성을 통과하는 고객 (5문항 중 5)
GOOD = {"deriv_exp": "experienced", "prior_loss": "no",
        "loss_tolerance_krw": 500_000_000, "understands": "yes"}
# 부적정 고객 — 경험 없음·이해 안 됨·감내 0 (소망전자 데모 프로필)
BAD = {"deriv_exp": "none", "prior_loss": "no",
       "loss_tolerance_krw": 0, "understands": "no"}


def hdr(role: str = "rm", sub: str = "suit-user") -> dict:
    return {"Authorization": "Bearer " + issue_dev_token(sub, role)}


# ── 1. 소비자 유형 ───────────────────────────────────────────────────
def test_default_is_general_consumer():
    t, why = S.consumer_type(S.ConsumerProfile())
    assert t == "일반금융소비자"
    assert "전환 신청 없음" in why


def test_sole_proprietor_cannot_become_professional():
    """개인사업자는 전환 신청이 있어도 일반으로 남는다 (보수적 적용)."""
    p = S.ConsumerProfile(biz="sole", pro_declared=True, deriv_exp="experienced")
    t, why = S.consumer_type(p)
    assert t == "일반금융소비자"
    assert "개인사업자" in why


def test_professional_requires_both_declaration_and_experience():
    assert S.consumer_type(S.ConsumerProfile(
        biz="corp", pro_declared=True, deriv_exp="limited"))[0] == "일반금융소비자"
    assert S.consumer_type(S.ConsumerProfile(
        biz="corp", pro_declared=True, deriv_exp="experienced"))[0] == "전문금융소비자"


# ── 2. 적정성 ────────────────────────────────────────────────────────
def test_empty_profile_fails_suitability():
    """정보 미제출 = 부적정. 통과가 기본값이면 규제가 아니다."""
    r = S.assess_suitability(S.ConsumerProfile(), scenario_loss_krw=10_000_000)
    assert r["advisable"] is False
    assert r["met"] <= 2
    assert "부적정" in r["verdict"]


def test_full_profile_passes():
    p = S.ConsumerProfile(deriv_exp="experienced", prior_loss="no",
                          loss_tolerance_krw=100_000_000, understands="yes", cash="ok")
    r = S.assess_suitability(p, scenario_loss_krw=10_000_000)
    assert r["met"] == 5 and r["advisable"] is True and r["verdict"] == "적정"


def test_tolerance_is_relative_to_trade_size():
    """감내 규모는 고정 기준이 아니라 이 거래의 시나리오 손실과 비교된다."""
    p = S.ConsumerProfile(deriv_exp="experienced", prior_loss="no",
                          loss_tolerance_krw=5_000_000, understands="yes", cash="ok")
    small = S.assess_suitability(p, scenario_loss_krw=1_000_000)
    large = S.assess_suitability(p, scenario_loss_krw=50_000_000)
    assert small["advisable"] is True and large["advisable"] is False


def test_tolerance_is_knockout_not_a_deduction():
    """감내 미달은 점수로 상쇄되지 않는다.

    설계 초안의 단순 합산에서는 경험·이해·현금이 모두 좋으면 감내 0 인 고객도 4/5 로
    '적정'이 됐다 — KIKO 가 난 경로가 정확히 그것이다. 그래서 필수항목으로 승격했다.
    """
    p = S.ConsumerProfile(deriv_exp="experienced", prior_loss="no",
                          loss_tolerance_krw=0, understands="yes", cash="ok")
    r = S.assess_suitability(p, scenario_loss_krw=50_000_000)
    assert r["met"] == 4                      # 점수는 높지만
    assert r["advisable"] is False            # 통과하지 못한다
    assert "tolerance" in r["failed_knockout"]
    assert "감내" in r["verdict"]


def test_understanding_is_knockout():
    """이해 미확인은 설명의무 미이행이다 — 경험이 많아도 통과시키지 않는다."""
    p = S.ConsumerProfile(deriv_exp="experienced", prior_loss="no",
                          loss_tolerance_krw=10_000_000_000, understands="no", cash="ok")
    r = S.assess_suitability(p, scenario_loss_krw=1_000_000)
    assert r["advisable"] is False and "understand" in r["failed_knockout"]


def test_caution_band_still_advisable():
    """3/5 는 '주의'이지 차단이 아니다 — 과하게 막으면 정상 고객을 잃는다.

    필수항목(이해·감내)은 충족하고 경험·현금만 부족한 경우다.
    """
    p = S.ConsumerProfile(deriv_exp="none", prior_loss="no",
                          loss_tolerance_krw=1_000_000_000, understands="yes", cash="tight")
    r = S.assess_suitability(p, scenario_loss_krw=10_000_000)
    assert r["met"] == S.SUIT_CAUTION
    assert r["failed_knockout"] == []
    assert r["advisable"] is True and "주의" in r["verdict"]


def test_suitability_note_declares_demo_origin():
    """평가표가 KB 공식이 아님을 응답이 스스로 밝힌다."""
    r = S.assess_suitability(S.ConsumerProfile(), 1_000_000)
    assert "KB" in r["note"] and "데모" in r["note"]


# ── 3. 손실 시나리오 ─────────────────────────────────────────────────
def test_sigma_move_scales_with_horizon():
    a = S.sigma_move(1500, 0.10, 63)
    b = S.sigma_move(1500, 0.10, 252)
    assert b > a > 0
    assert abs(b - 1500 * 0.10) < 1e-6          # 1년 = √1 = σ 그대로


def test_sigma_move_guards_bad_input():
    assert S.sigma_move(0, 0.1, 63) == 0.0
    assert S.sigma_move(1500, 0, 63) == 0.0
    assert S.sigma_move(1500, 0.1, 0) == 0.0


def test_range_forward_loss_is_smaller_than_forward():
    """범위형은 밴드폭만큼 참여하므로 기회손실이 선물환보다 작다."""
    kw = dict(amount=500_000, spot=1528.8, sigma_ann=0.098, horizon_bd=63)
    fwd = S.loss_scenarios("선물환", **kw)
    rng = S.loss_scenarios("범위선물환", band_pct=2.0, **kw)
    assert rng["opportunity_loss_1sigma_krw"] < fwd["opportunity_loss_1sigma_krw"]
    assert rng["band_krw"] > 0


def test_forward_scenario_warns_margin_call():
    s = S.loss_scenarios("선물환", 500_000, 1528.8, 0.098, 63)
    assert "추가담보" in s["margin_call"]


def test_insurance_scenario_has_no_bank_margin():
    s = S.loss_scenarios("환변동보험", 500_000, 1528.8, 0.098, 63)
    assert "여신" in s["margin_call"] or "공적보험" in s["margin_call"]
    assert s["band_krw"] == 0.0


def test_scenario_disclaims_prediction():
    """무예측 원칙 — 시나리오가 예측으로 읽히면 이 프로젝트의 전제가 깨진다."""
    s = S.loss_scenarios("선물환", 500_000, 1528.8, 0.098, 63)
    assert "예측이 아닙니다" in s["disclaimer"]


def test_scenario_does_not_invent_contract_rate():
    """계약환율·밴드가를 만들지 않는다 (products.yaml rate_policy)."""
    s = S.loss_scenarios("범위선물환", 500_000, 1528.8, 0.098, 63)
    assert "RM 견적" in s["disclaimer"]


# ── 4. 꺾기 ──────────────────────────────────────────────────────────
def test_kickback_flag_when_credit_and_derivative_together():
    f = S.kickback_flags(["특별출연", "선물환"])
    assert f and "꺾기" in f[0]


def test_no_kickback_flag_for_derivative_only():
    assert S.kickback_flags(["선물환", "칼라"]) == []


def test_no_kickback_flag_for_credit_only():
    assert S.kickback_flags(["특별출연", "외화대출"]) == []


def test_kickback_window_flag():
    near = S.kickback_flags(["선물환"], credit_exec_days=10)
    far = S.kickback_flags(["선물환"], credit_exec_days=200)
    assert any("구속성" in x for x in near)
    assert far == []


# ── 5. 통합 게이트 ───────────────────────────────────────────────────
def _gate(profile, keys=("선물환", "환변동보험", "특별출연")):
    return S.sales_gate(list(keys), profile, amount=500_000, spot=1528.8,
                        sigma_ann=0.098, horizon_bd=63)


def test_gate_withholds_derivative_but_keeps_insurance():
    """부적정이어도 K-SURE 보험·여신은 남는다 — 이게 포용의 핵심이다."""
    g = _gate(S.ConsumerProfile(**BAD))
    assert "선물환" not in g["advisable_keys"]
    assert "환변동보험" in g["advisable_keys"]
    assert "특별출연" in g["advisable_keys"]
    assert [w["key"] for w in g["withheld"]] == ["선물환"]


def test_gate_allows_all_for_qualified_consumer():
    g = _gate(S.ConsumerProfile(**GOOD))
    assert "선물환" in g["advisable_keys"] and g["withheld"] == []


def test_professional_consumer_is_exempt():
    """전문금융소비자는 적정성 면제 — 면제 '사실'이 응답에 남는다."""
    p = S.ConsumerProfile(biz="corp", pro_declared=True, deriv_exp="experienced")
    g = _gate(p)
    assert g["suitability_exempt"] is True
    assert "선물환" in g["advisable_keys"]


def test_withheld_carries_remedy():
    """막기만 하고 대안을 안 주면 RM 일만 늘어난다."""
    g = _gate(S.ConsumerProfile(**BAD))
    assert g["withheld"][0]["remedy"]


def test_gate_scenarios_cover_derivatives_and_insurance():
    g = _gate(S.ConsumerProfile(**GOOD))
    assert "선물환" in g["scenarios"] and "환변동보험" in g["scenarios"]
    assert "특별출연" not in g["scenarios"]      # 여신은 시나리오 대상 아님


# ── 6. API ───────────────────────────────────────────────────────────
def test_assess_returns_sales_gate():
    r = client.post("/v1/assess", json={"trade": TRADE, "consumer": GOOD}, headers=hdr())
    assert r.status_code == 200
    g = r.json()["sales_gate"]
    assert g["consumer_type"] == "일반금융소비자"
    assert g["suitability"]["met"] == 5
    assert "선물환" in g["advisable_keys"]


def test_assess_without_consumer_blocks_derivative():
    """consumer 미제출 = 확인 안 됨 = 파생 보류. 하위호환이되 안전한 쪽으로."""
    r = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr())
    assert r.status_code == 200
    body = r.json()
    assert "선물환" not in body["sales_gate"]["advisable_keys"]
    assert any("권유 보류" in b for b in body["blocked"])


def test_assess_bad_consumer_withholds_and_explains():
    r = client.post("/v1/assess", json={"trade": TRADE, "consumer": BAD}, headers=hdr())
    body = r.json()
    withheld = {w["key"]: w for w in body["sales_gate"]["withheld"]}
    assert "선물환" in withheld
    assert "적정성" in withheld["선물환"]["reason"]
    # 자격 판정 자체는 그대로 — 두 축이 섞이지 않았는지 확인
    elig = {d["key"]: d for d in body["eligibility"]}
    assert elig["선물환"]["eligible"] is True


def test_kickback_flag_surfaces_in_blocked():
    """무여신 수출 = 특별출연(여신) + 범위선물환(파생) 동시 → 꺾기 플래그."""
    t = dict(TRADE, credit="no")
    r = client.post("/v1/assess", json={"trade": t, "consumer": GOOD}, headers=hdr())
    body = r.json()
    assert any("꺾기" in b for b in body["blocked"])


def test_keyfacts_returns_hash_and_risks():
    r = client.post("/v1/keyfacts",
                    json={"trade": TRADE, "instrument": "선물환", "consumer": GOOD},
                    headers=hdr())
    assert r.status_code == 200
    b = r.json()
    assert len(b["sheet_hash"]) == 32
    assert b["explain_duty"] is True
    assert any("유리하게" in x for x in b["risks"])
    assert any("예측하지 않습니다" in x for x in b["risks"])


def test_keyfacts_hash_is_stable_and_content_bound():
    """같은 입력 = 같은 해시. 상품이 다르면 다른 해시."""
    a = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "선물환"},
                    headers=hdr()).json()
    b = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "선물환"},
                    headers=hdr()).json()
    c = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "범위선물환"},
                    headers=hdr()).json()
    assert a["sheet_hash"] == b["sheet_hash"] != c["sheet_hash"]


def test_ack_records_understanding():
    kf = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "선물환"},
                     headers=hdr()).json()
    r = client.post("/v1/keyfacts/ack",
                    json={"instrument": "선물환", "sheet_hash": kf["sheet_hash"],
                          "understood": True, "customer_name": "한빛정밀"},
                    headers=hdr())
    assert r.status_code == 200 and r.json()["recorded"] is True
    assert r.json()["sheet_hash"] == kf["sheet_hash"]


def test_ack_declined_returns_409():
    kf = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "선물환"},
                     headers=hdr()).json()
    r = client.post("/v1/keyfacts/ack",
                    json={"instrument": "선물환", "sheet_hash": kf["sheet_hash"],
                          "understood": False},
                    headers=hdr())
    assert r.status_code == 409
    assert "설명의무" in r.json()["detail"]


def test_keyfacts_requires_auth():
    r = client.post("/v1/keyfacts", json={"trade": TRADE, "instrument": "선물환"})
    assert r.status_code == 401


def test_sales_gate_audited():
    """판매 판정도 감사 대상 — '왜 그때 막았나'에 답할 수 있어야 한다."""
    client.post("/v1/assess", json={"trade": TRADE, "consumer": BAD},
                headers=hdr(sub="audit-probe"))
    tail = client.get("/v1/audit/mine?limit=5", headers=hdr(sub="audit-probe")).json()
    rows = tail["entries"] if isinstance(tail, dict) and "entries" in tail else tail
    blob = str(rows)
    assert "sales_gate" in blob and "suitability_verdict" in blob
