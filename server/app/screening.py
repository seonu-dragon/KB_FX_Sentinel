"""제재 스크리닝 — OFAC / UN / EU 공개 리스트 실수집 + 퍼지 이름매칭.

■ 여기는 진짜다
원장·고시환율과 달리 제재 리스트는 **공개 데이터**다. 미 재무부 OFAC SDN, UN 안보리
통합 리스트, EU 통합 리스트 모두 무료로 배포된다. 그래서 이 모듈은 스텁이 아니라
실제 수집·파싱·매칭을 한다.

■ 정직성 규칙 (데모에서 이어받음)
리스트에 안 걸렸다고 '정상'이라고 쓰지 않는다. 미탐은 결백의 증명이 아니다:
  · 이름 표기 차이(음차·약어·한자)로 못 잡을 수 있다
  · 리스트는 시점 스냅샷이다
  · 지분 50% 규칙(OFAC 50% rule) 같은 간접 소유는 이름 매칭으로 안 잡힌다
그래서 verdict 는 "미탐(리스트 기준)" 이고, 최종 판단은 준법 부서로 넘긴다.

■ 오프라인 우선
`SANCTIONS_ALLOW_FETCH=1` 일 때만 네트워크를 탄다. 기본은 로컬 캐시만 읽는다 —
폐쇄망·오프라인에서도 서버가 떠야 하고, 테스트가 외부망에 의존하면 안 된다.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from rapidfuzz import fuzz, process

from .config import settings

# 공개 배포 주소 (수집 스크립트가 사용)
#
# 실측(2026-07-19):
#   OFAC_SDN         ✅ 19,169건 수집 성공
#   UN_CONSOLIDATED  ✅  1,010건 수집 성공
#   EU_CONSOLIDATED  ❌ 403 Forbidden — 이 엔드포인트는 더 이상 무인증 공개가 아니다.
#                       EU FSD 이용자 토큰이 필요하며, 신청 전까지는 적재되지 않는다.
#                       (없는 리스트를 있는 척하지 않는다 — 미적재는 응답에도 드러난다.)
SOURCES = {
    "OFAC_SDN": "https://www.treasury.gov/ofac/downloads/sdn.csv",
    "UN_CONSOLIDATED": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
    "EU_CONSOLIDATED": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content",
}

# 이 임계 미만은 버린다. 낮추면 오탐이 폭증해 준법 담당자가 알림을 무시하게 된다
# (alert fatigue — 이게 실제 AML 운영의 주된 실패 모드다).
MATCH_THRESHOLD = 86.0

_LEGAL_SUFFIX = re.compile(
    r"\b(co|co\.|ltd|ltd\.|limited|inc|inc\.|corp|corp\.|corporation|llc|plc|gmbh|"
    r"s\.a\.|sa|nv|bv|ag|pte|pty|jsc|ojsc|oao|zao|company|holdings?|group|trading)\b",
    re.IGNORECASE)


def normalize(name: str) -> str:
    """이름 정규화 — 법인격 접미사·구두점·중복공백 제거.

    'ACME TRADING CO., LTD.' 와 'Acme Trading' 이 같은 후보로 모이게 한다.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[^\w\s\-]", " ", s, flags=re.UNICODE)
    s = _LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class Entry:
    list_name: str
    name: str
    entity_id: str = ""
    programs: tuple = ()

    @property
    def norm(self) -> str:
        return normalize(self.name)


@dataclass
class Hit:
    list_name: str
    matched_name: str
    score: float
    entity_id: str = ""
    programs: list = None


