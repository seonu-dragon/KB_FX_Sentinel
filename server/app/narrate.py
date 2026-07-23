"""LLM 설명층 — 서버(행내) 측 자연어 생성. 숫자는 결정론 엔진이 만들고, LLM은 '설명'만 한다.

이 모듈이 존재하는 이유
========================
오프라인 데모(V1)는 고객 화면에서 LLM을 부르지 않는다 — 회사 기준환율(영업비밀)·신용정보가
외부로 나가면 안 되기 때문이다(`AI_EXTERNAL_CALL_ENABLED=false`). 그래서 V1의 AI 요약은
행내 규칙 생성(환각 원천 불가)이다.

그런데 "AI Challenge인데 LLM이 어디 있나"라는 질문에는 정면으로 답해야 한다. 답은 여기다:
**서버 계층(=KB 행내 인프라)에 LLM 설명층을 두되, 세 가지 안전장치를 건다.**

  1. 그라운딩 — LLM에는 이미 계산된 사실(숫자)만 준다. 새 수치를 만들면 가드레일이 잡는다.
  2. 데이터 최소화 — LLM에 보내는 사실에서 **기업명·정확한 예산환율·금액을 뺀다**(de-identify).
     리스크 사실(포지션·BBP·부담액 구간·국면·수단)만으로 설명이 된다.
  3. 폴백 — LLM이 없거나(키 미설정) 가드레일을 못 넘으면 **규칙 템플릿**으로 조용히 내려간다.
     그래서 이 엔드포인트는 키 없이도 항상 유효한 설명을 돌려준다(오프라인 기본값 = 템플릿).

기본값은 OFF(`AI_NARRATE_ENABLED=false`)다. 켜더라도 위 안전장치는 그대로다.
파일럿에서는 이 자리에 KB 승인/온프레미스 LLM을 꽂는다 — 인터페이스는 이미 이 모양이다.
"""
from __future__ import annotations

import os
import re

# config 를 먼저 import 해야 sys.path 에 KB 루트가 얹혀 fx_sentinel 을 찾는다(engine.py 와 같은 규약).
from .config import settings  # noqa: F401  (부트스트랩 목적 — 부수효과로 sys.path 설정)

try:
    from fx_sentinel import llm as _llm  # Claude 어댑터 — 실패해도 폴백이 있으므로 치명적이지 않다
except Exception:  # pragma: no cover
    _llm = None


SYSTEM = (
    "당신은 KB 수출입 금융 AI 코파일럿의 애널리스트다. 아래에 이미 결정론 엔진이 계산한 "
    "사실(숫자)만 주어진다. 이 숫자를 중소 수출입기업 재무담당자가 이해하도록 자연스러운 "
    "한국어 2~3문장으로 설명한다.\n"
    "규칙: (1) 주어진 숫자만 그대로 사용하고 어떤 수치도 새로 만들거나 바꾸지 않는다. "
    "(2) 환율을 예측하지 않는다 — '오른다/내린다' 단정 금지. "
    "(3) 매수·매도 권유·수익 보장 금지, 정보 제공 톤. "
    "(4) 이모지·면책 문구·군더더기 금지, 담백하고 구체적으로. "
    "(5) 예산환율 대비 이탈확률(BBP)의 의미와 부담액(보수적 상한)을 우선 설명한다. "
    "(6) 회사명·정확한 금액을 지어내지 않는다 — 주어지지 않았으면 '이 거래'로 부른다."
)

# 수익보장·확정예측·매매권유 — 하나라도 있으면 LLM 출력을 버리고 템플릿으로 내려간다.
_BAD = re.compile(
    r"수익\s*을?\s*보장|원금\s*보장|손실\s*없|반드시\s*(오르|올라|내리|떨어)|무조건|"
    r"매수\s*하(세요|십시오|시길)|매도\s*하(세요|십시오|시길)|투자를?\s*(추천|권유)|확실(한|히)\s*수익"
)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _grade(bbp_pct: float) -> str:
    if bbp_pct < 20:
        return "안정"
    if bbp_pct < 40:
        return "주의"
    if bbp_pct < 60:
        return "경계"
    return "심각"


