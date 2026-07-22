"""서버측 자격 판정(ELIG) — 권위 있는 재검증.

■ 왜 서버에 또 있나
데모 HTML 의 `const ELIG` 는 브라우저에서 돈다. 사용자가 콘솔에서
`ELIG["선물환"].ok = () => true` 한 줄이면 가결제 거래에 선물환을 통과시킬 수 있다.
그래서 서버가 **화면이 뭘 보냈든 무시하고 다시 판정한다.**

■ 이중 구현 위험을 어떻게 막나
같은 규칙이 JS·Python 두 곳에 있으면 반드시 갈라진다(이 프로젝트가 이미
"같은 사실을 두 곳에 적으면 갈라진다"는 이유로 ELIG 를 단일 소스로 모은 적이 있다).
그래서 `server/tests/test_elig_parity.py` 가 실제 Chrome 으로 데모의 JS ELIG 를 돌려
**전 조합(2*2*2*2*2 × party 패턴)** 을 Python 결과와 대조한다. 갈라지면 테스트가 죽는다.

규칙 출처: FX_Sentinel_demo_ui.html 의 `const ELIG` (동일 문구·동일 조건).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 글로벌셀러 판정용 패턴 — JS 의 /marketplace|셀러|amazon|정산|플랫폼/i 와 동일.
_SELLER_RE = re.compile(r"marketplace|셀러|amazon|정산|플랫폼", re.IGNORECASE)


@dataclass(frozen=True)
class Facts:
    """판정 입력. 데모 HTML 의 폼 필드(f)와 같은 이름을 쓴다."""
    pos: str = "export"        # export | import
    cert: str = "confirmed"    # confirmed | provisional
    credit: str = "yes"        # yes | no
    cash: str = "ok"           # ok | tight
    biz: str = "corp"          # corp | sole
    settle: str = "fixed"      # fixed(특정일) | window(기간/범위)
    party: str = ""
    name: str = ""


# ── 규칙 ────────────────────────────────────────────────────────────
# 각 항목: (자격 충족 여부, 불충족 사유 한 줄)

def _ok_special(f: Facts) -> bool:
    return f.pos == "export" and f.credit == "no" and f.biz != "sole"


def _deny_special(f: Facts) -> str:
    if f.pos != "export":
        return "수출 거래 전용"
    if f.credit != "no":
        return "여신 보유 기업은 대상 아님"
    return "개인사업자는 대상 아님(법인 전용)"


def _ok_insurance(f: Facts) -> bool:
    # K-SURE 환변동보험은 수출 전용. credit 만 보면 수입 건에도 추천된다(과거 버그).
    return f.pos == "export" and f.credit == "no"


def _deny_insurance(f: Facts) -> str:
    if f.pos != "export":
        return "K-SURE 환변동보험은 수출 거래 전용"
    return "여신 보유 시 선물환 등 은행 상품이 우선"


def _ok_forward(f: Facts) -> bool:
    return f.cert == "confirmed" and f.credit == "yes"


def _deny_forward(f: Facts) -> str:
    if f.cert != "confirmed":
        return "실수요 미확정(가결제) — 취소 시 반대매매 손실 노출"
    return "여신(선물환 한도) 부재"


def _ok_collar(f: Facts) -> bool:
    return f.cert != "confirmed" or f.cash == "tight"


# 기간형 선물환(윈도우 포워드) — 결제일이 특정일이 아니라 기간(범위)일 때.
# 실수요 원칙은 그대로다: 거래 자체가 미확정(가결제)이면 고정이든 기간형이든 막힌다.
# 기간형이 푸는 건 '거래 존재 불확실'이 아니라 '결제일 불확실'이다 — 이 구분을 흐리지 않는다.
def _ok_flex_forward(f: Facts) -> bool:
    return f.cert == "confirmed" and f.credit == "yes" and f.settle == "window"


def _deny_flex_forward(f: Facts) -> str:
    if f.cert != "confirmed":
        return "실수요 미확정(가결제) — 거래 확정 후 가능"
    if f.credit != "yes":
        return "선물환 한도(여신) 필요"
    return "결제일이 확정이면 고정 선물환이 더 저렴"


# 범위선물환(레인지 포워드) — 상·하단 밴드로 관리하되 상단 이익 일부를 유지.
# 여신 보유 → 은행 범위선물환(제로코스트 칼라 밴드). 수출 → K-SURE 범위제한 선물환(무여신 가능).
# 여신 없는 '수입' 건은 둘 다 불가.
def _ok_range_forward(f: Facts) -> bool:
    return f.cert == "confirmed" and (f.credit == "yes" or f.pos == "export")


def _deny_range_forward(f: Facts) -> str:
    if f.cert != "confirmed":
        return "실수요 미확정(가결제) — 거래 확정 후 가능"
    return "여신 없는 수입 건은 범위형 불가 (수출은 K-SURE 범위형 가능)"


def _ok_guarantee(f: Facts) -> bool:
    return f.credit == "no"


def _ok_seller(f: Facts) -> bool:
    if f.biz == "sole":
        return True
    return bool(_SELLER_RE.search((f.party or "") + " " + (f.name or "")))


def _ok_fx_deposit(f: Facts) -> bool:
    return f.pos == "export"


def _ok_fx_loan(f: Facts) -> bool:
    return f.pos == "import" and f.credit == "yes"


def _deny_fx_loan(f: Facts) -> str:
    if f.pos != "import":
        return "외화 결제자금이 필요한 수입 건에 해당"
    return "여신 한도 필요"


RULES: dict[str, tuple] = {
    "특별출연":   (_ok_special,    _deny_special),
    "환변동보험": (_ok_insurance,  _deny_insurance),
    "선물환":     (_ok_forward,    _deny_forward),
    "기간형선물환": (_ok_flex_forward,  _deny_flex_forward),
    "범위선물환":   (_ok_range_forward, _deny_range_forward),
    "칼라":       (_ok_collar,     lambda f: "확정 거래 + 현금 여유 시 선물환이 최저비용"),
    "보증서":     (_ok_guarantee,  lambda f: "여신 보유 기업은 대상 아님"),
    "글로벌셀러": (_ok_seller,     lambda f: "개인사업자·소상공인 글로벌셀러 전용"),
    "외화예금":   (_ok_fx_deposit, lambda f: "외화를 수취하는 수출·정산 건에 해당"),
    "외화대출":   (_ok_fx_loan,    _deny_fx_loan),
}

KEYS = list(RULES.keys())


def eligible(key: str, f: Facts) -> bool:
    r = RULES.get(key)
    return bool(r[0](f)) if r else True


def deny_reason(key: str, f: Facts) -> str:
    r = RULES.get(key)
    return r[1](f) if r else ""


def evaluate_all(f: Facts) -> list[dict]:
    """전 항목 판정. 화면이 무엇을 보냈든 이 결과가 정답이다."""
    out = []
    for k in KEYS:
        ok = eligible(k, f)
        out.append({"key": k, "eligible": ok, "reason": "" if ok else deny_reason(k, f)})
    return out


# ── 가드레일 (자격과 별개인 '차단' 사유) ─────────────────────────────
def guardrails(f: Facts) -> list[str]:
    """자격이 있어도 막아야 하는 것들. 외국환거래법 실수요 원칙에서 온다."""
    blocks: list[str] = []
    if f.cert != "confirmed":
        blocks.append(
            "실수요 미확정(가결제) — 선물환 체결 차단. "
            "인보이스 확정 + 실수요 증빙 확인 후 해제")
    return blocks