class SanctionsIndex:
    """로컬 캐시에서 리스트를 읽어 매칭 인덱스를 만든다."""

    def __init__(self, directory: str):
        self.dir = directory
        self.entries: list[Entry] = []
        self.versions: dict[str, str] = {}
        self._choices: list[str] = []
        self.load()

    # ── 적재 ────────────────────────────────────────────────────
    def load(self) -> None:
        self.entries, self.versions = [], {}
        self._fp = self.fingerprint()
        if not os.path.isdir(self.dir):
            return
        for fn in sorted(os.listdir(self.dir)):
            path = os.path.join(self.dir, fn)
            if fn.endswith(".jsonl"):
                list_name = fn[:-6].upper()
                self._load_jsonl(path, list_name)
        # 동일 정규화 이름 중복 제거 — 같은 대상이 프로그램별로 여러 줄인 경우가 많다
        seen: set[tuple] = set()
        uniq: list[Entry] = []
        for e in self.entries:
            k = (e.list_name, e.norm)
            if e.norm and k not in seen:
                seen.add(k)
                uniq.append(e)
        self.entries = uniq
        self._choices = [e.norm for e in self.entries]

    def fingerprint(self) -> tuple:
        """디렉터리 상태 지문 — (파일명, mtime, 크기) 집합."""
        if not os.path.isdir(self.dir):
            return ()
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".jsonl"):
                p = os.path.join(self.dir, fn)
                st = os.stat(p)
                out.append((fn, st.st_mtime_ns, st.st_size))
        return tuple(out)

    def _load_jsonl(self, path: str, list_name: str) -> None:
        ts = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        self.versions[list_name] = ts.isoformat(timespec="seconds")
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                nm = (d.get("name") or "").strip()
                if not nm:
                    continue
                self.entries.append(Entry(
                    list_name=list_name, name=nm,
                    entity_id=str(d.get("id") or ""),
                    programs=tuple(d.get("programs") or ())))

    # ── 조회 ────────────────────────────────────────────────────
    def screen(self, party: str, limit: int = 10) -> list[Hit]:
        q = normalize(party)
        if not q or not self._choices:
            return []
        # token_set_ratio: 어순이 바뀌거나 토큰이 하나 더 붙어도 잡는다.
        raw = process.extract(q, self._choices, scorer=fuzz.token_set_ratio,
                              limit=limit * 3, score_cutoff=MATCH_THRESHOLD)
        hits: list[Hit] = []
        for _matched, score, idx in raw:
            e = self.entries[idx]
            hits.append(Hit(list_name=e.list_name, matched_name=e.name,
                            score=round(float(score), 1), entity_id=e.entity_id,
                            programs=list(e.programs)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    @property
    def loaded(self) -> bool:
        return bool(self.entries)

    @property
    def count(self) -> int:
        return len(self.entries)


_index: Optional[SanctionsIndex] = None


def get_index() -> SanctionsIndex:
    """인덱스 반환. 리스트 파일이 바뀌었으면 자동으로 다시 읽는다.

    이게 없으면 프로세스 시작 시점의 리스트로 영원히 판정한다. OFAC 는 거의 매일
    갱신되므로, 새 제재 대상이 추가돼도 서버를 재시작할 때까지 통과시키게 된다.
    (실제로 이 서버를 먼저 띄우고 나중에 리스트를 받았더니 계속 '미적재'로 응답했다.)

    비용은 요청당 stat 몇 번이다 — 20,089건을 매번 재적재하지 않고 지문이 바뀔 때만 읽는다.
    """
    global _index
    if _index is None:
        _index = SanctionsIndex(settings.sanctions_dir)
        return _index
    if _index.fingerprint() != getattr(_index, "_fp", ()):
        _index = SanctionsIndex(settings.sanctions_dir)
    return _index


def reload_index() -> SanctionsIndex:
    global _index
    _index = SanctionsIndex(settings.sanctions_dir)
    return _index


def verdict_for(hits: list[Hit], loaded: bool) -> str:
    """'정상' 이라고 절대 쓰지 않는다."""
    if not loaded:
        return "조회 불가 — 제재 리스트 미적재(준법 부서 수기 확인 필요)"
    if not hits:
        return ("리스트 기준 미탐 — 결백의 증명이 아님. "
                "표기 차이·간접 소유(50% 규칙)·시점 차이로 미탐 가능. 준법 확인 권장")
    top = hits[0].score
    if top >= 95:
        return "고유사 일치 — 거래 보류 후 준법·AML 심사 필수"
    return "유사 후보 발견 — 준법 부서 확인 전까지 진행 보류"
