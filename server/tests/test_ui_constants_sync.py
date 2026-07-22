"""데모 JS ↔ 서버 Python 상수 동기화 검사 (Chrome 불필요).

■ 왜 이 테스트가 따로 필요한가
`test_elig_parity.py` 는 실제 Chrome 으로 전 조합을 대조하는 진짜 파리티 테스트다. 하지만
Chrome 이 없는 환경(CI 컨테이너·리눅스 샌드박스)에서는 skip 된다. 그러면 그 환경에서는
**JS 와 Python 이 갈라져도 아무도 모른다.**

로직 전체를 브라우저 없이 대조하는 건 JS 평가기가 필요해서 과하다. 대신 **가장 현실적인
drift 원인인 상수**(임계·문항 키·상품 분류)를 정규식으로 뽑아 비교한다. 로직을 고칠 때는
보통 상수도 같이 건드리므로, 이 검사만으로도 대부분의 drift 를 잡는다.

Chrome 있는 환경에서는 이 테스트 + 전 조합 파리티가 함께 돈다 — 서로 대체가 아니라 보완이다.
"""
from __future__ import annotations

import io
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import suitability as S          # noqa: E402

HTML_PATH = os.path.join(KB, "FX_Sentinel_demo_ui.html")


@pytest.fixture(scope="module")
def html() -> str:
    if not os.path.exists(HTML_PATH):
        pytest.skip("데모 HTML 없음")
    return io.open(HTML_PATH, encoding="utf-8").read()


def _js_array(html: str, name: str) -> list[str]:
    """`const NAME=["a","b"];` 에서 문자열 원소를 뽑는다."""
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\]", html, re.S)
    assert m, f"데모 HTML 에서 {name} 을 찾지 못했습니다"
    return re.findall(r'"([^"]+)"', m.group(1))


def _js_num(html: str, name: str) -> float:
    m = re.search(r"(?:const|let|var)\s+[^;]*?\b" + re.escape(name) + r"\s*=\s*(-?[\d.]+)", html)
    assert m, f"데모 HTML 에서 {name} 을 찾지 못했습니다"
    return float(m.group(1))


# ── 상품 분류 ────────────────────────────────────────────────────────
def test_derivative_keys_match(html):
    """파생 분류가 갈라지면 한쪽에서만 적정성이 걸린다."""
    assert _js_array(html, "SUIT_DERIV") == list(S.DERIVATIVE_KEYS)


def test_credit_keys_match(html):
    assert _js_array(html, "SUIT_CREDIT") == list(S.CREDIT_KEYS)


def test_insurance_keys_match(html):
    """보험 분류가 갈라지면 무여신 SME 의 대안이 한쪽에서 사라진다."""
    assert _js_array(html, "SUIT_INSUR") == list(S.INSURANCE_KEYS)


def test_knockout_keys_match(html):
    """필수항목이 갈라지면 화면과 서버가 정반대 판정을 낸다."""
    assert _js_array(html, "SUIT_KNOCKOUT") == list(S.KNOCKOUT_KEYS)


# ── 임계 ─────────────────────────────────────────────────────────────
def test_suit_thresholds_match(html):
    assert _js_num(html, "SUIT_PASS") == S.SUIT_PASS
    assert _js_num(html, "SUIT_CAUTION") == S.SUIT_CAUTION


def test_kickback_window_matches(html):
    assert _js_num(html, "KICKBACK_WINDOW_DAYS") == S.KICKBACK_WINDOW_DAYS


# ── 문항 문구 ────────────────────────────────────────────────────────
def test_suit_questions_present_in_ui(html):
    """문항 문구가 다르면 같은 걸 물어본다고 말할 수 없다.

    고객이 화면에서 본 질문과 감사로그에 남는 질문이 달라지면, 나중에 '무엇을 확인했나'를
    증명할 수 없다.
    """
    r = S.assess_suitability(S.ConsumerProfile(), 1_000_000)
    for item in r["items"]:
        assert item["question"] in html, f"UI 에 없는 문항: {item['question']}"


# ── 정직성 문구 ──────────────────────────────────────────────────────
def test_ui_declares_demo_scoring(html):
    """평가표가 KB 공식이 아니라는 고지가 화면에 있어야 한다."""
    assert "KB 승인 평가표가 아닙니다" in html or "승인된 적정성 평가표가 아닙니다" in html


def test_ui_declares_no_prediction_in_scenario(html):
    """시나리오가 예측으로 읽히면 이 프로젝트의 무예측 전제가 깨진다."""
    assert "예측이 아닙니다" in html


def test_ui_scenario_defers_rate_to_rm(html):
    """계약환율·밴드가를 화면이 만들지 않는다는 고지(rate_policy 규율)."""
    assert "RM 견적 사항입니다" in html
