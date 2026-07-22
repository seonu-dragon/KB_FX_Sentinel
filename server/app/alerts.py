"""알림 규칙 엔진 — 구독 정의와 평가.

■ 무엇이 진짜인가
규칙 정의·평가·중복억제는 실제로 동작한다. **발송은 미연동이다** — 문자·이메일·앱푸시는
행내 발송 채널이 필요하다. 평가 결과는 '발송 대기'로 남고, 그 사실을 응답에 적는다.
"알림을 보냈다"고 하면 고객은 받았다고 믿는다.

■ 규칙 종류
    maturity     만기 D-N 도달
    bbp_above    BBP 가 임계 초과
    regime       시장 위험게이지(FX-EWI) 등급이 경계 이상
    unhedged     만기가 가까운데 헤지비율이 목표에 못 미침

■ 알림 피로(alert fatigue)를 어떻게 막나
같은 규칙·같은 거래에 대해 `cooldown_days` 안에는 다시 울리지 않는다.
매일 같은 알림이 오면 사람은 규칙을 끄거나 무시한다 — 그러면 정작 중요한 순간에도 안 본다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

RuleKind = Literal["maturity", "bbp_above", "regime", "unhedged"]

VALID_KINDS = ("maturity", "bbp_above", "regime", "unhedged")

# 게이지 등급 순서 — grade_of() 와 같은 축
GRADE_ORDER = ["정상", "주의", "경계", "심각"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Rule:
    rule_id: str
    owner: str
    kind: str
    # 규칙별 파라미터
    days_before: int = 7          # maturity
    threshold_pct: float = 50.0   # bbp_above
    min_grade: str = "경계"        # regime
    min_hedge_ratio: float = 0.5  # unhedged
    cooldown_days: int = 3
    enabled: bool = True
    created_at: str = ""
    # 마지막 발화 시각 (거래 ref 별)
    last_fired: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"알 수 없는 규칙 종류: {self.kind}")
        if self.kind == "maturity" and not (0 <= self.days_before <= 250):
            raise ValueError("days_before 는 0~250 영업일이어야 합니다")
        if self.kind == "bbp_above" and not (0 <= self.threshold_pct <= 100):
            raise ValueError("threshold_pct 는 0~100 이어야 합니다")
        if self.kind == "regime" and self.min_grade not in GRADE_ORDER:
            raise ValueError(f"알 수 없는 등급: {self.min_grade}")
        if self.kind == "unhedged" and not (0 <= self.min_hedge_ratio <= 1):
            raise ValueError("min_hedge_ratio 는 0~1 이어야 합니다")
        if self.cooldown_days < 0:
            raise ValueError("cooldown_days 는 음수일 수 없습니다")


@dataclass
class Signal:
    """평가 대상 — 거래 한 건의 현재 상태."""
    ref: str
    horizon_bd: int
    bbp_pct: float
    hedge_ratio: float
    gauge_grade: str
    name: str = ""


@dataclass
class Alert:
    alert_id: str
    rule_id: str
    kind: str
    ref: str
    message: str
    fired_at: str
    delivery: str


DELIVERY_NOTE = ("발송 채널 미연동 — 서버에 '발송 대기'로 기록될 뿐 "
                 "문자·이메일·앱푸시로 전달되지 않습니다")


def _fires(rule: Rule, s: Signal) -> Optional[str]:
    """규칙이 울리는가. 울리면 사람이 읽는 메시지를 돌려준다."""
    who = s.name or s.ref
    if rule.kind == "maturity":
        if s.horizon_bd <= rule.days_before:
            return f"{who} · 결제 만기 D-{s.horizon_bd} 임박 (기준 D-{rule.days_before})"
        return None
    if rule.kind == "bbp_above":
        if s.bbp_pct > rule.threshold_pct:
            return (f"{who} · 예산환율 초과 가능성 {s.bbp_pct:.1f}% "
                    f"(기준 {rule.threshold_pct:.0f}% 초과)")
        return None
    if rule.kind == "regime":
        try:
            cur = GRADE_ORDER.index(s.gauge_grade)
            need = GRADE_ORDER.index(rule.min_grade)
        except ValueError:
            return None
        if cur >= need:
            return f"시장 위험게이지 '{s.gauge_grade}' — 기준 '{rule.min_grade}' 이상"
        return None
    if rule.kind == "unhedged":
        # 만기가 규칙 기준 안으로 들어왔는데 헤지가 목표에 못 미치는 경우
        if s.horizon_bd <= rule.days_before and s.hedge_ratio < rule.min_hedge_ratio:
            return (f"{who} · 만기 D-{s.horizon_bd} 인데 헤지비율 {s.hedge_ratio:.0%} "
                    f"(기준 {rule.min_hedge_ratio:.0%} 미만)")
        return None
    return None


def evaluate(rules: list[Rule], signals: list[Signal],
             now: Optional[datetime] = None) -> list[Alert]:
    """규칙 × 거래 평가. 쿨다운 안이면 건너뛴다."""
    now = now or _now()
    out: list[Alert] = []
    for rule in rules:
        if not rule.enabled:
            continue
        for s in signals:
            msg = _fires(rule, s)
            if not msg:
                continue
            last = rule.last_fired.get(s.ref)
            if last:
                try:
                    prev = datetime.fromisoformat(last)
                    if (now - prev) < timedelta(days=rule.cooldown_days):
                        continue      # 쿨다운 — 같은 말을 매일 하지 않는다
                except ValueError:
                    pass
            rule.last_fired[s.ref] = now.isoformat(timespec="seconds")
            out.append(Alert(
                alert_id=uuid.uuid4().hex[:12],
                rule_id=rule.rule_id, kind=rule.kind, ref=s.ref,
                message=msg,
                fired_at=now.isoformat(timespec="seconds"),
                delivery=DELIVERY_NOTE,
            ))
    return out


# ── 저장소 (프로세스 내) ────────────────────────────────────────────
class RuleStore:
    """구독 저장소. 영속화는 감사로그와 달리 아직 파일이 아니다 — 그 한계를 밝힌다."""

    def __init__(self):
        self._rules: dict[str, Rule] = {}

    def add(self, owner: str, kind: str, **kw) -> Rule:
        r = Rule(rule_id=uuid.uuid4().hex[:12], owner=owner, kind=kind,
                 created_at=_now().isoformat(timespec="seconds"),
                 **{k: v for k, v in kw.items() if v is not None})
        r.validate()
        self._rules[r.rule_id] = r
        return r

    def list(self, owner: str) -> list[Rule]:
        return [r for r in self._rules.values() if r.owner == owner]

    def get(self, rule_id: str) -> Optional[Rule]:
        return self._rules.get(rule_id)

    def delete(self, rule_id: str, owner: str) -> bool:
        r = self._rules.get(rule_id)
        if r and r.owner == owner:
            del self._rules[rule_id]
            return True
        return False


_store: Optional[RuleStore] = None


def get_store() -> RuleStore:
    global _store
    if _store is None:
        _store = RuleStore()
    return _store
