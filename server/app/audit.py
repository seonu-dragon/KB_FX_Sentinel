"""감사로그 — append-only + 해시체인.

데모는 감사로그를 localStorage 에 뒀다. 그건 기기에 종속되고, 사용자가 지울 수 있고,
지운 흔적도 안 남는다. 금융 감사 요건으로는 성립하지 않는다.

여기서는:
  · append-only  — UPDATE/DELETE 를 코드 경로에서 제공하지 않는다
  · 해시체인      — 각 레코드가 이전 레코드의 해시를 품는다. 중간을 고치면 이후가 전부 깨진다
  · 버전 스탬프   — 엔진 버전·모델 버전·시각을 함께 남긴다("그때 뭐가 그렇게 판정했나")

한계(정직하게): SQLite 파일 자체를 통째로 교체하는 공격은 이걸로 못 막는다.
운영에서는 WORM 스토리지 또는 외부 앵커링(주기적 체인 헤드 공증)이 필요하다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_LOCK = threading.Lock()

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  audit_id   TEXT    NOT NULL UNIQUE,
  ts         TEXT    NOT NULL,
  actor      TEXT    NOT NULL,
  role       TEXT    NOT NULL,
  event      TEXT    NOT NULL,
  payload    TEXT    NOT NULL,
  engine_ver TEXT    NOT NULL,
  prev_hash  TEXT    NOT NULL,
  hash       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts    ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit(event);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canon(obj: Any) -> str:
    """정규 직렬화 — 키 순서가 흔들리면 해시가 흔들린다."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(prev_hash: str, ts: str, actor: str, role: str,
            event: str, payload: str, engine_ver: str) -> str:
    h = hashlib.sha256()
    for part in (prev_hash, ts, actor, role, event, payload, engine_ver):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")            # 필드 구분자 — 연접 모호성 제거
    return h.hexdigest()


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── 쓰기 ────────────────────────────────────────────────────
    def append(self, *, actor: str, role: str, event: str,
               payload: dict, engine_ver: str) -> str:
        """레코드 추가 후 audit_id 반환. 실패 시 예외(조용히 삼키지 않는다)."""
        audit_id = uuid.uuid4().hex
        ts = _now()
        body = _canon(payload)
        with _LOCK:
            cur = self._conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1")
            row = cur.fetchone()
            prev = row[0] if row else GENESIS
            digest = _digest(prev, ts, actor, role, event, body, engine_ver)
            self._conn.execute(
                "INSERT INTO audit (audit_id, ts, actor, role, event, payload,"
                " engine_ver, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (audit_id, ts, actor, role, event, body, engine_ver, prev, digest))
            self._conn.commit()
        return audit_id

    # ── 읽기 ────────────────────────────────────────────────────
    def get(self, audit_id: str) -> Optional[dict]:
        cur = self._conn.execute(
            "SELECT seq, audit_id, ts, actor, role, event, payload, engine_ver,"
            " prev_hash, hash FROM audit WHERE audit_id=?", (audit_id,))
        row = cur.fetchone()
        return self._row(row) if row else None

    def tail(self, limit: int = 100, actor: Optional[str] = None) -> list[dict]:
        q = ("SELECT seq, audit_id, ts, actor, role, event, payload, engine_ver,"
             " prev_hash, hash FROM audit")
        args: tuple = ()
        if actor:
            q += " WHERE actor=?"
            args = (actor,)
        q += " ORDER BY seq DESC LIMIT ?"
        args = args + (limit,)
        return [self._row(r) for r in self._conn.execute(q, args)]

    @staticmethod
    def _row(r) -> dict:
        return {"seq": r[0], "audit_id": r[1], "ts": r[2], "actor": r[3], "role": r[4],
                "event": r[5], "payload": json.loads(r[6]), "engine_version": r[7],
                "prev_hash": r[8], "hash": r[9]}

    # ── 검증 ────────────────────────────────────────────────────
    def verify(self) -> dict:
        """체인 전체 재계산. 위변조가 있으면 첫 깨진 지점을 돌려준다."""
        prev = GENESIS
        n = 0
        cur = self._conn.execute(
            "SELECT seq, audit_id, ts, actor, role, event, payload, engine_ver,"
            " prev_hash, hash FROM audit ORDER BY seq ASC")
        for r in cur:
            seq, audit_id, ts, actor, role, event, payload, engine_ver, prev_hash, hash_ = r
            if prev_hash != prev:
                return {"ok": False, "checked": n, "broken_at_seq": seq,
                        "reason": "prev_hash 불일치 — 이전 레코드가 삭제·변경됨"}
            expect = _digest(prev, ts, actor, role, event, payload, engine_ver)
            if expect != hash_:
                return {"ok": False, "checked": n, "broken_at_seq": seq,
                        "reason": "레코드 내용이 해시와 불일치 — 사후 변경됨"}
            prev = hash_
            n += 1
        return {"ok": True, "checked": n, "head": prev}

    def close(self) -> None:
        self._conn.close()


_log: Optional[AuditLog] = None


def get_log() -> AuditLog:
    global _log
    if _log is None:
        from .config import settings
        _log = AuditLog(settings.audit_db)
    return _log
