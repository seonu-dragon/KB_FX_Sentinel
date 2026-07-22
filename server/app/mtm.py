"""체결 후 평가손익(MTM)·증거금 스트레스.

■ 왜 필요한가 (부장 갭리뷰 GAP-2)
`lifecycle.py` 의 상태는 draft → … → contracted → settled 인데, **contracted 와 settled
사이에 아무것도 없다.** 시간이 흐르며 환율이 움직일 때 무슨 일이 생기는지 도구가 모른다.

KIKO 사고의 실제 메커니즘이 정확히 이 구간이다. 계약 시점엔 문제가 없었다. 환율이
급변하자 기업의 평가손이 폭증 → 은행이 추가담보를 요구 → 기업 자금경색 → 은행 여신 부실.
소비자보호 이슈이자 **동시에 은행 신용리스크 이슈**다.

기존 화면은 국면 스트레스를 **계약 전 BBP** 에만 걸었다. 정작 물어야 할 건
"이미 체결한 300k 선물환이 급변을 맞으면 평가손이 얼마고, 추가담보를 얼마 물어야 하고,
그 회사 현금으로 감당 가능한가"다.

■ 방향을 틀리지 않는다 (설계 중 잡은 오류)
초안 데모 시나리오는 "킹달러 → 체결 선물환 평가손"이었다. 그런데 데모의 체결 건
(나래상사)은 **수입**이고 매수 선물환이다 — 킹달러(원화 약세)에서는 계약환율이 시장보다
유리해져 **평가익**이 난다. 헤지가 제 역할을 한 것이다.

수입기업의 평가손·추가담보는 반대 방향(**원화 강세**)에서 난다. 포지션마다 어느 쪽이
불리한지가 다르므로, 이 모듈은 방향을 가정하지 않고 **양방향을 다 계산한 뒤 불리한 쪽을
지목**한다. 방향을 예측하는 게 아니라, 어느 방향이 이 회사에 아픈지를 밝히는 것이다.

■ 무엇을 계산하지 않는가
  · 할인(현재가치화)·스왑포인트·대고객 스프레드 — 금리커브와 고시 조건이 필요하다.
    없는 값을 지어내지 않고 **현물 차이 기준 근사**임을 명시한다.
  · 실제 증거금률·추가담보 요구 기준 — KB 여신정책 소관이다. 데모 설계값으로 두고 표기한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# 추가담보(마진콜) 트리거 — 평가손이 명목 원화환산액의 이 비율을 넘으면 요구 대상.
# 데모 설계값이며 KB 여신정책의 실제 기준이 아니다. 은행·상품·고객등급마다 다르다.
MARGIN_CALL_PCT = 0.10

# 스트레스에 쓸 표준편차 배수. 예측이 아니라 "이만큼 움직이면"의 격자다.
SIGMA_STEPS = (-2.0, -1.0, 1.0, 2.0)


def sigma_move(spot: float, sigma_ann: float, horizon_bd: int) -> float:
    """만기까지의 1σ 이동폭. BBP·손실시나리오와 같은 시간 스케일(√(N/252))."""
    if spot <= 0 or sigma_ann <= 0 or horizon_bd <= 0:
        return 0.0
    return spot * sigma_ann * math.sqrt(horizon_bd / 252.0)


def mtm_value(pos: str, contract_rate: float, valuation_rate: float,
              notional: float) -> float:
    """고객 관점 평가손익(원). 양수=평가익, 음수=평가손.

    수출(매도 선물환): 계약환율에 팔기로 했으므로 시장이 오르면 손해다 → (K_c − S)·N
    수입(매수 선물환): 계약환율에 사기로 했으므로 시장이 내리면 손해다 → (S − K_c)·N

    ※ 할인·스왑포인트 미반영 근사다. 정확한 MTM 은 잔존만기 선도환율로 평가하고
      할인해야 하는데, 그러려면 금리커브와 고시 스왑포인트가 필요하다 —
      없는 값을 지어내지 않는다(rate_policy).
    """
    if notional <= 0:
        return 0.0
    if pos == "export":
        return (contract_rate - valuation_rate) * notional
    return (valuation_rate - contract_rate) * notional


def adverse_direction(pos: str) -> dict:
    """이 포지션에 **불리한** 환율 방향.

    이걸 명시하는 이유: 화면이 "킹달러 = 위험"처럼 방향을 고정해 말하면 수입기업에게
    거짓말이 된다. 수입기업의 헤지는 킹달러에서 이익이 나고, 원화 강세에서 아프다.
    """
    if pos == "export":
        return {"direction": "up",
                "label": "원화 약세(환율 상승)",
                "why": "매도 선물환은 계약환율에 팔아야 하므로 시장이 오를수록 기회손실·평가손이 커집니다"}
    return {"direction": "down",
            "label": "원화 강세(환율 하락)",
            "why": "매수 선물환은 계약환율에 사야 하므로 시장이 내릴수록 평가손이 커집니다"}


@dataclass(frozen=True)
class Contract:
    """체결된 헤지 약정. 데모에서는 프리셋, 실서비스에서는 KB 원장."""
    pos: str                  # export | import
    notional: float           # 거래 통화 명목
    contract_rate: float      # 체결 환율
    horizon_bd: int           # 잔존 만기(영업일)
    currency: str = "USD"


def margin_call(mtm_krw: float, notional_krw: float,
                cash_buffer_krw: float = 0.0,
                trigger_pct: float = MARGIN_CALL_PCT) -> dict:
    """추가담보 요구 판정 + 감당 가능성.

    ■ 왜 현금여유를 따로 받는가 (F1 의 '감내 가능 손실'과 다른 것)
    감내 가능 손실은 **손익 관점**이고("이만큼 손해봐도 회사가 버틴다"),
    추가담보는 **유동성 관점**이다("지금 당장 이만큼 현금을 넣을 수 있다").
    KIKO 에서 기업들이 무너진 지점이 정확히 이 차이다 — 장부상 감당 가능한 손실이었지만
    당장 넣을 현금이 없었다. 두 값을 같은 것으로 뭉개면 그 위험이 안 보인다.
    """
    loss = max(0.0, -mtm_krw)                 # 평가손만(평가익은 담보 요구 대상 아님)
    threshold = max(0.0, notional_krw) * trigger_pct
    called = loss > threshold
    required = max(0.0, loss - threshold) if called else 0.0
    coverable = (cash_buffer_krw >= required) if called else True
    return {
        "triggered": called,
        "threshold_krw": int(round(threshold)),
        "required_krw": int(round(required)),
        "cash_buffer_krw": int(round(cash_buffer_krw)),
        "coverable": coverable,
        "shortfall_krw": int(round(max(0.0, required - cash_buffer_krw))) if called else 0,
        "trigger_pct": trigger_pct,
    }


def stress(c: Contract, spot: float, sigma_ann: float,
           cash_buffer_krw: float = 0.0,
           regime_name: str = "현재") -> dict:
    """국면 σ 로 ±1σ·±2σ 를 걸어 평가손익·추가담보를 전개한다.

    방향을 예측하지 않는다 — 양방향을 다 계산하고, 이 포지션에 불리한 쪽을 지목한다.
    """
    m1 = sigma_move(spot, sigma_ann, c.horizon_bd)
    notional_krw = c.notional * spot
    adv = adverse_direction(c.pos)

    rows = []
    for k in SIGMA_STEPS:
        s = spot + k * m1
        if s <= 0:
            continue
        v = mtm_value(c.pos, c.contract_rate, s, c.notional)
        mc = margin_call(v, notional_krw, cash_buffer_krw)
        rows.append({
            "sigma": k,
            "label": f"{k:+.0f}σ",
            "rate": round(s, 1),
            "mtm_krw": int(round(v)),
            "is_loss": v < 0,
            "margin": mc,
        })

    # 현재 시점 평가 (스트레스 없음)
    now_v = mtm_value(c.pos, c.contract_rate, spot, c.notional)
    now_mc = margin_call(now_v, notional_krw, cash_buffer_krw)

    losses = [r for r in rows if r["is_loss"]]
    worst = min(rows, key=lambda r: r["mtm_krw"]) if rows else None
    called = [r for r in rows if r["margin"]["triggered"]]
    uncoverable = [r for r in called if not r["margin"]["coverable"]]

    if uncoverable:
        verdict = "감당 불가 구간 있음"
        advice = (f"{uncoverable[0]['label']} 이동에서 추가담보 요구가 현금여유를 초과합니다. "
                  "체결 명목을 줄이거나, 추가담보가 없는 수단(옵션형·K-SURE)을 검토하십시오.")
    elif called:
        verdict = "추가담보 요구 가능"
        advice = ("스트레스 구간에서 추가담보 요구가 발생하지만 신고된 현금여유로 대응 가능합니다. "
                  "요구 시점에 실제 현금이 있는지 자금계획과 대조하십시오.")
    else:
        verdict = "추가담보 요구선 미도달"
        advice = "이 스트레스 범위에서는 추가담보 요구선에 닿지 않습니다."

    return {
        "regime": regime_name,
        "sigma_ann": sigma_ann,
        "sigma_move_1": round(m1, 1),
        "spot": spot,
        "contract_rate": c.contract_rate,
        "notional": c.notional,
        "notional_krw": int(round(notional_krw)),
        "currency": c.currency,
        "pos": c.pos,
        "adverse": adv,
        "now": {"rate": spot, "mtm_krw": int(round(now_v)),
                "is_loss": now_v < 0, "margin": now_mc},
        "rows": rows,
        "worst": worst,
        "loss_count": len(losses),
        "verdict": verdict,
        "advice": advice,
        "note": (
            "평가손익은 **현물 차이 기준 근사**입니다 — 할인(현재가치화)·스왑포인트·"
            "대고객 스프레드는 금리커브와 고시 조건이 필요해 반영하지 않았습니다(RM 견적). "
            f"추가담보 트리거({trigger_label(MARGIN_CALL_PCT)})는 데모 설계값이며 KB 여신정책의 "
            "실제 증거금 기준이 아닙니다. ±σ 이동은 국면 변동성을 적용한 시나리오이며 "
            "환율 예측이 아닙니다(방향 가정 없음)."),
    }


def trigger_label(pct: float) -> str:
    return f"평가손 > 명목의 {pct:.0%}"


def sizing_advice(c: Contract, spot: float, sigma_ann: float,
                  cash_buffer_krw: float) -> Optional[dict]:
    """감당 불가일 때, **체결 시점에 명목을 얼마로 줄였어야 했는가**를 역산한다.

    막기만 하면 "그래서 어쩌라고"가 된다. 이 회사가 감당 가능한 명목 상한을 숫자로 준다.
    사후 분석이자, 다음 체결 때 쓰는 사전 규율이다.

    유도: 불리한 2σ 이동에서
        평가손 = m2 · N        (원)
        임계   = trigger_pct · N · spot
        요구액 = m2·N − trigger_pct·N·spot ≤ cash
        → N ≤ cash / (m2 − trigger_pct·spot)
    분모가 0 이하면 어떤 명목에서도 요구가 발생하지 않는다(트리거가 이동폭보다 크다).
    """
    m2 = 2.0 * sigma_move(spot, sigma_ann, c.horizon_bd)
    denom = m2 - MARGIN_CALL_PCT * spot
    if denom <= 0:
        return None
    max_notional = cash_buffer_krw / denom
    if max_notional >= c.notional:
        return None
    return {
        "max_notional": round(max_notional, 0),
        "current_notional": c.notional,
        "reduce_by": round(c.notional - max_notional, 0),
        "currency": c.currency,
        "basis": "불리 방향 2σ 이동 기준 · 신고된 현금여유로 추가담보를 감당할 수 있는 최대 명목",
    }
