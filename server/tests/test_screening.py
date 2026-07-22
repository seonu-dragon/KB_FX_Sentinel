"""제재 스크리닝 — 정규화·퍼지매칭·정직성 문구.

네트워크를 타지 않는다. 가상 샘플 리스트를 임시 디렉터리에 만들어 검증한다
(실제 OFAC/UN/EU 수집은 scripts/fetch_sanctions.py 로 명시 실행).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.screening import SanctionsIndex, normalize, verdict_for   # noqa: E402

FIXTURE = [
    {"name": "ACME TRADING CO., LTD.", "id": "T-1", "programs": ["TESTPROG"]},
    {"name": "Vostok Machine Industries OAO", "id": "T-2", "programs": ["TESTPROG"]},
    {"name": "Zenith Shipping Group Limited", "id": "T-3", "programs": ["TESTPROG"]},
    {"name": "Al-Hidaya Foundation", "id": "T-4", "programs": ["TESTPROG"]},
]


@pytest.fixture(scope="module")
def index():
    d = tempfile.mkdtemp(prefix="fxs_sanc_")
    with io.open(os.path.join(d, "sample_testlist.jsonl"), "w", encoding="utf-8") as f:
        for r in FIXTURE:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return SanctionsIndex(d)


# ── 정규화 ──────────────────────────────────────────────────────────
def test_normalize_strips_legal_suffixes():
    assert normalize("ACME TRADING CO., LTD.") == normalize("Acme Trading")


def test_normalize_is_case_and_punctuation_insensitive():
    assert normalize("Zenith  Shipping,  Group!") == normalize("zenith shipping group")


# ── 매칭 ────────────────────────────────────────────────────────────
def test_exact_name_hits(index):
    hits = index.screen("ACME TRADING CO., LTD.")
    assert hits and hits[0].matched_name.startswith("ACME")
    assert hits[0].score >= 95


def test_legal_form_variation_still_hits(index):
    """'Co., Ltd.' 유무로 놓치면 스크리닝이 무의미하다."""
    hits = index.screen("Acme Trading")
    assert hits and hits[0].matched_name.startswith("ACME")


def test_word_order_variation_hits(index):
    hits = index.screen("Shipping Zenith Group")
    assert hits and "Zenith" in hits[0].matched_name


def test_unrelated_name_does_not_hit(index):
    assert index.screen("한빛정밀 주식회사") == []


def test_threshold_blocks_weak_similarity(index):
    """오탐이 쏟아지면 준법 담당자가 알림을 무시하게 된다(alert fatigue)."""
    assert index.screen("Zen Cafe") == []


# ── 정직성 ──────────────────────────────────────────────────────────
def test_verdict_never_says_normal_on_miss():
    v = verdict_for([], loaded=True)
    assert "정상" not in v
    assert "결백의 증명이 아님" in v


def test_verdict_when_list_missing_is_not_a_pass():
    """리스트가 없으면 '통과'가 아니라 '조회 불가'다."""
    v = verdict_for([], loaded=False)
    assert "조회 불가" in v


def test_high_score_demands_hold(index):
    hits = index.screen("Vostok Machine Industries OAO")
    v = verdict_for(hits, loaded=True)
    assert "보류" in v


def test_empty_index_is_not_silently_ok():
    empty = SanctionsIndex(tempfile.mkdtemp(prefix="fxs_empty_"))
    assert empty.loaded is False
    assert "조회 불가" in verdict_for(empty.screen("ACME"), loaded=empty.loaded)
