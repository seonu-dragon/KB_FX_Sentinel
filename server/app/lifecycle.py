"""체결 후 라이프사이클 상태머신.

데모는 "상담 요청"까지만 있고 그 뒤가 없었다. 실제 업무는 거기서 시작한다:
서류 받고, 한도 확인하고, 체결하고, 만기에 이행하고, 필요하면 롤오버·부분해지한다.

■ 상태
    draft        진단만 된 상태
    submitted    상담 접수
    docs_review  실수요 증빙 검토
    limit_check  여신·한도 확인
    contracted   헤지 체결
    settled      만기 이행 완료
    rolled_over  만기 연장(재체결)
    cancelled    취소

■ 왜 상태머신인가
전이를 코드 곳곳의 if 로 흩으면 "취소된 건이 체결로 넘어가는" 경로가 반드시 생긴다.
여기서 한 곳에 모아 **허용되지 않은 전이를 거부**한다.

■ 외국환거래법 가드 (데모에서 이어받음)
헤지 체결 후 실수요(원 거래 금액)를 초과하는 부분해지는 **투기 포지션**을 만든다.
그래서 부분해지는 잔여 실수요 범위 안에서만 허용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

DRAFT = "draft"
SUBMITTED = "submitted"
DOCS_REVIEW = "docs_review"
LIMIT_CHECK = "limit_check"
CONTRACTED = "contracted"
SETTLED = "settled"
ROLLED_OVER = "rolled_over"
CANCELLED = "cancelled"

STATES = (DRAFT, SUBMITTED, DOCS_REVIEW, LIMIT_CHECK,
          CONTRACTED, SETTLED, ROLLED_OVER, CANCELLED)

TERMINAL = (SETTLED, CANCELLED)

# 허용 전이 — 여기 없으면 거부된다.
TRANSITIONS: dict[str, tuple] = {
    DRAFT:       (SUBMITTED, CANCELLED),
    SUBMITTED:   (DOCS_REVIEW, CANCELLED),
    DOCS_REVIEW: (LIMIT_CHECK, SUBMITTED, CANCELLED),   # 서류 미비 시 반려
    LIMIT_CHECK: (CONTRACTED, DOCS_REVIEW, CANCELLED),  # 한도 부족 시 반려
    CONTRACTED:  (SETTLED, ROLLED_OVER, CANCELLED),
    ROLLED_OVER: (SETTLED, ROLLED_OVER, CANCELLED),
    SETTLED:     (),
    CANCELLED:   (),
}

# 전이 권한 — 화면 숨김이 아니라 서버가 막는다.
REQUIRED_ROLE: dict[str, tuple] = {
    SUBMITTED:   ("customer", "rm", "branch", "admin"),
    DOCS_REVIEW: ("rm", "branch", "compliance", "admin"),
    LIMIT_CHECK: ("rm", "branch", "admin"),
    CONTRACTED:  ("rm", "branch", "admin"),
    SETTLED:     ("rm", "branch", "admin"),
    ROLLED_OVER: ("rm", "branch", "admin"),
    CANCELLED:   ("customer", "rm", "branch", "admin"),
}


class TransitionError(Exception):
    """허용되지 않은 전이. 조용히 무시하지 않고 거부 사유를 낸다."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Deal:
    deal_id: str
    owner: str
    state: str = DRAFT
    real_demand_amount: float = 0.0    # 원 거래(실수요) 금액
    hedged_amount: float = 0.0         # 현재 헤지 체결 잔액
    history: list = field(default_factory=list)

    # ── 전이 ────────────────────────────────────────────────────
    def can_transition(self, to: str) -> bool:
        return to in TRANSITIONS.get(self.state, ())

    def transition(self, to: str, *, actor: str, role: str, note: str = "") -> None:
        if to not in STATES:
            raise TransitionError(f"알 수 없는 상태: {to}")
        if self.state in TERMINAL:
            raise TransitionError(f"종료된 건입니다({self.state}) — 더 이상 전이할 수 없습니다")
        if not self.can_transition(to):
            allowed = ", ".join(TRANSITIONS.get(self.state, ())) or "(없음)"
            raise TransitionError(
                f"{self.state} → {to} 는 허용되지 않습니다. 가능: {allowed}")
        need = REQUIRED_ROLE.get(to, ())
        if need and role not in need:
            raise TransitionError(f"{to} 전이에는 {', '.join(need)} 권한이 필요합니다")

        prev = self.state
        self.state = to
        self.history.append({"from": prev, "to": to, "actor": actor,
                             "role": role, "note": note, "ts": _now()})

    # ── 헤지 잔액 ────────────────────────────────────────────────
    def contract_hedge(self, amount: float, *, actor: str, role: str) -> None:
        """헤지 체결. 실수요를 초과하면 거부한다(외국환거래법 실수요 원칙)."""
        if amount <= 0:
            raise TransitionError("체결 금액은 0보다 커야 합니다")
        if self.hedged_amount + amount > self.real_demand_amount + 1e-9:
            raise TransitionError(
                f"실수요 초과 — 실수요 {self.real_demand_amount:,.0f} 중 "
                f"이미 {self.hedged_amount:,.0f} 체결됨. "
                f"추가 가능 {max(self.real_demand_amount - self.hedged_amount, 0):,.0f}")
        self.hedged_amount += amount
        self.history.append({"event": "hedge.contract", "amount": amount,
                             "actor": actor, "role": role, "ts": _now()})

    def unwind_hedge(self, amount: float, *, actor: str, role: str) -> None:
        """부분해지. 보유 헤지 잔액을 넘겨 해지하면 반대 포지션(투기)이 된다 — 거부."""
        if amount <= 0:
            raise TransitionError("해지 금액은 0보다 커야 합니다")
        if amount > self.hedged_amount + 1e-9:
            raise TransitionError(
                f"보유 헤지 초과 해지 — 잔액 {self.hedged_amount:,.0f} 를 넘길 수 없습니다. "
                "초과 해지는 실수요 없는 반대 포지션이 됩니다")
        self.hedged_amount -= amount
        self.history.append({"event": "hedge.unwind", "amount": amount,
                             "actor": actor, "role": role, "ts": _now()})

    def reduce_real_demand(self, new_amount: float, *, actor: str, role: str) -> list[str]:
        """원 거래 감액(부분 취소). 헤지가 남으면 초과분 해지를 요구한다."""
        if new_amount < 0:
            raise TransitionError("실수요 금액은 음수일 수 없습니다")
        self.real_demand_amount = new_amount
        warnings = []
        if self.hedged_amount > new_amount + 1e-9:
            warnings.append(
                f"헤지 잔액 {self.hedged_amount:,.0f} 가 축소된 실수요 {new_amount:,.0f} 를 "
                f"초과합니다 — {self.hedged_amount - new_amount:,.0f} 해지가 필요합니다")
        self.history.append({"event": "real_demand.reduce", "to": new_amount,
                             "actor": actor, "role": role, "ts": _now()})
        return warnings

    @property
    def unhedged_amount(self) -> float:
        return max(self.real_demand_amount - self.hedged_amount, 0.0)


class DealStore:
    def __init__(self):
        self._deals: dict[str, Deal] = {}

    def create(self, deal_id: str, owner: str, real_demand_amount: float) -> Deal:
        d = Deal(deal_id=deal_id, owner=owner, real_demand_amount=real_demand_amount)
        self._deals[deal_id] = d
        return d

    def get(self, deal_id: str) -> Optional[Deal]:
        return self._deals.get(deal_id)

    def list(self, owner: str) -> list[Deal]:
        return [d for d in self._deals.values() if d.owner == owner]


_store: Optional[DealStore] = None


def get_store() -> DealStore:
    global _store
    if _store is None:
        _store = DealStore()
    return _store
