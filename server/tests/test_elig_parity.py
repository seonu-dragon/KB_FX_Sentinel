"""ELIG 이중구현 방지 — 데모 JS 와 서버 Python 을 전 조합 대조.

■ 왜 필요한가
같은 자격 규칙이 두 곳에 있다: 데모 HTML 의 `const ELIG`(JS) 와 server/app/elig.py.
이 프로젝트는 예전에 자격 조건이 화면 곳곳에 흩어져 서로 반대로 말한 적이 있고,
그래서 ELIG 를 단일 소스로 모았다. 서버가 생기면서 다시 두 곳이 됐다.

두 곳을 유지하되 **갈라지면 즉시 죽게** 만든다. 실제 Chrome 으로 데모의 JS 를 돌려
전 조합의 판정을 받아오고, Python 결과와 한 건씩 비교한다.

전제: Google Chrome 설치 (기존 tests/ui_smoke_test.py 와 동일한 하네스).
Chrome 이 없으면 skip 한다 — CI 환경이 없다고 서버 테스트 전체가 죽으면 안 된다.
"""
from __future__ import annotations

import io
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.elig import KEYS, Facts, deny_reason, eligible   # noqa: E402

SRC = os.path.join(KB, "FX_Sentinel_demo_ui.html")
MARK = "@@ELIG@@"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# 전 조합: pos × cert × credit × cash × biz × settle = 64
# party 는 글로벌셀러 정규식을 때리는 값과 아닌 값을 함께 넣는다.
POS = ("export", "import")
CERT = ("confirmed", "provisional")
CREDIT = ("yes", "no")
CASH = ("ok", "tight")
BIZ = ("corp", "sole")
SETTLE = ("fixed", "window")
PARTIES = ("Tokyo Precision KK", "Amazon Marketplace 정산", "", "글로벌 셀러 플랫폼")


def _chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _cases() -> list[dict]:
    out = []
    for pos, cert, credit, cash, biz, settle, party in itertools.product(
            POS, CERT, CREDIT, CASH, BIZ, SETTLE, PARTIES):
        out.append({"pos": pos, "cert": cert, "credit": credit, "cash": cash,
                    "biz": biz, "settle": settle, "party": party,
                    "name": "테스트기업"})
    return out


def _probe(cases: list[dict]) -> str:
    """데모 페이지 안에서 JS ELIG 를 전 조합 실행하고 JSON 을 뱉는 스크립트."""
    return (
        "<script>(function(){var CASES=" + json.dumps(cases, ensure_ascii=False) + ";"
        "var KEYS=" + json.dumps(KEYS, ensure_ascii=False) + ";"
        "var out=[];"
        "for(var i=0;i<CASES.length;i++){var f=CASES[i];var row={};"
        "for(var j=0;j<KEYS.length;j++){var k=KEYS[j];"
        "var ok=eligible(k,f);row[k]=[!!ok, ok?'':String(denyReason(k,f))];}"
        "out.push(row);}"
        "var d=document.createElement('div');d.id='__elig';"
        "d.textContent='" + MARK + "'+JSON.stringify(out)+'" + MARK + "';"
        "document.body.appendChild(d);})();</script>"
    )


def _run_js(cases: list[dict]) -> list[dict]:
    chrome = _chrome()
    src = io.open(SRC, encoding="utf-8").read()
    html = src.replace("</body>", _probe(cases) + "\n</body>")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "elig.html")
        io.open(path, "w", encoding="utf-8").write(html)
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=8000", "--dump-dom",
             "file:///" + path.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", timeout=180)
        dom = proc.stdout or ""
    # DOM 덤프에는 주입한 <script> 원문도 그대로 들어 있어, 마커가 **코드 문자열에서 먼저**
    # 걸린다(그러면 JSON 이 아니라 "'+JSON.stringify(out)+'" 를 파싱하게 된다).
    # 스크립트를 걷어낸 뒤 결과 div 만 본다.
    dom = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
    found = re.findall(re.escape(MARK) + "(.*?)" + re.escape(MARK), dom, re.S)
    if not found:
        pytest.fail("JS 프로브 미실행 — 데모의 eligible/denyReason 을 찾을 수 없음")
    import html as _html
    return json.loads(_html.unescape(found[0]))


@pytest.mark.skipif(_chrome() is None, reason="Chrome 미설치 — JS 대조 불가")
def test_elig_matches_demo_js():
    cases = _cases()
    js = _run_js(cases)
    assert len(js) == len(cases)

    mismatches = []
    for case, jrow in zip(cases, js):
        f = Facts(**case)
        for k in KEYS:
            py_ok = eligible(k, f)
            js_ok, js_reason = jrow[k][0], jrow[k][1]
            if py_ok != js_ok:
                mismatches.append(f"[{k}] ok 불일치 py={py_ok} js={js_ok} · {case}")
                continue
            if not py_ok:
                py_reason = deny_reason(k, f)
                if py_reason != js_reason:
                    mismatches.append(
                        f"[{k}] 사유 불일치\n  py={py_reason!r}\n  js={js_reason!r}\n  {case}")

    assert not mismatches, (
        f"{len(mismatches)}건 불일치 — 서버 ELIG 와 데모 JS 가 갈라졌습니다:\n"
        + "\n".join(mismatches[:12]))


def test_case_space_is_actually_exhaustive():
    """조합이 줄어들면 대조가 조용히 약해진다 — 개수를 못박아 둔다."""
    assert len(_cases()) == 2 * 2 * 2 * 2 * 2 * 2 * len(PARTIES) == 256
