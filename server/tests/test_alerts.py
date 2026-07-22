"""알림 규칙 엔진 — 발화 조건·쿨다운·정직성."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.alerts import (DELIVERY_NOTE, Rule, RuleStore, Signal,   # noqa: E402
                        evaluate)


def sig(ref="L1", hbd=63, bbp=40.0, hr=0.5, grade="정상", name="테스트"):
    return Signal(ref=ref, horizon_bd=hbd, bbp_pct=bbp, hedge_ratio=hr,
                  gauge_grade=grade, name=name)


def rule(kind, **kw):
    r = Rule(rule_id="r1", owner="u1", kind=kind, **kw)
    r.validate()
    return r


# ── 발화 조건 ───────────────────────────────────────────────────────
def test_maturity_fires_inside_window():
    assert evaluate([rule("maturity", days_before=7)], [sig(hbd=5)])
    assert not evaluate([rule("maturity", days_before=7)], [sig(hbd=30)])


def test_maturity_boundary_is_inclusive():
    assert evaluate([rule("maturity", days_before=7)], [sig(hbd=7)])


def test_bbp_above_is_strict():
    """'초과' 라고 했으면 같은 값에서는 울리지 않아야 한다."""
    assert not evaluate([rule("bbp_above", threshold_pct=50)], [sig(bbp=50.0)])
    assert evaluate([rule("bbp_above", threshold_pct=50)], [sig(bbp=50.1)])


def test_regime_uses_grade_order():
    r = rule("regime", min_grade="경계")
    assert not evaluate([r], [sig(grade="주의")])
    r.last_fired.clear()
    assert evaluate([r], [sig(grade="경계")])
    r.last_fired.clear()
    assert evaluate([r], [sig(grade="심각")])


def test_unhedged_needs_both_conditions():
    """만기가 가깝고 + 헤지가 모자랄 때만. 하나만으로는 안 울린다."""
    r = rule("unhedged", days_before=10, min_hedge_ratio=0.5)
    assert not evaluate([r], [sig(hbd=60, hr=0.0)])      # 만기 멀다
    r.last_fired.clear()
    assert not evaluate([r], [sig(hbd=5, hr=1.0)])       # 헤지 충분
    r.last_fired.clear()
    assert evaluate([r], [sig(hbd=5, hr=0.0)])


def test_disabled_rule_does_not_fire():
    r = rule("maturity", days_before=7)
    r.enabled = False
    assert not evaluate([r], [sig(hbd=1)])


# ── 쿨다운 (알림 피로 방지) ─────────────────────────────────────────
def test_cooldown_suppresses_repeat():
    r = rule("maturity", days_before=7, cooldown_days=3)
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    assert evaluate([r], [sig(hbd=5)], now=now)
    assert not evaluate([r], [sig(hbd=5)], now=now + timedelta(days=1))
    assert not evaluate([r], [sig(hbd=5)], now=now + timedelta(days=2, hours=23))


def test_cooldown_expires():
    r = rule("maturity", days_before=7, cooldown_days=3)
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    evaluate([r], [sig(hbd=5)], now=now)
    assert evaluate([r], [sig(hbd=5)], now=now + timedelta(days=3, seconds=1))


def test_cooldown_is_per_trade():
    """한 거래가 울렸다고 다른 거래가 막히면 안 된다."""
    r = rule("maturity", days_before=7, cooldown_days=3)
    now = datetime(2026, 7, 19, tzinfo=timezone.utc)
    evaluate([r], [sig(ref="L1", hbd=5)], now=now)
    assert evaluate([r], [sig(ref="L2", hbd=5)], now=now)


# ── 검증 ────────────────────────────────────────────────────────────
def test_invalid_kind_rejected():
    with pytest.raises(ValueError):
        Rule(rule_id="x", owner="u", kind="telepathy").validate()


def test_invalid_threshold_rejected():
    with pytest.raises(ValueError):
        Rule(rule_id="x", owner="u", kind="bbp_above", threshold_pct=150).validate()


def test_negative_cooldown_rejected():
    with pytest.raises(ValueError):
        Rule(rule_id="x", owner="u", kind="maturity", cooldown_days=-1).validate()


# ── 저장소 격리 ─────────────────────────────────────────────────────
def test_rules_are_per_owner():
    s = RuleStore()
    s.add("alice", "maturity")
    s.add("bob", "maturity")
    assert len(s.list("alice")) == 1
    assert len(s.list("bob")) == 1


def test_cannot_delete_others_rule():
    s = RuleStore()
    r = s.add("alice", "maturity")
    assert s.delete(r.rule_id, "bob") is False
    assert s.delete(r.rule_id, "alice") is True


# ── 정직성 ──────────────────────────────────────────────────────────
def test_alert_admits_it_is_not_delivered():
    a = evaluate([rule("maturity", days_before=7)], [sig(hbd=5)])[0]
    assert "전달되지 않습니다" in a.delivery
    assert "미연동" in DELIVERY_NOTE
