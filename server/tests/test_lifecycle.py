"""라이프사이클 상태머신 — 전이 규칙·권한·실수요 가드."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.lifecycle import (CANCELLED, CONTRACTED, DOCS_REVIEW, DRAFT,  # noqa: E402
                           LIMIT_CHECK, ROLLED_OVER, SETTLED, SUBMITTED,
                           Deal, TransitionError)


def deal(amount=500_000.0):
    return Deal(deal_id="D1", owner="u1", real_demand_amount=amount)


def advance(d, path, role="rm"):
    for st in path:
        d.transition(st, actor="u1", role=role)


# ── 전이 규칙 ───────────────────────────────────────────────────────
def test_happy_path():
    d = deal()
    advance(d, [SUBMITTED, DOCS_REVIEW, LIMIT_CHECK, CONTRACTED, SETTLED])
    assert d.state == SETTLED


def test_cannot_skip_to_contracted():
    """서류·한도를 건너뛰고 체결로 가면 실수요 확인 없이 헤지가 나간다."""
    d = deal()
    with pytest.raises(TransitionError):
        d.transition(CONTRACTED, actor="u1", role="rm")


def test_cannot_leave_terminal_state():
    d = deal()
    advance(d, [SUBMITTED, DOCS_REVIEW, LIMIT_CHECK, CONTRACTED, SETTLED])
    with pytest.raises(TransitionError) as e:
        d.transition(ROLLED_OVER, actor="u1", role="rm")
    assert "종료된 건" in str(e.value)


def test_cancelled_is_terminal():
    d = deal()
    d.transition(CANCELLED, actor="u1", role="customer")
    with pytest.raises(TransitionError):
        d.transition(SUBMITTED, actor="u1", role="customer")


def test_docs_can_be_returned_to_submitted():
    """서류 미비 반려 경로가 있어야 실무가 돈다."""
    d = deal()
    advance(d, [SUBMITTED, DOCS_REVIEW])
    d.transition(SUBMITTED, actor="rm1", role="rm")
    assert d.state == SUBMITTED


def test_unknown_state_rejected():
    d = deal()
    with pytest.raises(TransitionError):
        d.transition("teleported", actor="u1", role="admin")


def test_history_records_every_transition():
    d = deal()
    advance(d, [SUBMITTED, DOCS_REVIEW])
    assert len(d.history) == 2
    assert d.history[0]["from"] == DRAFT and d.history[0]["to"] == SUBMITTED


# ── 권한 ────────────────────────────────────────────────────────────
def test_customer_cannot_contract():
    """체결은 고객이 혼자 누를 수 있는 버튼이 아니다."""
    d = deal()
    advance(d, [SUBMITTED, DOCS_REVIEW, LIMIT_CHECK])
    with pytest.raises(TransitionError) as e:
        d.transition(CONTRACTED, actor="u1", role="customer")
    assert "권한" in str(e.value)


def test_customer_can_submit_and_cancel():
    d = deal()
    d.transition(SUBMITTED, actor="u1", role="customer")
    d.transition(CANCELLED, actor="u1", role="customer")
    assert d.state == CANCELLED


def test_compliance_can_review_docs():
    d = deal()
    d.transition(SUBMITTED, actor="u1", role="customer")
    d.transition(DOCS_REVIEW, actor="c1", role="compliance")
    assert d.state == DOCS_REVIEW


# ── 실수요 가드 (외국환거래법) ──────────────────────────────────────
def test_hedge_cannot_exceed_real_demand():
    d = deal(500_000)
    d.contract_hedge(300_000, actor="rm1", role="rm")
    with pytest.raises(TransitionError) as e:
        d.contract_hedge(300_000, actor="rm1", role="rm")
    assert "실수요 초과" in str(e.value)


def test_hedge_exactly_at_real_demand_is_allowed():
    d = deal(500_000)
    d.contract_hedge(500_000, actor="rm1", role="rm")
    assert d.hedged_amount == 500_000
    assert d.unhedged_amount == 0


def test_unwind_cannot_exceed_hedged():
    """보유분을 넘겨 해지하면 실수요 없는 반대 포지션(투기)이 된다."""
    d = deal(500_000)
    d.contract_hedge(200_000, actor="rm1", role="rm")
    with pytest.raises(TransitionError) as e:
        d.unwind_hedge(300_000, actor="rm1", role="rm")
    assert "반대 포지션" in str(e.value)


def test_unwind_reduces_balance():
    d = deal(500_000)
    d.contract_hedge(400_000, actor="rm1", role="rm")
    d.unwind_hedge(150_000, actor="rm1", role="rm")
    assert d.hedged_amount == 250_000
    assert d.unhedged_amount == 250_000


def test_reducing_real_demand_warns_about_excess_hedge():
    """원 거래가 줄면 기존 헤지가 실수요를 넘게 된다 — 조용히 두면 안 된다."""
    d = deal(500_000)
    d.contract_hedge(500_000, actor="rm1", role="rm")
    warns = d.reduce_real_demand(200_000, actor="rm1", role="rm")
    assert warns and "해지가 필요" in warns[0]


def test_reducing_real_demand_without_excess_is_quiet():
    d = deal(500_000)
    d.contract_hedge(100_000, actor="rm1", role="rm")
    assert d.reduce_real_demand(200_000, actor="rm1", role="rm") == []


def test_zero_or_negative_amounts_rejected():
    d = deal(500_000)
    with pytest.raises(TransitionError):
        d.contract_hedge(0, actor="rm1", role="rm")
    with pytest.raises(TransitionError):
        d.unwind_hedge(-5, actor="rm1", role="rm")
