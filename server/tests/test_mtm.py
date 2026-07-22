"""F2 체결 후 MTM·증거금 스트레스 — 단위 + API 테스트.

이 테스트가 지키는 불변식:
  · **방향을 틀리지 않는다.** 수출(매도)은 환율 상승에서, 수입(매수)은 하락에서 아프다.
    화면이 "달러 강세 = 위험"으로 고정해 말하면 수입기업에게 거짓말이 된다.
  · 평가익은 담보 요구 대상이 아니다.
  · 감내 가능 '손실'(F1)과 즉시 동원 '현금'(F2)은 다른 값이다 — KIKO 가 난 지점이 이 차이다.
  · 할인·스왑포인트를 지어내지 않는다(근사임을 응답이 밝힌다).
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

_TMP = tempfile.mkdtemp(prefix="fxs_mtm_")
os.environ.setdefault("DEV_JWT_SECRET", "test-only-secret")
os.environ.setdefault("AUDIT_DB", os.path.join(_TMP, "audit.sqlite3"))
os.environ.setdefault("SANCTIONS_DIR", os.path.join(_TMP, "sanctions"))

from app import mtm as M                    # noqa: E402
from app.auth import issue_dev_token        # noqa: E402
from app.main import app                    # noqa: E402
from tests.asgi_client import ASGIClient    # noqa: E402

client = ASGIClient(app)
SPOT = 1528.8

# 데모의 체결 건 — 나래상사(수입) 선물환 매수 300,000 @ 1526.4
NARAE = {"pos": "import", "notional": 300_000, "contract_rate": 1526.4,
         "horizon_bd": 63, "currency": "USD"}
KINGDOLLAR_SIGMA = 0.164        # 2022 달러 강세 국면 실측 σ (REG.USD 와 같은 값)


def hdr(role: str = "rm", sub: str = "mtm-user") -> dict:
    return {"Authorization": "Bearer " + issue_dev_token(sub, role)}


# ── 1. 평가손익 방향 ─────────────────────────────────────────────────
def test_export_loses_when_rate_rises():
    """매도 선물환 — 시장이 오르면 계약환율에 팔아야 하므로 손해."""
    assert M.mtm_value("export", 1500, 1600, 100) < 0
    assert M.mtm_value("export", 1500, 1400, 100) > 0


def test_import_loses_when_rate_falls():
    """매수 선물환 — 시장이 내리면 계약환율에 사야 하므로 손해."""
    assert M.mtm_value("import", 1500, 1400, 100) < 0
    assert M.mtm_value("import", 1500, 1600, 100) > 0


def test_positions_are_mirror_images():
    a = M.mtm_value("export", 1500, 1600, 100)
    b = M.mtm_value("import", 1500, 1600, 100)
    assert a == pytest.approx(-b)


def test_mtm_zero_notional_guard():
    assert M.mtm_value("export", 1500, 1600, 0) == 0.0


def test_adverse_direction_differs_by_position():
    """이 구분이 F2 의 핵심 — 방향을 고정해 말하면 한쪽에게 거짓말이 된다."""
    assert M.adverse_direction("export")["direction"] == "up"
    assert M.adverse_direction("import")["direction"] == "down"


def test_kingdollar_is_a_gain_for_importer():
    """데모 체결 건은 수입이다 — 달러 강세(원화 약세)에서 평가익이 난다.

    초안 데모 시나리오가 '달러 강세 → 체결 선물환 평가손'이었는데, 수입 매수 선물환에는
    틀린 서술이다. 헤지가 제 역할을 한 것이고, 이 회사가 아픈 방향은 반대다.
    """
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, KINGDOLLAR_SIGMA)
    up = [r for r in out["rows"] if r["sigma"] > 0]
    down = [r for r in out["rows"] if r["sigma"] < 0]
    assert all(r["mtm_krw"] > 0 for r in up), "수입 헤지는 환율 상승에서 평가익"
    assert all(r["mtm_krw"] < 0 for r in down), "수입 헤지는 환율 하락에서 평가손"
    assert out["adverse"]["direction"] == "down"


# ── 2. 추가담보 ──────────────────────────────────────────────────────
def test_gain_never_triggers_margin_call():
    mc = M.margin_call(mtm_krw=+50_000_000, notional_krw=100_000_000)
    assert mc["triggered"] is False and mc["required_krw"] == 0


def test_small_loss_below_threshold_no_call():
    """명목의 10% 이하 평가손은 요구선에 안 닿는다."""
    mc = M.margin_call(mtm_krw=-5_000_000, notional_krw=100_000_000)
    assert mc["triggered"] is False


def test_large_loss_triggers_and_computes_shortfall():
    mc = M.margin_call(mtm_krw=-30_000_000, notional_krw=100_000_000,
                       cash_buffer_krw=5_000_000)
    assert mc["triggered"] is True
    assert mc["required_krw"] == 20_000_000          # 손실 3천만 − 임계 1천만
    assert mc["coverable"] is False
    assert mc["shortfall_krw"] == 15_000_000


def test_sufficient_cash_is_coverable():
    mc = M.margin_call(mtm_krw=-30_000_000, notional_krw=100_000_000,
                       cash_buffer_krw=50_000_000)
    assert mc["triggered"] is True and mc["coverable"] is True
    assert mc["shortfall_krw"] == 0


# ── 3. 스트레스 통합 ─────────────────────────────────────────────────
def test_stress_flags_uncoverable_for_importer_on_appreciation():
    """원화 강세 2σ 에서 수입기업이 감당 못 하는 구간이 잡힌다."""
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, KINGDOLLAR_SIGMA, cash_buffer_krw=1_000_000)
    assert out["verdict"] == "감당 불가 구간 있음"
    worst = out["worst"]
    assert worst["sigma"] == -2.0 and worst["mtm_krw"] < 0


def test_stress_with_large_buffer_is_coverable():
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, KINGDOLLAR_SIGMA, cash_buffer_krw=5_000_000_000)
    assert out["verdict"] != "감당 불가 구간 있음"


def test_calm_regime_may_not_reach_trigger():
    """평온 국면에서는 요구선에 안 닿는다 — 국면이 결과를 바꾼다는 증명."""
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, 0.058, cash_buffer_krw=0)
    assert out["verdict"] == "추가담보 요구선 미도달"


def test_stress_rows_cover_both_directions():
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, KINGDOLLAR_SIGMA)
    assert [r["sigma"] for r in out["rows"]] == [-2.0, -1.0, 1.0, 2.0]


def test_note_declares_approximation_and_no_prediction():
    c = M.Contract(**NARAE)
    out = M.stress(c, SPOT, KINGDOLLAR_SIGMA)
    assert "근사" in out["note"]
    assert "스왑포인트" in out["note"] and "RM 견적" in out["note"]
    assert "예측이 아닙니다" in out["note"]
    assert "데모 설계값" in out["note"]


# ── 4. 명목 역산 ─────────────────────────────────────────────────────
def test_sizing_advice_when_uncoverable():
    """막기만 하지 않고 '얼마로 줄였어야 했나'를 준다."""
    c = M.Contract(**NARAE)
    s = M.sizing_advice(c, SPOT, KINGDOLLAR_SIGMA, cash_buffer_krw=1_000_000)
    assert s is not None
    assert 0 < s["max_notional"] < c.notional
    assert s["reduce_by"] == pytest.approx(c.notional - s["max_notional"])


def test_no_sizing_advice_when_already_fine():
    c = M.Contract(**NARAE)
    assert M.sizing_advice(c, SPOT, KINGDOLLAR_SIGMA, 5_000_000_000) is None


def test_no_sizing_advice_when_trigger_never_hit():
    """이동폭이 트리거보다 작으면 어떤 명목에서도 요구가 없다 → 조언 불필요."""
    c = M.Contract(**NARAE)
    assert M.sizing_advice(c, SPOT, 0.01, 0) is None


# ── 5. API ───────────────────────────────────────────────────────────
def test_mtm_endpoint_requires_auth():
    assert client.post("/v1/mtm", json=NARAE).status_code == 401


def test_mtm_endpoint_returns_stress_table():
    r = client.post("/v1/mtm", json=dict(NARAE, sigma_ann=KINGDOLLAR_SIGMA,
                                         regime_name="2022 달러 강세",
                                         cash_buffer_krw=1_000_000), headers=hdr())
    assert r.status_code == 200
    b = r.json()
    assert b["regime"] == "2022 달러 강세"
    assert len(b["rows"]) == 4
    assert b["adverse"]["direction"] == "down"
    assert b["verdict"] == "감당 불가 구간 있음"
    assert b["sizing"] is not None


def test_mtm_defaults_to_snapshot_sigma():
    r = client.post("/v1/mtm", json=NARAE, headers=hdr())
    assert r.json()["sigma_ann"] == pytest.approx(0.098)


def test_mtm_reports_bank_side_cee():
    """소비자보호와 은행 신용리스크가 같은 화면에서 만나는 지점."""
    r = client.post("/v1/mtm", json=NARAE, headers=hdr())
    b = r.json()
    assert b["bank_cee_notional"] == pytest.approx(3_000)      # 명목의 1%
    assert b["bank_cee_krw"] == pytest.approx(3_000 * SPOT, rel=0.01)


def test_mtm_market_source_is_not_kb_official():
    r = client.post("/v1/mtm", json=NARAE, headers=hdr())
    assert "KB 고시환율 아님" in r.json()["market_source"]


def test_mtm_is_audited():
    client.post("/v1/mtm", json=dict(NARAE, sigma_ann=KINGDOLLAR_SIGMA),
                headers=hdr(sub="mtm-audit"))
    tail = client.get("/v1/audit/mine?limit=5", headers=hdr(sub="mtm-audit")).json()
    assert "mtm.stress" in str(tail)


def test_export_position_via_api_has_opposite_adverse():
    r = client.post("/v1/mtm", json=dict(NARAE, pos="export"), headers=hdr())
    assert r.json()["adverse"]["direction"] == "up"
