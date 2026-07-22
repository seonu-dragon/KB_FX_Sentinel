"""금소법 판매프로세스 — 소비자유형·적정성·손실시나리오·설명의무·꺾기 차단.

■ 왜 이 모듈이 따로 있는가 (ELIG 와 무엇이 다른가)
`elig.py` 는 **"이 상품이 이 거래에 해당하는가"** 를 본다 — 수출인가, 여신이 있는가,
결제일이 기간인가. 상품 쪽 조건이다.

이 모듈은 **"이 고객에게 이걸 권유해도 되는가"** 를 본다 — 파생 손익구조를 이해하는가,
감내 가능한 손실 규모인가, 여신을 미끼로 파생을 끼워파는 모양이 아닌가. 사람 쪽 조건이다.

둘을 한 곳에 섞지 않는 이유: 자격은 **상품 조건이 바뀌면** 바뀌고, 적정성은 **고객이
바뀌면** 바뀐다. 섞으면 "왜 이 회사엔 선물환이 안 뜨지"에 답할 때 두 축을 분리해서
말할 수 없다. 그리고 ELIG 는 이 프로젝트가 어렵게 단일 소스로 모아둔 자산이라
성격이 다른 규칙을 밀어넣어 오염시키지 않는다.

파이프라인:  ELIG(자격) → SUIT(적정성) → 최종 권유 가능
             둘 다 통과해야 권유한다. 하나라도 막히면 사유를 갈라서 말한다.

■ 무엇이 진짜이고 무엇이 데모인가 (정직성)
  · 6대 판매원칙의 **구조**(누구에게 무엇을 확인하고 무엇을 기록하는가)는 실제 금소법 체계다.
  · **문항·배점·임계는 이 데모의 설계값**이다. KB 의 실제 적정성 평가표가 아니다.
    실서비스는 KB 소비자보호부의 승인된 평가표로 교체된다 — 그 사실을 응답에 적는다.
  · 손실 시나리오는 **±σ 이동 가정**이며 예측이 아니다(drift=0, 이 프로젝트의 무예측 원칙).
  · 계약환율·밴드가·프리미엄은 **여기서 만들지 않는다**(products.yaml rate_policy).
    시나리오는 "현재 스냅샷 환율 대비 이동폭"으로만 표현하고, 계약조건은 RM 견적으로 넘긴다.

■ KIKO 가 남긴 교훈이 이 모듈의 설계 이유다
2008 년 문제의 본질은 "은행이 중소기업에 파생을 팔았다"가 아니라 **"위험을 제대로
알리지 않고 팔았다"** 였다. 그래서 이 모듈은 상품을 더 잘 팔게 돕지 않는다 —
**팔면 안 되는 상대를 걸러내고, 판 뒤에 '설명 안 들었다'는 말이 못 나오게 기록**한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

# ── 상품 성격 분류 ───────────────────────────────────────────────────
# 적정성 확인 의무는 **파생상품**에 붙는다. 보험(K-SURE)·여신은 다른 규제 축이다.
# 이 구분을 안 하면 환변동보험에까지 파생 적정성을 요구해서, 정작 무여신 SME 의
# 유일한 헤지 수단을 막아버린다(= 규제를 과하게 적용해 포용을 해치는 실패).
DERIVATIVE_KEYS = ("선물환", "기간형선물환", "범위선물환", "칼라")
CREDIT_KEYS = ("특별출연", "외화대출", "보증서")     # 여신성 상품
INSURANCE_KEYS = ("환변동보험",)                      # 공적보험 — 파생 아님

# 적정성 판정 임계 — 5문항 중 충족 개수.
# 낮추면 부적정을 놓치고, 높이면 정상 고객까지 막는다. 데모 설계값이며 KB 기준 아님.
SUIT_PASS = 4          # 이상: 적정
SUIT_CAUTION = 3       # 이 값: 주의(권유 가능하나 설명 강화)
# SUIT_CAUTION 미만: 부적정 — 파생 권유 차단

# ■ 필수 항목(knock-out) — 점수로 상쇄할 수 없는 문항
#
# 설계 초안은 5문항을 단순 합산했다. 그러자 **감내 가능 손실이 0 인 고객이 4/5 로 '적정'**
# 이 됐다 — 경험 많고 이해하고 현금 여유 있으면, 손실을 못 견뎌도 통과한다는 뜻이다.
# 그건 KIKO 가 정확히 그렇게 났던 경로다(정교한 고객이 감당 못 할 규모를 떠안음).
#
# 감내 가능 여부와 손익구조 이해는 **다른 문항으로 벌충되는 성질이 아니다.** 못 견디면
# 못 견디는 것이고, 이해 못 하면 설명의무가 이행되지 않은 것이다. 그래서 이 둘은
# 가점 항목이 아니라 통과 요건으로 둔다.
KNOCKOUT_KEYS = ("understand", "tolerance")

# 꺾기(구속성 판매) 판정 창 — 여신 실행 전후 이 일수 안의 파생 체결은 소명 대상.
KICKBACK_WINDOW_DAYS = 31

ConsumerType = Literal["일반금융소비자", "전문금융소비자"]


@dataclass(frozen=True)
class ConsumerProfile:
    """고객 쪽 사실. 거래(TradeInput)가 아니라 **사람**에 붙는 정보다."""
    biz: str = "corp"                    # corp | sole
    deriv_exp: str = "none"              # none | limited | experienced
    prior_loss: str = "no"               # yes | no — 과거 파생 손실 경험
    loss_tolerance_krw: float = 0.0      # 감내 가능 손실 (원) — 고객 자기신고
    understands: str = "no"              # yes | no — 손익구조 이해 자가확인
    pro_declared: bool = False           # 자발적 전문금융소비자 전환 신청
    cash: str = "ok"                     # ok | tight — 증거금 대응력 (거래 폼에서 승계)


# ── 1. 소비자 유형 판정 ──────────────────────────────────────────────
def consumer_type(p: ConsumerProfile) -> tuple[ConsumerType, str]:
    """일반 / 전문 금융소비자.

    전문금융소비자가 되면 적합성·적정성·설명의무의 상당 부분이 면제된다. 그래서
    **전문 인정을 보수적으로** 잡는다 — 여기서 헐겁게 잡으면 규제를 우회하는 통로가 된다.

    개인사업자는 전환 대상에서 제외했다. 실제 제도상 일반투자자의 전문 전환은 요건이
    까다롭고, SME 개인사업자를 전문으로 올려 설명의무를 면제하는 건 KIKO 의 실패를
    되풀이하는 길이다.
    """
    if not p.pro_declared:
        return "일반금융소비자", "전문금융소비자 전환 신청 없음 — 보호 규정 전부 적용"
    if p.biz == "sole":
        return "일반금융소비자", "개인사업자는 전문금융소비자 전환 대상에서 제외(보수적 적용)"
    if p.deriv_exp != "experienced":
        return "일반금융소비자", "전환 신청은 있으나 파생 거래경험 요건 미충족"
    return ("전문금융소비자",
            "전환 신청 + 파생 거래경험 확인 — 단, 전환 서면·KB 승인 절차는 RM 확인 사항")


# ── 2. 적정성 확인 (파생상품) ────────────────────────────────────────
@dataclass
class SuitItem:
    key: str
    question: str
    met: bool
    detail: str


def _required_tolerance(scenario_loss_krw: float) -> float:
    """이 거래의 불리 시나리오 손실을 감당하려면 최소 얼마를 감내할 수 있어야 하는가.

    1σ 불리 이동 손실을 기준으로 둔다. 2σ 를 요구하면 거의 모든 SME 가 부적정이 되고,
    0.5σ 를 요구하면 아무도 안 걸린다. 1σ 는 '통상적으로 일어나는 나쁜 날'이다.
    """
    return scenario_loss_krw


def assess_suitability(p: ConsumerProfile, scenario_loss_krw: float) -> dict:
    """5문항 적정성 확인. 파생상품에만 적용된다.

    `scenario_loss_krw` 는 이 거래의 1σ 불리 시나리오 손실(원) — 감내 가능 규모와
    비교하기 위해 받는다. 고정 금액 기준을 쓰면 100만 달러 거래와 1만 달러 거래에
    같은 잣대를 대게 된다.
    """
    need = _required_tolerance(scenario_loss_krw)
    items = [
        SuitItem("exp", "파생상품(선물환·옵션) 거래 경험이 있습니까?",
                 p.deriv_exp != "none",
                 {"none": "거래 경험 없음", "limited": "제한적 경험",
                  "experienced": "충분한 경험"}.get(p.deriv_exp, "미확인")),
        SuitItem("understand", "환율이 반대로 움직일 때의 손익구조를 이해하십니까?",
                 p.understands == "yes",
                 "이해 확인" if p.understands == "yes" else "이해 확인 안 됨"),
        SuitItem("prior", "과거 파생상품 손실 경험 이후에도 감내가 가능합니까?",
                 p.prior_loss == "no" or p.loss_tolerance_krw >= need,
                 "손실 경험 없음" if p.prior_loss == "no"
                 else ("손실 경험 있으나 감내 규모 충족" if p.loss_tolerance_krw >= need
                       else "손실 경험 있고 감내 규모 미달")),
        SuitItem("tolerance",
                 "이 거래의 불리 시나리오 손실을 감내할 수 있습니까?",
                 p.loss_tolerance_krw >= need,
                 f"감내 신고 {p.loss_tolerance_krw:,.0f}원 / 필요 {need:,.0f}원"),
        SuitItem("cash", "증거금·추가담보 요구에 대응할 현금 여유가 있습니까?",
                 p.cash == "ok",
                 "현금 여유 있음" if p.cash == "ok" else "현금 부담 — 추가담보 대응력 취약"),
    ]
    met = sum(1 for i in items if i.met)
    failed_knockout = [i for i in items if i.key in KNOCKOUT_KEYS and not i.met]

    if failed_knockout:
        # 점수와 무관하게 차단. 무엇이 걸렸는지 이름을 대서 말한다 —
        # "부적정"만 통보하면 고객도 RM 도 무엇을 고쳐야 하는지 모른다.
        names = {"understand": "손익구조 이해 미확인", "tolerance": "감내 가능 손실 미달"}
        why = " · ".join(names[i.key] for i in failed_knockout)
        verdict, advisable = f"부적정 — 파생상품 권유 차단 (필수항목: {why})", False
    elif met >= SUIT_PASS:
        verdict, advisable = "적정", True
    elif met == SUIT_CAUTION:
        verdict, advisable = "주의 — 설명 강화 후 권유 가능", True
    else:
        verdict, advisable = "부적정 — 파생상품 권유 차단", False

    return {
        "met": met,
        "total": len(items),
        "verdict": verdict,
        "advisable": advisable,
        "items": [{"key": i.key, "question": i.question, "met": i.met, "detail": i.detail,
                   "required": i.key in KNOCKOUT_KEYS}
                  for i in items],
        "failed_knockout": [i.key for i in failed_knockout],
        "required_tolerance_krw": int(round(need)),
        "note": ("문항·배점·임계는 본 데모의 설계값이며 KB 의 승인된 적정성 평가표가 "
                 "아닙니다. 실서비스는 소비자보호부 표준 평가표로 대체됩니다."),
    }


# ── 3. 손실 시나리오 (예측 아님) ─────────────────────────────────────
def sigma_move(spot: float, sigma_ann: float, horizon_bd: int) -> float:
    """만기까지의 1σ 환율 이동폭(원).

    BBP 와 같은 시간 스케일(√(N/252))을 쓴다. 방향은 넣지 않는다 — drift=0.
    """
    if spot <= 0 or sigma_ann <= 0 or horizon_bd <= 0:
        return 0.0
    return spot * sigma_ann * math.sqrt(horizon_bd / 252.0)


def loss_scenarios(instrument: str, amount: float, spot: float,
                   sigma_ann: float, horizon_bd: int,
                   band_pct: float = 2.0) -> dict:
    """이 수단을 썼을 때 **환율이 반대로 갔을 때** 무슨 일이 생기는가.

    화면이 지금까지 보여준 건 "헤지하면 리스크가 준다"는 유리한 면이었다. 범위선물환은
    특히 '상단 이익 유지'라는 좋은 말만 붙어 있었다. 그 옆에 같은 크기로 불리한 면을 둔다.

    ■ 무엇을 계산하는가
    선물환류(전량 고정): 환율이 고객에게 **유리하게** 움직여도 계약환율로 정산하므로
                         그 차이만큼 기회손실이 난다. 1σ·2σ 이동을 그대로 쓴다.
    범위형·칼라:        밴드 안에서는 시장환율로 정산되므로 밴드폭까지는 참여한다.
                         밴드를 넘어선 초과분만 기회손실이다.

    ■ 무엇을 계산하지 않는가
    계약환율·밴드가·프리미엄은 **만들지 않는다**(rate_policy). 밴드폭은 `가정` 라벨을
    달고 받는다 — S8 의 범위형 경제성 분석이 쓴 것과 같은 규율이다.
    """
    m1 = sigma_move(spot, sigma_ann, horizon_bd)
    m2 = 2.0 * m1
    band = spot * (band_pct / 100.0)

    if instrument in ("범위선물환", "칼라"):
        # 밴드 안은 참여 → 초과분만 포기
        f1, f2 = max(0.0, m1 - band), max(0.0, m2 - band)
        shape = f"밴드 ±{band_pct:g}%(가정) 안에서는 시장환율로 정산, 밴드를 넘어서면 밴드가로 정산"
        margin_call = ("옵션형 구조 — 선납 프리미엄 없음. 다만 은행 내부한도·평가손 관리 대상이며 "
                       "밴드 이탈 폭이 크면 추가담보 요구가 발생할 수 있습니다(RM 확인)")
    elif instrument in ("환변동보험",):
        f1, f2 = m1, m2
        shape = "K-SURE 일반형은 전량 고정 — 환율이 유리하게 가면 그만큼 환수(기회손실)"
        margin_call = "공적보험 — 은행 여신·증거금 불요. 보험료·환수 조건은 K-SURE 약관"
    else:
        f1, f2 = m1, m2
        shape = "계약환율로 전량 고정 — 환율이 유리하게 움직여도 그 이익을 누리지 못함"
        margin_call = ("선물환은 평가손 발생 시 **추가담보·증거금 요구 대상**입니다. "
                       "급변 국면에서 자금부담이 커질 수 있습니다")

    return {
        "instrument": instrument,
        "sigma_move_1": round(m1, 1),
        "sigma_move_2": round(m2, 1),
        "band_krw": round(band, 1) if instrument in ("범위선물환", "칼라") else 0.0,
        "band_pct": band_pct if instrument in ("범위선물환", "칼라") else 0.0,
        "opportunity_loss_1sigma_krw": int(round(f1 * amount)),
        "opportunity_loss_2sigma_krw": int(round(f2 * amount)),
        "shape": shape,
        "margin_call": margin_call,
        "disclaimer": ("환율이 반대로 움직인 경우의 **시나리오**이며 예측이 아닙니다"
                       "(방향 가정 없음 · ±1σ·±2σ 이동). 계약환율·밴드가·수수료는 "
                       "미반영이며 RM 견적 사항입니다."),
    }


# ── 4. 핵심설명서 ────────────────────────────────────────────────────
# 상품별 위험 문안. products.yaml 이 '무엇인가'를 적는다면 여기는 '무엇이 나쁠 수 있는가'다.
_RISK_TEXT = {
    "선물환": [
        "환율이 유리하게 움직여도 계약환율로 정산되어 이익을 누릴 수 없습니다.",
        "평가손 발생 시 추가담보·증거금을 요구받을 수 있습니다.",
        "실수요(원 거래)가 취소되면 반대매매 손실이 발생합니다.",
        "중도 해지 시 시장가 기준 청산비용이 발생합니다.",
    ],
    "기간형선물환": [
        "환율이 유리하게 움직여도 계약환율로 정산되어 이익을 누릴 수 없습니다.",
        "인출 기간 안에 약정 금액을 인출하지 못하면 잔여분 청산비용이 발생합니다.",
        "평가손 발생 시 추가담보·증거금을 요구받을 수 있습니다.",
    ],
    "범위선물환": [
        "밴드 상단을 넘는 유리한 환율의 이익은 포기됩니다.",
        "밴드 하단 아래로 내려가도 밴드가로 정산되어 손실이 제한되지만, 밴드 폭만큼은 손실을 부담합니다.",
        "밴드 이탈 폭이 크면 추가담보 요구가 발생할 수 있습니다.",
        "옵션 조합 상품이므로 중도 해지 비용이 선물환보다 클 수 있습니다.",
    ],
    "칼라": [
        "밴드 상단을 넘는 유리한 환율의 이익은 포기됩니다.",
        "선납 프리미엄이 0 이라는 것은 비용이 없다는 뜻이 아니라, 상단 이익으로 비용을 지불한다는 뜻입니다.",
        "옵션 조합 상품이므로 중도 해지 비용이 선물환보다 클 수 있습니다.",
    ],
    "환변동보험": [
        "환율이 유리하게 움직이면 이익분을 K-SURE 에 환수합니다.",
        "보험료가 발생하며, 청약 한도·보험종목은 K-SURE 심사 사항입니다.",
        "수출 거래 전용이며 수입 결제에는 사용할 수 없습니다.",
    ],
}

_COMMON_RISK = [
    "이 진단은 환율을 예측하지 않습니다. 방향 예측에 근거한 상품 권유가 아닙니다.",
    "표시된 금액은 계약조건(계약환율·밴드가·수수료) 미반영 시나리오이며, 최종 조건은 KB 영업점 견적입니다.",
    "본 화면은 상담 후보 제시까지이며 계약 체결이 아닙니다.",
]


def key_facts_sheet(instrument: str, scenario: dict, consumer: ConsumerType) -> dict:
    """핵심설명서 1장 — 이해확인 체크의 대상이 되는 문서.

    고객이 '설명 못 들었다'고 할 때 은행이 내놓을 수 있는 건 이 문서와 그 확인 기록뿐이다.
    그래서 문서 본문을 **해시 가능한 구조**로 만들어 감사로그에 고정한다(main.py 가 처리).
    """
    risks = list(_RISK_TEXT.get(instrument, ["해당 상품의 위험 문안이 등록되지 않았습니다 — RM 확인 필요"]))
    return {
        "instrument": instrument,
        "consumer_type": consumer,
        "risks": risks + _COMMON_RISK,
        "scenario_summary": (
            f"1σ 불리 시나리오 기회손실 {scenario['opportunity_loss_1sigma_krw']:,}원 · "
            f"2σ {scenario['opportunity_loss_2sigma_krw']:,}원"),
        "margin_call": scenario["margin_call"],
        "explain_duty": ("일반금융소비자" == consumer),
        "note": ("설명의무 이행 기록입니다. 고객 이해확인 체크 시 본 문서 해시가 "
                 "감사로그에 고정됩니다."),
    }


# ── 5. 꺾기(구속성 판매) 차단 ────────────────────────────────────────
def kickback_flags(eligible_keys: list[str],
                   credit_exec_days: Optional[int] = None) -> list[str]:
    """여신을 조건으로 파생을 끼워파는 모양을 잡는다.

    ■ 왜 이게 실제 위험인가
    이 서비스는 같은 화면에서 **여신상품(특별출연·외화대출)** 과 **파생상품(선물환류)** 을
    함께 추천한다. 구조상 "여신 해줄 테니 선물환도 하시죠"로 읽힐 수 있고, 검사에서
    제일 먼저 보는 지점이 여기다.

    ■ 무엇을 하나
    막지는 않는다 — 정당한 동시 니즈가 실제로 존재한다(수출대금 조기화 + 환헤지).
    대신 **플래그를 세워 RM 티켓에 남긴다.** 판단은 사람이 하고, 기록은 시스템이 남긴다.
    """
    flags: list[str] = []
    has_credit = any(k in CREDIT_KEYS for k in eligible_keys)
    has_deriv = any(k in DERIVATIVE_KEYS for k in eligible_keys)

    if has_credit and has_deriv:
        flags.append(
            "여신성 상품과 파생상품이 동시 추천됨 — 구속성 판매(꺾기)로 오인될 수 있습니다. "
            "파생 체결이 여신 조건이 아님을 고객에게 고지하고 그 사실을 기록하십시오.")

    if credit_exec_days is not None and abs(credit_exec_days) <= KICKBACK_WINDOW_DAYS:
        flags.append(
            f"여신 실행일 전후 {KICKBACK_WINDOW_DAYS}일 이내(D{credit_exec_days:+d})의 파생 체결 — "
            "구속성 판매 점검 대상입니다. 준법 확인 없이 진행하지 마십시오.")
    return flags


# ── 6. 통합 게이트 ───────────────────────────────────────────────────
def sales_gate(eligible_keys: list[str], p: ConsumerProfile,
               amount: float, spot: float, sigma_ann: float, horizon_bd: int,
               band_pct: float = 2.0,
               credit_exec_days: Optional[int] = None) -> dict:
    """ELIG 통과 목록을 받아 **권유 가능 여부**까지 판정한다.

    반환의 `advisable_keys` 가 화면이 실제로 권유해도 되는 목록이다.
    `withheld` 는 자격은 있으나 적정성으로 막힌 것 — 사유를 갈라서 돌려준다.
    ("자격이 없다"와 "당신에게 권하지 않는다"는 고객에게 완전히 다른 말이다.)
    """
    ctype, ctype_reason = consumer_type(p)

    # 적정성 판단 기준이 되는 손실 규모는 **가장 표준적인 파생(선물환)** 의 1σ 로 잡는다.
    # 수단마다 다른 기준을 쓰면 "칼라는 통과했는데 선물환은 부적정" 같은 혼란이 생긴다.
    ref = loss_scenarios("선물환", amount, spot, sigma_ann, horizon_bd, band_pct)
    suit = assess_suitability(p, ref["opportunity_loss_1sigma_krw"])

    # 전문금융소비자는 적정성 확인 의무가 면제된다. 다만 **면제된 사실을 기록**한다 —
    # 면제를 조용히 적용하면 나중에 "왜 확인 안 했나"에 답할 근거가 없다.
    exempt = (ctype == "전문금융소비자")
    advisable_deriv = True if exempt else suit["advisable"]

    advisable_keys, withheld = [], []
    for k in eligible_keys:
        if k in DERIVATIVE_KEYS and not advisable_deriv:
            withheld.append({
                "key": k,
                "reason": "적정성 미달 — 파생상품 권유 차단(금융소비자보호법 적정성 원칙)",
                "remedy": "설명 강화 후 재확인, 또는 보험형(K-SURE)·여신형 대안 검토",
            })
        else:
            advisable_keys.append(k)

    scenarios = {k: loss_scenarios(k, amount, spot, sigma_ann, horizon_bd, band_pct)
                 for k in eligible_keys
                 if k in DERIVATIVE_KEYS or k in INSURANCE_KEYS}

    return {
        "consumer_type": ctype,
        "consumer_type_reason": ctype_reason,
        "suitability": suit,
        "suitability_exempt": exempt,
        "advisable_keys": advisable_keys,
        "withheld": withheld,
        "scenarios": scenarios,
        "kickback_flags": kickback_flags(eligible_keys, credit_exec_days),
        "disclaimer": ("금융소비자보호법 6대 판매원칙 중 적합성·적정성·설명의무·"
                       "불공정영업행위 금지를 화면 단계에서 점검합니다. "
                       "문항·임계는 데모 설계값이며 최종 판매 적정성 판단은 "
                       "KB 영업점·소비자보호부 절차를 따릅니다."),
    }
