"""공개 제재 리스트 수집 → 로컬 jsonl 캐시.

    python scripts/fetch_sanctions.py            # 전체
    python scripts/fetch_sanctions.py --only ofac
    python scripts/fetch_sanctions.py --sample   # 네트워크 없이 테스트용 샘플 생성

네트워크를 타므로 기본적으로 **명시 실행**만 한다. 서버는 이 캐시를 읽기만 하며,
캐시가 없으면 '조회 불가'로 정직하게 응답한다(가짜 통과 금지).

출력: server/var/sanctions/<LIST>.jsonl  — 한 줄에 {"name","id","programs"}
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir)))

from app.config import settings          # noqa: E402
from app.screening import SOURCES        # noqa: E402


def _out(name: str) -> str:
    os.makedirs(settings.sanctions_dir, exist_ok=True)
    return os.path.join(settings.sanctions_dir, f"{name.lower()}.jsonl")


def _write(name: str, rows: list[dict]) -> str:
    path = _out(name)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  → {path}  ({len(rows):,}건)")
    return path


def fetch_ofac() -> list[dict]:
    """OFAC SDN CSV. 컬럼: ent_num, SDN_Name, SDN_Type, Program, ..."""
    import httpx
    r = httpx.get(SOURCES["OFAC_SDN"], timeout=60, follow_redirects=True)
    r.raise_for_status()
    rows = []
    for rec in csv.reader(io.StringIO(r.text)):
        if len(rec) < 4:
            continue
        ent, nm, _typ, prog = rec[0], rec[1], rec[2], rec[3]
        nm = (nm or "").strip()
        if not nm or nm == "-0- ":
            continue
        rows.append({"name": nm, "id": ent.strip(),
                     "programs": [p.strip() for p in prog.split(";") if p.strip()]})
    return rows


def fetch_un() -> list[dict]:
    """UN 안보리 통합 리스트 XML."""
    import httpx
    r = httpx.get(SOURCES["UN_CONSOLIDATED"], timeout=90, follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("INDIVIDUAL", "ENTITY"):
            continue
        parts, ref, progs = [], "", []
        for ch in node:
            t = ch.tag.split("}")[-1]
            if t.startswith(("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")):
                if (ch.text or "").strip():
                    parts.append(ch.text.strip())
            elif t == "DATAID":
                ref = (ch.text or "").strip()
            elif t == "UN_LIST_TYPE" and (ch.text or "").strip():
                progs.append(ch.text.strip())
        nm = " ".join(parts).strip()
        if nm:
            rows.append({"name": nm, "id": ref, "programs": progs})
    return rows


def fetch_eu() -> list[dict]:
    """EU 통합 리스트 CSV(세미콜론 구분). 스키마가 바뀌면 이름 컬럼을 탐색한다."""
    import httpx
    r = httpx.get(SOURCES["EU_CONSOLIDATED"], timeout=90, follow_redirects=True)
    r.raise_for_status()
    text = r.content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    name_keys = None
    rows = []
    for rec in reader:
        if name_keys is None:
            name_keys = [k for k in rec.keys()
                         if k and "name" in k.lower() and "birth" not in k.lower()]
        nm = ""
        for k in (name_keys or []):
            if (rec.get(k) or "").strip():
                nm = rec[k].strip()
                break
        if nm:
            rows.append({"name": nm,
                         "id": (rec.get("Entity_LogicalId") or "").strip(),
                         "programs": [(rec.get("Entity_Regulation_Programme") or "").strip()]})
    return rows


SAMPLE = {
    # 오프라인 테스트용. 실제 리스트가 아니라 **명백한 가상 항목**이며,
    # 파일명도 sample_ 로 시작해 실데이터와 섞이지 않는다.
    "SAMPLE_TESTLIST": [
        {"name": "ACME TRADING CO., LTD.", "id": "TEST-1", "programs": ["TESTPROG"]},
        {"name": "Vostok Machine Industries OAO", "id": "TEST-2", "programs": ["TESTPROG"]},
        {"name": "Zenith Shipping Group Limited", "id": "TEST-3", "programs": ["TESTPROG"]},
    ]
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["ofac", "un", "eu"])
    ap.add_argument("--sample", action="store_true",
                    help="네트워크 없이 가상 샘플 리스트 생성(테스트용)")
    a = ap.parse_args()

    if a.sample:
        for nm, rows in SAMPLE.items():
            _write(nm, rows)
        print("샘플 생성 완료 — 실제 제재 리스트가 아닙니다(테스트 전용).")
        return 0

    jobs = {"ofac": ("OFAC_SDN", fetch_ofac),
            "un": ("UN_CONSOLIDATED", fetch_un),
            "eu": ("EU_CONSOLIDATED", fetch_eu)}
    if a.only:
        jobs = {a.only: jobs[a.only]}

    failed = 0
    for key, (name, fn) in jobs.items():
        print(f"[{key}] 수집 중…")
        try:
            _write(name, fn())
        except Exception as e:
            # 한 소스가 죽어도 나머지는 받는다. 실패를 조용히 삼키지 않는다.
            failed += 1
            print(f"  ! 실패: {type(e).__name__}: {e}")
    return 1 if failed == len(jobs) else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main())
