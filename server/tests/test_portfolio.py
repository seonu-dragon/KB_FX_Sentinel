"""포트폴리오 집계 — 네팅 엄격성·합산 근거·만기 캘린더."""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.portfolio import Leg, aggregate, bucket_of      # noqa: E402


def leg(ref, pos, amt, hbd, bbp=0.5, es=1_000_000, cur="USD"):
    return Leg(ref=ref, pos=pos, currency=cur, amount=amt,
               horizon_bd=hbd, bbp=bbp, es_total_krw=es)


# ── 버킷 ────────────────────────────────────────────────────────────
def test_bucket_boundaries():
    assert bucket_of(0) == "0-1M"
    assert bucket_of(21) == "0-1M"
    assert bucket_of(22) == "1-3M"
    assert bucket_of(63) == "1-3M"
    assert bucket_of(64) == "3-6M"
    assert bucket_of(126) == "3-6M"
    assert bucket_of(127) == "6M+"
    assert bucket_of(9999) == "6M+"


# ── 네팅 ────────────────────────────────────────────────────────────
def test_same_bucket_offsets():
    """같은 통화·같은 만기 구간이면 상계된다."""
    a = aggregate([leg("A", "export", 500_000, 63), leg("B", "import", 300_000, 63)])
    c = a["by_currency"][0]
    assert c["offset_within_bucket"] == 300_000
    assert c["net_exposure"] == 200_000
    assert c["timing_mismatch"] == 0


def test_different_bucket_does_not_offset():
    """3월에 받을 달러로 9월 지급을 상계했다고 하면 그 사이 위험이 사라진 것처럼 보인다."""
    a = aggregate([leg("A", "export", 500_000, 21),      # 0-1M
                   leg("B", "import", 500_000, 126)])    # 3-6M
    c = a["by_currency"][0]
    assert c["offset_within_bucket"] == 0, "만기가 다른데 상계됐다"
    assert c["net_exposure"] == 1_000_000
    assert c["timing_mismatch"] == 500_000, "시점 불일치가 드러나야 한다"


def test_timing_mismatch_is_disclosed_in_notes():
    a = aggregate([leg("A", "export", 500_000, 21), leg("B", "import", 500_000, 126)])
    assert any("만기" in n and "남습니다" in n for n in a["notes"])


def test_different_currency_never_offsets():
    a = aggregate([leg("A", "export", 500_000, 63, cur="USD"),
                   leg("B", "import", 500_000, 63, cur="EUR")])
    for c in a["by_currency"]:
        assert c["offset_within_bucket"] == 0, "다른 통화가 상계됐다"


def test_gross_is_sum_of_all_legs():
    a = aggregate([leg("A", "export", 500_000, 63), leg("B", "import", 300_000, 63)])
    assert a["gross_exposure"]["USD"] == 800_000


# ── 합산 ────────────────────────────────────────────────────────────
def test_es_is_summed_linearly():
    """E[ΣX]=ΣE[X] — 상관과 무관하게 성립한다."""
    a = aggregate([leg("A", "export", 100_000, 63, es=1_000_000),
                   leg("B", "export", 100_000, 63, es=2_500_000)])
    assert a["total_es_krw"] == 3_500_000


def test_weighted_bbp_is_amount_weighted():
    a = aggregate([leg("A", "export", 900_000, 63, bbp=0.10),
                   leg("B", "export", 100_000, 63, bbp=1.00)])
    # (0.9*0.10 + 0.1*1.00) = 0.19
    assert a["weighted_bbp_pct"] == pytest.approx(19.0, abs=0.05)


def test_weighted_bbp_is_not_probability_of_any_breach():
    """1−Π(1−pᵢ) 였다면 두 건 각 50% 에서 75% 가 나온다. 우리는 50% 여야 한다."""
    a = aggregate([leg("A", "export", 100_000, 63, bbp=0.5),
                   leg("B", "export", 100_000, 63, bbp=0.5)])
    assert a["weighted_bbp_pct"] == pytest.approx(50.0, abs=0.05)


def test_notes_explain_the_definition():
    a = aggregate([leg("A", "export", 100_000, 63)])
    joined = " ".join(a["notes"])
    assert "한 건이라도" in joined and "선형성" in joined


# ── 만기 캘린더 ─────────────────────────────────────────────────────
def test_calendar_groups_by_bucket():
    a = aggregate([leg("A", "export", 100_000, 10),
                   leg("B", "export", 200_000, 30),
                   leg("C", "export", 300_000, 40)])
    buckets = {c["bucket"]: c for c in a["maturity_calendar"]}
    assert buckets["0-1M"]["trade_count"] == 1
    assert buckets["1-3M"]["trade_count"] == 2
    assert buckets["1-3M"]["amount"] == 500_000


def test_empty_portfolio_is_safe():
    a = aggregate([])
    assert a["trade_count"] == 0
    assert a["total_es_krw"] == 0
    assert a["notes"]
