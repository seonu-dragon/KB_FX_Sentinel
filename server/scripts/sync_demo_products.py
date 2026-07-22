"""상품 마스터(YAML) ↔ 데모 HTML 의 KB_PRODUCTS 동기화 검사·생성.

    python scripts/sync_demo_products.py --check    # 어긋나면 exit 1 (테스트가 이걸 쓴다)
    python scripts/sync_demo_products.py --write    # YAML 기준으로 데모 JS 블록 재생성
    python scripts/sync_demo_products.py --diff     # 차이만 출력

■ 왜 필요한가
데모 HTML 은 단일 파일로 배포돼야 해서 런타임에 YAML 을 읽을 수 없다. 그래서 같은 내용이
두 곳에 존재한다 — 그대로 두면 반드시 갈라진다(이 프로젝트가 ELIG 에서 이미 겪은 일).
`--check` 를 CI 에 걸어 갈라지는 순간 죽게 만들고, `--write` 로 YAML → HTML 방향의
단방향 생성을 제공한다. **마스터는 YAML 이고, HTML 은 생성물이다.**

--write 는 HTML 을 수정하므로 기본값이 아니다. 실행 후에는 반드시
`python tests/ui_smoke_test.py` 로 데모 회귀를 확인할 것.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))
sys.path.insert(0, ROOT)

from app.catalog import get_catalog          # noqa: E402

DEMO = os.path.join(KB, "FX_Sentinel_demo_ui.html")
BEGIN = "var KB_PRODUCTS=["
END = "\n];"

# YAML 필드 → 데모 JS 필드(축약키). 데모의 키 이름을 바꾸지 않기 위해 매핑을 둔다.
FIELD_MAP = [("cat", "category"), ("n", "name"), ("src", "source"),
             ("d", "description"), ("req", "requirement"),
             ("docs", "documents"), ("ch", "channels"), ("path", "kb_path")]


def expected_rows() -> list[dict]:
    cat = get_catalog()
    rows = []
    for p in cat.all():
        rows.append({js: getattr(p, py) for js, py in FIELD_MAP})
    return rows


def demo_block() -> tuple[str, int, int]:
    src = io.open(DEMO, encoding="utf-8").read()
    i = src.find(BEGIN)
    if i < 0:
        raise SystemExit("데모에서 KB_PRODUCTS 를 찾지 못했습니다")
    j = src.find(END, i)
    if j < 0:
        raise SystemExit("KB_PRODUCTS 블록의 끝을 찾지 못했습니다")
    return src, i, j + len(END)


def demo_rows() -> list[dict]:
    """데모의 JS 리터럴을 파싱. 정규식으로 JS 를 읽는 건 위험하므로 보수적으로 처리한다."""
    src, i, j = demo_block()
    body = src[i + len(BEGIN):j - len(END)]
    rows = []
    for line in body.split("\n"):
        line = line.strip().rstrip(",")
        if not line.startswith("{") or not line.endswith("}"):
            continue
        row = {}
        for js, _py in FIELD_MAP:
            m = re.search(js + r':"((?:[^"\\]|\\.)*)"', line)
            if m:
                row[js] = m.group(1).replace('\\"', '"')
        if row:
            rows.append(row)
    return rows


def render_block(rows: list[dict]) -> str:
    lines = [BEGIN]
    for k, r in enumerate(rows):
        parts = []
        for js, _py in FIELD_MAP:
            v = (r.get(js) or "").replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{js}:"{v}"')
        tail = "" if k == len(rows) - 1 else ","
        lines.append(" {" + ",".join(parts) + "}" + tail)
    return "\n".join(lines) + END


def compare() -> list[str]:
    exp, got = expected_rows(), demo_rows()
    problems = []
    if len(exp) != len(got):
        problems.append(f"건수 불일치: YAML {len(exp)}건 vs 데모 {len(got)}건")
    for idx, (e, g) in enumerate(zip(exp, got)):
        for js, _py in FIELD_MAP:
            if (e.get(js) or "") != (g.get(js) or ""):
                problems.append(
                    f"[{idx}] {e.get('n', '?')} · 필드 {js} 불일치\n"
                    f"    YAML: {e.get(js)!r}\n"
                    f"    데모: {g.get(js)!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    problems = compare()

    if a.write:
        src, i, j = demo_block()
        new = src[:i] + render_block(expected_rows()) + src[j:]
        io.open(DEMO, "w", encoding="utf-8", newline="").write(new)
        print(f"데모 KB_PRODUCTS 를 YAML 기준으로 재생성했습니다 ({len(expected_rows())}건).")
        print("→ 반드시 `python tests/ui_smoke_test.py` 로 회귀를 확인하십시오.")
        return 0

    if not problems:
        print(f"동기화 OK — 상품 {len(expected_rows())}건 일치 (카탈로그 v{get_catalog().version})")
        return 0

    print(f"불일치 {len(problems)}건:")
    for p in problems[:20]:
        print("  " + p)
    if not (a.check or a.diff):
        print("\n--write 로 YAML 기준 재생성이 가능합니다.")
    return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main())
