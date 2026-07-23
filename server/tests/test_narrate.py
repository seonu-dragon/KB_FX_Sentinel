"""LLM 설명층(/v1/narrate) — 그라운딩·데이터 최소화·폴백 테스트.

핵심 계약:
  · 기본값은 template(키 미설정/미활성) — 항상 유효한 설명을 돌려준다.
  · 숫자는 서버 엔진이 다시 계산한다(화면 값 안 믿음).
  · LLM 출력이 환각(사실에 없는 수치)·금지표현이면 template 으로 폴백한다.
  · LLM 에는 기업명·예산환율·금액을 보내지 않는다(de-identify).
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="fxs_test_narrate_")
os.environ["DEV_JWT_SECRET"] = "test-only-secret"
os.environ["AUDIT_DB"] = os.path.join(_TMP, "audit.sqlite3")
os.environ["SANCTIONS_DIR"] = os.path.join(_TMP, "sanctions")
os.environ.pop("AI_NARRATE_ENABLED", None)   # 기본 = 비활성(template)

from app import narrate as narrate_mod       # noqa: E402
from app.auth import issue_dev_token          # noqa: E402
from app.main import app                      # noqa: E402
from tests.asgi_client import ASGIClient      # noqa: E402

client = ASGIClient(app)

TRADE = {
    "name": "나래상사", "party": "Narae Materials Import", "pos": "import",
    "cert": "confirmed", "credit": "yes", "cash": "ok", "biz": "corp",
    "budget_rate": 1500, "amount": 400000, "horizon": 63, "currency": "USD",
    "country": "베트남",
}


def hdr(role: str = "rm", sub: str = "u1") -> dict:
    return {"Authorization": "Bearer " + issue_dev_token(sub, role)}


# ── 인증 ─────────────────────────────────────────────────────────────
def test_narrate_requires_token():
    r = client.post("/v1/narrate", json={"trade": TRADE})
    assert r.status_code == 401


def test_market_override_needs_rm():
    r = client.post("/v1/narrate",
                    json={"trade": TRADE, "market": {"sigma_ann": 0.16}},
                    headers=hdr("customer"))
    assert r.status_code == 403


# ── 기본 = template, 항상 유효 ───────────────────────────────────────
def test_default_is_template_and_valid():
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "template"
    assert d["grounded"] is True
    assert d["model"] is None
    assert len(d["narrative"]) > 20
    # 서버가 숫자를 다시 계산했는가 — BBP 가 응답에 있고 설명이 그 성격을 말한다
    assert d["bbp_pct"] > 0
    assert "예측" in d["narrative"] or "번역" in d["narrative"]
    assert d["audit_id"]


def test_numbers_are_server_computed_not_client():
    """화면이 이상한 값을 보내도 서버는 trade 로 다시 계산한다(narrate 는 BBP 를 안 받는다)."""
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    d = r.json()
    # assess 와 같은 결정론 엔진 → 같은 BBP
    a = client.post("/v1/assess", json={"trade": TRADE}, headers=hdr()).json()
    assert abs(d["bbp_pct"] - a["bbp_pct"]) < 0.01


# ── LLM 경로 (monkeypatch — 실제 키 없이 검증) ───────────────────────
class _FakeLLM:
    MODEL = "claude-opus-4-8"
    def __init__(self, reply):
        self._reply = reply
        self.seen = None
    def narrate(self, system, user):
        self.seen = user
        return self._reply


def _with_llm(monkeypatch, reply):
    monkeypatch.setenv("AI_NARRATE_ENABLED", "1")
    fake = _FakeLLM(reply)
    monkeypatch.setattr(narrate_mod, "_llm", fake)
    return fake


def test_llm_grounded_reply_is_used(monkeypatch):
    # 사실에 있는 숫자만 쓴 좋은 설명 → source=llm
    good = "이 거래는 예산환율 이탈확률이 48.4%로 경계 구간입니다. 부담액은 보수적으로 최대 수준을 봅니다."
    _with_llm(monkeypatch, good)
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    d = r.json()
    # BBP 는 서버 계산값. 위 문장의 48.4 는 예시가 아니라 실제 BBP 와 맞춰야 grounded.
    # 실제 BBP 를 받아 문장을 구성해 다시 검증한다(엔진 값에 의존).
    bbp = d["bbp_pct"]
    _with_llm(monkeypatch, f"이 거래는 예산환율 이탈확률이 {bbp:.1f}%로 관리가 필요합니다. 방향 예측이 아니라 리스크 번역입니다.")
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    d = r.json()
    assert d["source"] == "llm"
    assert d["model"] == "claude-opus-4-8"
    assert f"{bbp:.1f}" in d["narrative"]


def test_llm_hallucinated_number_falls_back(monkeypatch):
    # 사실에 없는 구체 수치(1,250원 목표가) = 환각 → template 으로 폴백
    _with_llm(monkeypatch, "환율이 1250원까지 오를 것이며 목표가는 1,300원입니다.")
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    d = r.json()
    assert d["source"] == "template"
    assert "1250" not in d["narrative"] and "1,300" not in d["narrative"]


def test_llm_forbidden_phrase_falls_back(monkeypatch):
    _with_llm(monkeypatch, "지금 매수하세요. 수익을 보장합니다.")
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    d = r.json()
    assert d["source"] == "template"
    assert "보장" not in d["narrative"]


def test_llm_none_falls_back(monkeypatch):
    _with_llm(monkeypatch, None)   # 키 없음/네트워크 실패 시뮬
    r = client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    assert r.json()["source"] == "template"


# ── 데이터 최소화 — LLM 에 기업명·예산환율·금액을 보내지 않는가 ──────
def test_llm_facts_are_deidentified(monkeypatch):
    fake = _with_llm(monkeypatch, "이 거래는 리스크 번역 결과입니다.")
    client.post("/v1/narrate", json={"trade": TRADE}, headers=hdr())
    sent = fake.seen or ""
    assert "나래상사" not in sent          # 기업명 미전송
    assert "1500" not in sent and "1,500" not in sent   # 예산환율 미전송
    assert "400000" not in sent and "400,000" not in sent  # 금액 미전송
    # 리스크 사실은 들어간다
    assert "이탈확률" in sent and "부담액" in sent


# ── 가드레일 유닛 ────────────────────────────────────────────────────
def test_grounded_unit():
    facts = narrate_mod.build_facts(pos="import", bbp_pct=48.4, es_total_krw=10_680_000,
                                    horizon_bd=63, gauge_grade="주의", regime="기준일 · 평시",
                                    instrument="선물환", hedge_ratio=0.5)
    assert narrate_mod.grounded("이탈확률 48.4%, 부담액 10,680,000원 수준입니다.", facts) is True
    assert narrate_mod.grounded("목표가 1,250원까지 오릅니다.", facts) is False
    assert narrate_mod.grounded("수익을 보장합니다.", facts) is False