def build_facts(*, pos: str, bbp_pct: float, es_total_krw: float, horizon_bd: int,
                gauge_grade: str, regime: str, instrument: str, hedge_ratio: float) -> str:
    """LLM에 줄 사실 — de-identified. 기업명·예산환율·금액은 넣지 않는다(데이터 최소화)."""
    pos_k = "수출(달러 수취)" if pos == "export" else "수입(달러 지급)"
    return (
        "[계산된 사실 — 이 숫자만 사용, 새 수치 금지]\n"
        f"- 포지션: {pos_k}\n"
        f"- 예산환율 이탈확률(BBP): {bbp_pct:.1f}% [{_grade(bbp_pct)}]\n"
        f"- 이탈 시 부담액(보수적 상한): {int(round(es_total_krw)):,}원\n"
        f"- 결제 만기: {horizon_bd}영업일\n"
        f"- 시장 위험게이지 등급: {gauge_grade}\n"
        f"- 시장 국면: {regime}\n"
        f"- 권고 헤지수단: {instrument}\n"
        f"- 참고 헤지비율: {int(round(hedge_ratio * 100))}%\n"
        "[요청] 위 사실만으로 이 거래의 환리스크 상황을 2~3문장으로 설명하세요. "
        "기업명·정확한 예산환율·금액은 주어지지 않았으니 '이 거래' 또는 '귀사'로 부르세요."
    )


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def grounded(text: str, facts: str) -> bool:
    """출력에 (a) 금지표현이 없고 (b) 지어낸 숫자가 없는지. 사실에 없는 2자리 이상 숫자가 나오면 거짓."""
    if not text or _BAD.search(text):
        return False
    fact_digits = _digits(facts)  # 사실의 모든 숫자를 이어붙인 것 — 부분일치로 관대하게 검사
    for tok in _NUM.findall(text):
        d = tok.replace(",", "").split(".")[0]
        if len(d) >= 2 and d not in fact_digits:
            return False  # 사실에 없던 구체 수치 = 환각 → 버린다
    return True


def template_narrative(*, pos: str, bbp_pct: float, es_total_krw: float,
                       horizon_bd: int, instrument: str, hedge_ratio: float) -> str:
    """규칙 기반 설명 — LLM이 없거나 가드레일을 못 넘을 때의 폴백. 숫자를 만들지 않는다."""
    pos_k = "수출대금 수취" if pos == "export" else "수입대금 지급"
    won = f"{int(round(es_total_krw)):,}원"
    if bbp_pct >= 40:
        head = (f"이 거래는 결제일({horizon_bd}영업일 뒤 {pos_k})에 예산환율이 깨질 가능성이 "
                f"{bbp_pct:.1f}%로 경보 구간입니다. 이탈 시 부담액은 보수적으로 최대 {won} 수준으로 봅니다.")
        tail = (f" 실수요 범위 안에서 부분 헤지(참고 {int(round(hedge_ratio*100))}%)와 "
                f"{instrument} 검토를 권합니다. 방향을 예측하는 것이 아니라, 정해진 포지션의 리스크를 번역한 값입니다.")
    else:
        head = (f"이 거래는 예산환율 초과 가능성이 {bbp_pct:.1f}%로 비교적 안정적입니다. "
                f"이탈 시 부담액은 보수적 상한으로 {won} 수준입니다.")
        tail = " 지금 서둘러 헤지할 필요는 낮고, 급변 시 알림을 걸어두는 편이 낫습니다. 이 값은 예측이 아니라 리스크 번역입니다."
    return head + tail


def enabled() -> bool:
    v = os.environ.get("AI_NARRATE_ENABLED")
    return bool(v) and v.strip().lower() in ("1", "true", "yes", "on")


def narrate(*, pos: str, bbp_pct: float, es_total_krw: float, horizon_bd: int,
            gauge_grade: str, regime: str, instrument: str, hedge_ratio: float) -> dict:
    """설명 생성. 반환: {narrative, source: 'llm'|'template', grounded, model}.

    항상 유효한 설명을 돌려준다(폴백 보장). LLM 성공 + 가드레일 통과일 때만 source='llm'.
    """
    tmpl = template_narrative(pos=pos, bbp_pct=bbp_pct, es_total_krw=es_total_krw,
                              horizon_bd=horizon_bd, instrument=instrument, hedge_ratio=hedge_ratio)
    if not (enabled() and _llm is not None):
        return {"narrative": tmpl, "source": "template", "grounded": True, "model": None}

    facts = build_facts(pos=pos, bbp_pct=bbp_pct, es_total_krw=es_total_krw, horizon_bd=horizon_bd,
                        gauge_grade=gauge_grade, regime=regime, instrument=instrument, hedge_ratio=hedge_ratio)
    text = None
    try:
        text = _llm.narrate(SYSTEM, facts)
    except Exception:
        text = None

    if text and grounded(text, facts):
        return {"narrative": text.strip(), "source": "llm",
                "grounded": True, "model": getattr(_llm, "MODEL", None)}
    # LLM 미가용·실패·환각 → 템플릿으로 정직하게 내려간다
    return {"narrative": tmpl, "source": "template", "grounded": True, "model": None}
