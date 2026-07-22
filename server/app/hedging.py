"""레이어드(분할) 헤지 스케줄.

■ 무엇을 분할하는가 — 비율이 아니라 **실행 시점**
목표 헤지비율은 기존 엔진(`hedge_ratio_from`, 0/50/100)이 정한 값을 **그대로 쓴다.**
레이어드는 그 목표를 여러 차례에 나눠 체결하는 것이다. 이게 기업 재무에서 말하는
layering 의 원래 의미이기도 하다.

■ 왜 목표비율을 다시 정하지 않는가 (설계 실패에서 배운 것)
처음에는 LossAlert 를 25% 단위로 재매핑해 "더 정밀한 목표비율"을 내려 했다. 그러자
LossAlert=0.15 에서 기존 엔진은 50% 헤지, 새 로직은 0% 헤지를 권고했다 —
**같은 입력에 정반대 권고**다. 화면과 서버가 서로 다른 말을 하게 된다.

비율 밴드(0/50/100 을 25 단위로 쪼갤지)는 **KB 의 정책 결정**이지 도구가 임의로
바꿀 사안이 아니다. 임계 0.15/0.35 는 캘리브레이션된 값이고, 그걸 말없이 다시 쓰는 건
개선이 아니라 규칙 분기다. 그래서 여기서는 목표비율을 건드리지 않는다.

■ 레이어드가 해주는 것 / 안 해주는 것
해주는 것: 전량을 한 시점에 체결할 때의 **타이밍 집중 위험** 분산.
안 해주는 것: **기대 손익 개선.** 환율을 예측하지 않으므로 평균적으로 더 나은 환율을
           얻는다는 주장은 불가하다(예측 알파 주장 = 이 프로젝트가 기각한 것).
비용:      체결 건수가 늘어 스프레드·수수료가 증가할 수 있다. 금액은 산출하지 않는다
           (KB 요율 미연동 — 지어내지 않는다).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

STEP = 0.25          # 회차 단위 — 목표비율을 이 크기로 나눠 체결한다


@dataclass(frozen=True)
class Tranche:
    seq: int
    ratio: float             # 이 회차가 담당하는 비율(전체 명목 대비)
    amount: float            # 이 회차 명목
    execute_at_bd: int       # 지금부터 몇 영업일 뒤에 체결
    remaining_bd: int        # 체결 시점에서 만기까지 남는 영업일
    cumulative_ratio: float  # 이 회차까지 누적 헤지비율


def build_schedule(amount: float, horizon_bd: int, target_ratio: float,
                   step: float = STEP) -> dict:
    """목표 헤지비율을 분할 체결 스케줄로 전개.

    `target_ratio` 는 **엔진이 이미 정한 값**을 그대로 받는다(재계산하지 않는다).
    회차 수 = 목표 / step. 만기 전 구간에 균등 배치하되 마지막 회차 뒤에 여유를 남긴다
    (체결·정산 리드타임).
    """
    if not (target_ratio == target_ratio):        # NaN 방어
        target_ratio = 0.0
    target_ratio = max(0.0, min(1.0, float(target_ratio)))

    if target_ratio <= 0 or amount <= 0 or horizon_bd <= 0:
        return {
            "target_ratio": 0.0,
            "step": step,
            "tranches": [],
            "unhedged_ratio": 1.0,
            "note": "목표 헤지비율 0% — 분할 대상 없음",
        }

    n = max(1, int(round(target_ratio / step)))
    per = target_ratio / n                        # 목표를 정확히 채우도록 균등 분할

    tranches: list[Tranche] = []
    cum = 0.0
    for i in range(n):
        at = int(round(horizon_bd * i / n))
        cum += per
        tranches.append(Tranche(
            seq=i + 1,
            ratio=round(per, 6),
            amount=round(amount * per, 2),
            execute_at_bd=at,
            remaining_bd=max(horizon_bd - at, 1),
            cumulative_ratio=round(min(cum, 1.0), 6),
        ))

    unhedged = round(1.0 - target_ratio, 6)
    return {
        "target_ratio": target_ratio,
        "step": step,
        "tranches": [asdict(t) for t in tranches],
        "unhedged_ratio": unhedged,
        "note": (f"목표 {target_ratio:.0%} 를 {n}회로 나눠 체결하는 일정입니다. "
                 "목표비율 자체는 기존 헤지규율(LossAlert)이 정한 값이며 분할이 바꾸지 않습니다. "
                 "분할은 체결 타이밍 집중을 분산할 뿐 기대손익을 개선하지 않으며, "
                 f"미헤지 {unhedged:.0%} 는 만기까지 시장에 노출됩니다. "
                 "체결 건수가 늘어 스프레드·수수료가 증가할 수 있습니다(요율은 RM 견적)."),
    }
