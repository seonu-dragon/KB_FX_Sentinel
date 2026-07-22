"""제재 리스트 자동 재적재 회귀 테스트.

■ 왜 있는가 (실제로 겪은 일)
서버를 먼저 띄우고 나중에 `fetch_sanctions.py` 로 리스트를 받았더니, 서버는 계속
"조회 불가 — 미적재"로 응답했다. 인덱스를 최초 접근 때 만들고 다시 읽지 않았기 때문이다.

준법 시스템에서 이건 단순 불편이 아니다. OFAC SDN 은 거의 매일 갱신되므로,
재시작 전까지는 **새로 제재된 상대방을 계속 미탐으로 통과**시킨다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _write(d: str, fn: str, rows: list[dict]) -> None:
    with io.open(os.path.join(d, fn), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_index_picks_up_lists_added_after_startup(monkeypatch):
    """빈 디렉터리로 시작 → 나중에 리스트 도착 → 재시작 없이 탐지되어야 한다."""
    from app import screening
    from app.config import settings

    d = tempfile.mkdtemp(prefix="fxs_reload_")
    monkeypatch.setattr(settings, "sanctions_dir", d, raising=False)
    screening.reload_index()

    idx = screening.get_index()
    assert idx.loaded is False
    assert idx.screen("ACME TRADING") == []

    _write(d, "sample_testlist.jsonl",
           [{"name": "ACME TRADING CO., LTD.", "id": "T-1", "programs": ["TEST"]}])

    idx2 = screening.get_index()
    assert idx2.loaded is True, "리스트가 도착했는데도 재적재되지 않았다"
    assert idx2.screen("ACME TRADING"), "새 리스트로 조회되지 않는다"


def test_index_reloads_when_list_is_updated(monkeypatch):
    """기존 리스트에 새 대상이 추가되면 재시작 없이 반영되어야 한다."""
    from app import screening
    from app.config import settings

    d = tempfile.mkdtemp(prefix="fxs_reload2_")
    monkeypatch.setattr(settings, "sanctions_dir", d, raising=False)
    _write(d, "sample_testlist.jsonl", [{"name": "OLD ENTITY", "id": "1"}])
    screening.reload_index()

    assert screening.get_index().screen("NEWLY SANCTIONED CORP") == []

    time.sleep(0.01)      # mtime 해상도 여유
    _write(d, "sample_testlist.jsonl",
           [{"name": "OLD ENTITY", "id": "1"},
            {"name": "NEWLY SANCTIONED CORP", "id": "2"}])

    hits = screening.get_index().screen("Newly Sanctioned Corp")
    assert hits, "리스트 갱신이 반영되지 않았다 — 새 제재 대상을 계속 미탐 처리한다"


def test_unchanged_directory_does_not_rebuild(monkeypatch):
    """지문이 같으면 재적재하지 않는다(요청마다 2만건 재파싱 방지)."""
    from app import screening
    from app.config import settings

    d = tempfile.mkdtemp(prefix="fxs_reload3_")
    monkeypatch.setattr(settings, "sanctions_dir", d, raising=False)
    _write(d, "sample_testlist.jsonl", [{"name": "STATIC ENTITY", "id": "1"}])
    screening.reload_index()

    a = screening.get_index()
    b = screening.get_index()
    assert a is b, "변경이 없는데 인덱스를 다시 만들었다"
