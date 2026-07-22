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

from app import limits as LIM             # noqa: E402
from app import mtm as MTM                # noqa: E402
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


# ── 여신 한도 (F3) ───────────────────────────────────────────────────
def test_ccf_bands_match(html):
    """신용환산율이 갈라지면 화면과 서버가 다른 여신 계상액을 낸다."""
    m = re.search(r"const\s+CCF_BANDS\s*=\s*\[(.*?)\];", html, re.S)
    assert m, "데모 HTML 에서 CCF_BANDS 를 찾지 못했습니다"
    js = [tuple(float(x) for x in row.split(","))
          for row in re.findall(r"\[([^\]]+)\]", m.group(1))]
    py = [(float(a), float(b), float(c)) for a, b, c in LIM.CCF_BANDS]
    assert js == py


def test_util_warn_matches(html):
    assert _js_num(html, "UTIL_WARN") == LIM.UTIL_WARN


def test_ui_explains_notional_vs_cee(html):
    """소진은 명목, 여신 계상은 CEE — 두 숫자의 구분이 화면에 있어야 한다."""
    assert "명목 전액이 아니라 신용환산액" in html
    assert "KB 여신 계상액" in html


def test_ui_declares_limit_self_reported(html):
    """한도가 원장 조회값이 아님을 화면이 밝힌다."""
    assert "고객 자기신고" in html and "KB 원장 조회값이 아닙니다" in html


# ── 체결 후 MTM (F2) ─────────────────────────────────────────────────
def test_margin_call_pct_matches(html):
    """트리거가 갈라지면 화면과 서버가 다른 담보 요구를 낸다."""
    assert _js_num(html, "MARGIN_CALL_PCT") == MTM.MARGIN_CALL_PCT


def test_sigma_steps_match(html):
    m = re.search(r"const\s+SIGMA_STEPS\s*=\s*\[([^\]]+)\]", html)
    assert m, "데모 HTML 에서 SIGMA_STEPS 를 찾지 못했습니다"
    js = tuple(float(x) for x in m.group(1).split(","))
    assert js == MTM.SIGMA_STEPS


def test_ui_states_adverse_direction_per_position(html):
    """포지션마다 아픈 방향이 다르다는 사실이 화면 문구에 있어야 한다.

    "킹달러 = 위험"처럼 방향을 고정해 말하면 수입기업에게 거짓말이 된다.
    """
    assert "원화 약세(환율 상승)" in html and "원화 강세(환율 하락)" in html
    assert "매수 선물환은 계약환율에 사야 하므로" in html


def test_ui_separates_loss_tolerance_from_cash(html):
    """감내 가능 '손실'과 즉시 동원 '현금'이 별도 입력이어야 한다 — KIKO 의 실제 구도."""
    assert 'id="in-tol"' in html and 'id="in-cashbuf"' in html


def test_ui_declares_mtm_is_approximation(html):
    assert "현물 차이 기준 근사" in html


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
