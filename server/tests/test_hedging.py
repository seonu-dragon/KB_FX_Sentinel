"""레이어드 헤지 — 목표비율 불변·스케줄 불변식·정직성 문구."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))
for p in (ROOT, KB):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.hedging import build_schedule                          # noqa: E402
from fx_sentinel.bbp import hedge_ratio_from                    # noqa: E402


# ── 목표비율을 건드리지 않는가 (가장 중요) ──────────────────────────
def test_target_ratio_is_never_altered():
    """분할은 실행 방식이지 정책이 아니다 — 엔진이 준 목표를 그대로 낸다.

    초기 구현은 LossAlert 를 25% 단위로 재매핑했다가, LossAlert=0.15 에서
    엔진은 50% 헤지·새 로직은 0% 헤지를 권고하는 충돌을 만들었다.
    이 테스트가 그 회귀를 막는다.
    """
    for la in [i / 200 for i in range(0, 120)]:
        mvp = hedge_ratio_from(la)
        s = build_schedule(amount=500_000, horizon_bd=63, target_ratio=mvp)
        assert s["target_ratio"] == mvp, f"la={la}: 목표비율이 {mvp}→{s['target_ratio']} 로 변경됨"


def test_engine_ratios_produce_expected_tranche_counts():
    assert len(build_schedule(500_000, 63, 0.0)["tranches"]) == 0
    assert len(build_schedule(500_000, 63, 0.5)["tranches"]) == 2
    assert len(build_schedule(500_000, 63, 1.0)["tranches"]) == 4


# ── 스케줄 불변식 ───────────────────────────────────────────────────
def test_tranches_sum_to_target():
    for target in (0.5, 1.0):
        s = build_schedule(amount=500_000, horizon_bd=63, target_ratio=target)
        assert sum(t["ratio"] for t in s["tranches"]) == pytest.approx(target)
        assert sum(t["amount"] for t in s["tranches"]) == pytest.approx(500_000 * target, rel=1e-6)


def test_partial_target_leaves_unhedged_remainder():
    s = build_schedule(amount=400_000, horizon_bd=63, target_ratio=0.5)
    assert s["unhedged_ratio"] == pytest.approx(0.5)


def test_all_tranches_execute_before_maturity():
    s = build_schedule(amount=500_000, horizon_bd=63, target_ratio=1.0)
    for t in s["tranches"]:
        assert 0 <= t["execute_at_bd"] < 63
        assert t["remaining_bd"] >= 1


def test_execution_times_are_strictly_increasing():
    s = build_schedule(amount=500_000, horizon_bd=126, target_ratio=1.0)
    times = [t["execute_at_bd"] for t in s["tranches"]]
    assert times == sorted(times)
    assert len(set(times)) == len(times), "같은 날 두 회차가 겹친다"


def test_cumulative_reaches_target():
    s = build_schedule(amount=500_000, horizon_bd=63, target_ratio=1.0)
    assert s["tranches"][-1]["cumulative_ratio"] == pytest.approx(1.0)


def test_zero_target_yields_no_tranches():
    s = build_schedule(amount=500_000, horizon_bd=63, target_ratio=0.0)
    assert s["tranches"] == []
    assert s["unhedged_ratio"] == 1.0


def test_short_horizon_does_not_crash():
    s = build_schedule(amount=100_000, horizon_bd=1, target_ratio=1.0)
    assert s["tranches"]
    for t in s["tranches"]:
        assert t["remaining_bd"] >= 1


def test_nan_and_out_of_range_are_safe():
    assert build_schedule(500_000, 63, float("nan"))["tranches"] == []
    assert build_schedule(500_000, 63, -1)["tranches"] == []
    assert build_schedule(500_000, 63, 5)["target_ratio"] == 1.0


# ── 정직성 ──────────────────────────────────────────────────────────
def test_note_denies_expected_return_improvement():
    """'분할하면 더 유리한 환율' 은 예측 알파 주장이다 — 하지 않는다."""
    note = build_schedule(500_000, 63, 1.0)["note"]
    assert "기대손익을 개선하지 않" in note
    for banned in ("유리한 환율", "더 좋은", "수익", "이익을"):
        assert banned not in note


def test_note_says_target_is_not_changed_by_layering():
    note = build_schedule(500_000, 63, 0.5)["note"]
    assert "분할이 바꾸지 않습니다" in note


def test_note_discloses_unhedged_exposure_and_cost():
    note = build_schedule(500_000, 63, 0.5)["note"]
    assert "노출" in note
    assert "스프레드" in note or "수수료" in note
