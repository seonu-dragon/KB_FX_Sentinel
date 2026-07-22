"""
FX Sentinel — LLM 어댑터 (Claude API)
=====================================
하이브리드 원칙(청사진 §5.1): **결정론 코어가 숫자를 산출하고, LLM은 그 숫자를 '설명'만 한다.**
LLM이 수치를 지어내거나 바꾸지 못하도록 시스템 프롬프트로 강하게 그라운딩한다.

라이브 API가 없어도(패키지 미설치·자격증명 없음·호출 실패) end-to-end 데모가 끊기지 않도록,
narrate()는 실패 시 None을 반환하고 호출부는 규칙기반 템플릿으로 폴백한다.

기본 모델: claude-opus-4-8 (Anthropic 공식 SDK). 짧은 설명 생성이라 thinking은 켜지 않는다.
"""
from __future__ import annotations

MODEL = "claude-opus-4-8"

_client = None
_disabled = False


def disable() -> None:
    """LLM 강제 비활성화(오프라인/테스트/--no-llm)."""
    global _disabled
    _disabled = True


def _get_client():
    global _client, _disabled
    if _disabled:
        return None
    if _client is not None:
        return _client
    try:
        import anthropic
    except Exception:
        _disabled = True
        return None
    try:
        # 자격증명은 환경에서 해석(ANTHROPIC_API_KEY 또는 ANTHROPIC_AUTH_TOKEN)
        c = anthropic.Anthropic()
    except Exception:
        _disabled = True
        return None
    # 생성은 키 없이도 되므로(호출 시점에야 401) — 자격증명 유무를 여기서 정직하게 판별
    if not (getattr(c, "api_key", None) or getattr(c, "auth_token", None)):
        _disabled = True
        return None
    _client = c
    return _client


def available() -> bool:
    return _get_client() is not None


def narrate(system: str, user: str, max_tokens: int = 700) -> str | None:
    """계산된 사실(user)만으로 자연어 설명을 생성. 실패 시 None → 호출부가 템플릿 폴백.

    max_tokens는 짧은 설명이라 16k 미만 → 비스트리밍으로 충분(SDK 타임아웃 여유).
    """
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except Exception:
        # 401/네트워크/모델미지원 등 어떤 실패든 규칙기반으로 폴백
        return None
