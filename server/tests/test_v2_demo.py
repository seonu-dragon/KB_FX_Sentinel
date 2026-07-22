"""V2(서버연동 데모) — V1 과의 동기화 + V1 무오염 + 안전 불변식.

V1 은 예선 제출물이고 V2 는 생성물이다. 손으로 두 벌 유지하면 반드시 갈라지므로
`build_server_demo.py --check` 로 최신 여부를 강제한다.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))

V1 = os.path.join(KB, "FX_Sentinel_demo_ui.html")
V2 = os.path.join(KB, "FX_Sentinel_demo_ui_server.html")
BUILD = os.path.join(ROOT, "scripts", "build_server_demo.py")


def _read(p: str) -> str:
    return io.open(p, encoding="utf-8", newline="").read()


def test_v2_exists():
    assert os.path.exists(V2), "V2 가 없습니다 — build_server_demo.py 실행 필요"


def test_v2_is_up_to_date_with_v1():
    """V1 을 고쳤는데 V2 를 다시 만들지 않으면 두 데모가 다른 화면이 된다."""
    r = subprocess.run([sys.executable, BUILD, "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=180)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def test_v1_is_not_contaminated():
    """빌드가 V1 을 건드리면 예선 제출물이 오염된다."""
    v1 = _read(V1)
    for token in ("SERVER_LINK_V2", "srv-run", "srv-base", "/v1/assess"):
        assert token not in v1, f"V1 에 V2 흔적이 있습니다: {token}"


def test_v2_has_the_panel():
    v2 = _read(V2)
    for token in ("SERVER_LINK_V2", "srv-run", "srv-ping", "/v1/assess", "/v1/integrations"):
        assert token in v2, f"V2 에 연동 블록이 없습니다: {token}"


def test_v2_does_not_autocall_on_load():
    """로드만으로 네트워크가 나가면 '서버 없이 재현 가능'이라는 전제가 깨진다.

    주입 스크립트가 boot() 에서 addEventListener 만 하고 verify()/ping() 을
    직접 부르지 않는지 본다.
    """
    v2 = _read(V2)
    i = v2.find("SERVER_LINK_V2")
    tail = v2[i:]
    boot = tail[tail.find("function boot()"):tail.find("})();", tail.find("function boot()"))]
    assert "addEventListener" in boot
    assert "verify()" not in boot, "로드 시 자동 호출이 있습니다"
    assert "ping()" not in boot, "로드 시 자동 호출이 있습니다"


def test_v2_keeps_external_call_policy():
    """고객정보 외부 AI 전송 정책은 V2 에서도 그대로다."""
    v2 = _read(V2)
    assert "const AI_EXTERNAL_CALL_ENABLED = false;" in v2


def test_v2_never_claims_verified_on_fallback():
    """폴백했는데 '서버 검증됨'이라고 하면 그게 가장 나쁜 거짓말이다."""
    v2 = _read(V2)
    assert "서버로 검증되지 않았습니다" in v2


def test_v2_declares_server_is_authoritative_on_mismatch():
    v2 = _read(V2)
    assert "불일치 — 서버 값이 정답" in v2


def test_v2_note_has_no_markdown_leakage():
    """note() 는 HTML 을 이스케이프하므로 '**' 를 쓰면 별표가 화면에 그대로 보인다.

    V1 본문에는 한국어 주석에 '**강조**' 가 정상적으로 쓰이므로, 파일을 통째로 훑으면
    오탐이 난다(실제로 처음 이렇게 짰다가 걸렸다). 주입 상수만 정확히 본다.
    """
    sys.path.insert(0, ROOT)
    from scripts.build_server_demo import PANEL, SCRIPT      # noqa: E402
    for name, blob in (("PANEL", PANEL), ("SCRIPT", SCRIPT)):
        assert "**" not in blob, f"{name} 에 마크다운 강조가 남아 있습니다"


def test_v2_title_distinguishes_the_build():
    assert "서버연동판(V2)" in _read(V2)


def test_build_refuses_to_run_on_contaminated_v1():
    """V1 에 이미 V2 블록이 있으면 빌드가 멈춰야 한다(이중 주입 방지)."""
    sys.path.insert(0, ROOT)
    from scripts.build_server_demo import build      # noqa: E402
    with pytest.raises(SystemExit):
        build(_read(V2))          # V2 를 입력으로 주면 이미 마커가 있다
