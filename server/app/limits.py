"""여신 한도 소진 — 신용환산액(CEE) 기준.

■ 왜 필요한가 (부장 갭리뷰 GAP-3)
`elig.py` 의 `credit` 은 `yes|no` 이진값이다. 그런데 실무에서 RM 이 선물환 건을 받으면
제일 먼저 하는 일은 "이 회사 파생한도 잔액이 얼마고 이 건으로 얼마 소진되나"다.

한도 없이 "선물환 추천"을 띄우면 **RM 업무가 줄지 않고 늘어난다.** 고객은 화면에서
추천을 봤으니 된다고 생각하고 오는데, RM 이 한도를 조회해보니 안 되면 그때부터 실랑이다.
도구가 고객 기대를 만들고 그 뒷수습을 영업점이 하는 구조 — 부장이 가장 싫어하는 시나리오다.

■ 두 개의 숫자가 필요하다 (설계 중 정정한 부분)
초안은 한도를 **원화 CEE 기준**으로만 잡았다. 그러자 한 가지가 안 맞았다 —
1년 이하 환산율이 1% 라서, 5억 한도가 명목 3,200만 달러까지 받아준다. SME 현실과
동떨어지고, 화면에서 한도가 차는 장면 자체가 안 나온다.

실무를 다시 보면 숫자가 **두 개**다:
  · **명목 한도** — 고객이 아는 방식이다. "우리 선물환 한도 100만불" 처럼 외화 명목으로
    약정하고, 소진도 명목으로 센다. 화면의 소진 게이지는 이걸 따라야 한다.
  · **신용환산액(CEE)** — 은행 내부 여신 계상액이다. 계약 시점 시장가치는 0 에 가깝고
    은행이 지는 위험은 만기까지 환율이 움직여 상대방이 이행하지 못할 위험이므로
        CEE = 명목 × 신용환산율(만기 구간별)
    로 환산해 여신에 잡는다. 만기가 길수록 환산율이 크다.

둘 다 보여준다. 고객은 "내 한도가 얼마나 찼나"(명목)를 묻고, RM·여신부는 "이 건이 우리
여신을 얼마나 먹나"(CEE)를 묻는다. 하나만 보여주면 한쪽은 답을 못 얻는다.

■ 요율은 여전히 만들지 않는다 (products.yaml rate_policy)
한도와 요율은 다른 문제다.
  · **한도액**은 고객이 알 수 있고 알려줄 수 있는 값이다(자기 여신 약정).
  · **요율·스프레드**는 은행의 가격정책이라 우리가 지어내면 계약조건 오인이 된다.
그래서 한도는 입력받아 계산하고, 요율은 끝까지 RM 견적으로 넘긴다.

신용환산율 자체도 은행·감독규정마다 다르므로 **데모 설계값**임을 응답에 적는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── 신용환산율 (만기 구간별) ─────────────────────────────────────────
# 바젤 커런트익스포저법의 환율계약 add-on 표(1년 이하 1%, 1~5년 5%, 5년 초과 7.5%)를
# 참고한 **데모 설계값**이다. KB 내부 한도규정의 실제 환산율이 아니다 — 그 값은 여신정책
# 소관이고, 연동 전까지 우리가 정할 수 있는 값이 아니다. 그 사실을 응답에 적는다.
#
# 구간을 영업일로 잡는 이유: 이 도구의 모든 만기 입력이 영업일이다(BBP·스케줄과 같은 축).
CCF_BANDS = [
    (0,   252,    0.01),     # 1년 이하
    (253, 1260,   0.05),     # 1~5년
    (1261, 10_000, 0.075),   # 5년 초과
]

# 소진율 경고 임계 — 넘으면 화면이 색을 바꾸고 RM 확인사항이 붙는다.
UTIL_WARN = 0.80
UTIL_FULL = 1.00


def ccf_for(horizon_bd: int) -> float:
    for lo, hi, r in CCF_BANDS:
        if lo <= horizon_bd <= hi:
            return r
    return CCF_BANDS[-1][2]


def credit_equivalent(notional: float, horizon_bd: int) -> float:
    """신용환산액 — 이 거래가 실제로 한도를 얼마나 먹는가.

    명목 그대로가 아니라는 점이 핵심이다. 500,000 USD 3개월 선물환은 명목 5억이 아니라
    그 1% 만 한도를 먹는다. 이 구분을 못 하면 화면이 한도를 10배 과대하게 잡아
    멀쩡한 거래를 막는다.
    """
    if notional <= 0 or horizon_bd <= 0:
        return 0.0
    return notional * ccf_for(horizon_bd)


@dataclass(frozen=True)
class LimitInput:
    """고객 자기신고 한도. 원장 미연동이므로 **사실로 승격하지 않는다.**

    단위는 **거래 통화 명목**이다(예: USD 1,000,000). 원화가 아닌 이유는 실제 약정이
    그렇게 나가기 때문이다 — "선물환 한도 100만불".
    """
    limit_notional: float = 0.0   # 파생(선물환) 한도 약정액 — 거래 통화 명목
    used_notional: float = 0.0    # 기사용 명목


def assess_limit(li: Optional[LimitInput], notional: float, horizon_bd: int,
                 spot: float = 0.0) -> dict:
    """한도 소진 프리뷰.

    소진율은 **명목 기준**(고객이 아는 방식), 여신 계상액은 **CEE**(은행 내부)로 함께 낸다.

    한도 미입력(limit_notional<=0)은 **초과가 아니라 '미확인'** 이다. 0 으로 두고 초과
    판정하면 한도를 모르는 고객 전원이 차단된다 — 모르는 것과 없는 것은 다르다.
    """
    cee_ccy = credit_equivalent(notional, horizon_bd)      # 거래 통화 기준 CEE
    cee_krw = cee_ccy * spot if spot > 0 else 0.0
    rate = ccf_for(horizon_bd)

    if li is None or li.limit_notional <= 0:
        return {
            "known": False,
            "notional": round(notional, 2),
            "cee_notional": round(cee_ccy, 2),
            "cee_krw": int(round(cee_krw)),
            "ccf": rate,
            "limit_notional": 0.0, "used_notional": 0.0, "available_notional": 0.0,
            "util_before": 0.0, "util_after": 0.0,
            "exceeds": False,
            "status": "미확인",
            "message": ("파생 한도 미입력 — 소진 여부를 판정할 수 없습니다. "
                        f"이 거래가 KB 여신에 계상되는 신용환산액은 {cee_ccy:,.0f}"
                        f"(명목의 {rate:.1%})이며, 한도 확인은 RM 원장 조회 사항입니다."),
            "note": _NOTE,
        }

    avail = max(0.0, li.limit_notional - li.used_notional)
    before = li.used_notional / li.limit_notional
    after = (li.used_notional + notional) / li.limit_notional
    exceeds = (li.used_notional + notional) > li.limit_notional

    if exceeds:
        status = "초과"
        msg = (f"이 거래 명목 {notional:,.0f}을 더하면 한도를 넘습니다 "
               f"(잔여 {avail:,.0f}). 선물환 계열은 체결할 수 없습니다 — "
               "명목 축소, 여신 증액 협의, 또는 여신 불요 수단(K-SURE)으로 전환이 필요합니다.")
    elif after >= UTIL_WARN:
        status = "임박"
        msg = (f"체결 시 소진율 {after:.0%} — 한도 임박입니다. "
               "후속 거래 여력이 줄어드니 RM 과 증액 시점을 협의하십시오.")
    else:
        status = "여유"
        msg = f"체결 시 소진율 {after:.0%} — 잔여 여력이 있습니다."

    return {
        "known": True,
        "notional": round(notional, 2),
        "cee_notional": round(cee_ccy, 2),
        "cee_krw": int(round(cee_krw)),
        "ccf": rate,
        "limit_notional": round(li.limit_notional, 2),
        "used_notional": round(li.used_notional, 2),
        "available_notional": round(avail, 2),
        "util_before": round(before, 4),
        "util_after": round(after, 4),
        "exceeds": exceeds,
        "status": status,
        "message": msg,
        "note": _NOTE,
    }


_NOTE = ("한도 소진은 **명목 기준**(약정 방식)이고, 신용환산액(CEE)은 이 거래가 KB 여신에 "
         "계상되는 금액입니다 — 파생은 명목 전액이 여신으로 잡히지 않습니다. "
         "한도액·기사용액은 **고객 자기신고**이며 KB 원장 조회값이 아닙니다. "
         "신용환산율은 바젤 환율계약 add-on 을 참고한 데모 설계값이며 KB 여신정책의 "
         "실제 환산율이 아닙니다. 금리·수수료·스프레드는 산출하지 않습니다(RM 견적).")


# ── 한도 초과 시 대안 라우팅 ─────────────────────────────────────────
def fallback_keys(exceeds: bool, pos: str) -> list[str]:
    """한도가 막혔을 때 **여신을 쓰지 않는** 수단으로 넘긴다.

    막기만 하고 끝내면 RM 일만 늘어난다. 여신 불요 수단이 실제로 존재하므로 그걸 말한다.
    수입 건은 K-SURE 가 수출 전용이라 대안이 없다 — 없는 상품을 지어내지 않고
    선행 과제(여신 협의)를 말한다.
    """
    if not exceeds:
        return []
    if pos == "export":
        # K-SURE 는 공적보험이라 은행 여신을 쓰지 않는다 → 한도와 무관하게 살아남는다.
        return ["환변동보험", "범위선물환"]
    return []
