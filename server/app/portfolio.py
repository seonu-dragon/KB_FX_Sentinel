"""포트폴리오 집계 — 다건 거래의 합산 노출·자연헤지(네팅)·만기 캘린더.

■ 왜 필요한가
도구가 거래 한 건만 보면, 같은 달에 USD 를 받고 또 USD 를 지급하는 기업에게
양쪽 모두 헤지하라고 말한다. 실제로는 상당 부분이 저절로 상계된다.

■ 네팅을 어떻게 계산하는가 (데모보다 엄격하게)
데모 대시보드는 **만기를 무시하고** 수출 USD 총액과 수입 USD 총액을 상계한다.
그건 과대 상계다 — 3월에 받을 달러로 9월에 낼 달러를 막을 수는 있지만, 그 사이
6개월치 환위험과 자금부담이 남는다.

그래서 여기서는 **(통화 × 만기버킷) 안에서만** 상계하고, 버킷을 넘는 상계는
`timing_mismatch` 로 따로 표시한다. 둘을 같은 숫자로 뭉개지 않는다.

■ 합산의 수학적 근거
ES 합산: E[ΣXᵢ] = ΣE[Xᵢ] 는 **상관과 무관하게** 성립하므로 단순 합이 옳다.
가중 BBP: Σwᵢ·pᵢ = "예산환율을 넘길 것으로 기대되는 금액의 비중".
          "한 건이라도 넘을 확률"(1−Π(1−pᵢ))이 **아니다** — 그건 독립을 가정해야 하는데
          같은 통화의 연속 만기는 강하게 상관돼 성립하지 않는다.
          (데모의 분할결제 합산과 같은 논리다.)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

# 만기 버킷 — 영업일 기준. 상계는 같은 버킷 안에서만 인정한다.
BUCKETS = [
    ("0-1M", 0, 21),
    ("1-3M", 22, 63),
    ("3-6M", 64, 126),
    ("6M+", 127, 10_000),
]


def bucket_of(horizon_bd: int) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= horizon_bd <= hi:
            return name
    return BUCKETS[-1][0]


@dataclass
class Leg:
    """집계 입력 한 건 — 진단 결과가 붙은 거래."""
    ref: str
    pos: str            # export | import
    currency: str
    amount: float
    horizon_bd: int
    bbp: float          # 0~1
    es_total_krw: float


def aggregate(legs: list[Leg]) -> dict:
    if not legs:
        return {
            "trade_count": 0, "by_currency": [], "maturity_calendar": [],
            "weighted_bbp_pct": 0.0, "total_es_krw": 0,
            "gross_exposure": {}, "net_after_bucket_netting": {},
            "offset_within_bucket": {}, "timing_mismatch": {},
            "notes": ["집계할 거래가 없습니다"],
        }

    gross: dict[str, float] = defaultdict(float)
    recv: dict[str, float] = defaultdict(float)      # 통화별 수취(수출)
    pay: dict[str, float] = defaultdict(float)       # 통화별 지급(수입)
    # (통화, 버킷) 단위
    b_recv: dict[tuple, float] = defaultdict(float)
    b_pay: dict[tuple, float] = defaultdict(float)

    for lg in legs:
        gross[lg.currency] += lg.amount
        key = (lg.currency, bucket_of(lg.horizon_bd))
        if lg.pos == "export":
            recv[lg.currency] += lg.amount
            b_recv[key] += lg.amount
        else:
            pay[lg.currency] += lg.amount
            b_pay[key] += lg.amount

    # ── 버킷 내 상계(인정) ──────────────────────────────────────
    offset_bucket: dict[str, float] = defaultdict(float)
    net_bucket: dict[str, float] = defaultdict(float)
    for cur in set(list(recv) + list(pay)):
        for name, _lo, _hi in BUCKETS:
            r = b_recv.get((cur, name), 0.0)
            p = b_pay.get((cur, name), 0.0)
            offset_bucket[cur] += min(r, p)
            net_bucket[cur] += abs(r - p)

    # ── 만기 무시 상계(참고용) — 데모 대시보드가 쓰는 방식 ────────
    naive_offset = {cur: min(recv.get(cur, 0.0), pay.get(cur, 0.0))
                    for cur in set(list(recv) + list(pay))}
    # 버킷을 넘어야만 가능한 추가 상계 = 시점 불일치분
    mismatch = {cur: round(naive_offset.get(cur, 0.0) - offset_bucket.get(cur, 0.0), 2)
                for cur in naive_offset}

    total_amt = sum(lg.amount for lg in legs)
    wbbp = sum(lg.bbp * lg.amount for lg in legs) / total_amt if total_amt else 0.0
    total_es = sum(lg.es_total_krw for lg in legs)

    # ── 만기 캘린더 ─────────────────────────────────────────────
    cal = []
    for name, _lo, _hi in BUCKETS:
        items = [lg for lg in legs if bucket_of(lg.horizon_bd) == name]
        if not items:
            continue
        amt = sum(i.amount for i in items)
        cal.append({
            "bucket": name,
            "trade_count": len(items),
            "amount": round(amt, 2),
            "weighted_bbp_pct": round(
                100 * sum(i.bbp * i.amount for i in items) / amt, 1) if amt else 0.0,
            "es_krw": int(round(sum(i.es_total_krw for i in items))),
        })

    by_cur = []
    for cur in sorted(gross):
        by_cur.append({
            "currency": cur,
            "gross": round(gross[cur], 2),
            "receive": round(recv.get(cur, 0.0), 2),
            "pay": round(pay.get(cur, 0.0), 2),
            "offset_within_bucket": round(offset_bucket.get(cur, 0.0), 2),
            "net_exposure": round(net_bucket.get(cur, 0.0), 2),
            "timing_mismatch": mismatch.get(cur, 0.0),
        })

    notes = [
        "상계는 같은 통화 · 같은 만기 구간 안에서만 인정했습니다.",
        "ES 합산은 기댓값의 선형성으로 상관과 무관하게 성립합니다.",
        ("가중 BBP 는 '예산환율을 넘길 것으로 기대되는 금액의 비중'이며, "
         "'한 건이라도 넘을 확률'이 아닙니다(만기 간 상관 때문에 독립 가정이 성립하지 않음)."),
    ]
    if any(v > 0 for v in mismatch.values()):
        notes.append(
            "만기 구간이 다른 수취·지급이 있습니다. 만기를 무시하면 더 많이 상계되는 것처럼 "
            "보이지만, 그 사이 기간의 환위험과 자금부담은 남습니다 — timing_mismatch 로 분리했습니다.")

    return {
        "trade_count": len(legs),
        "by_currency": by_cur,
        "maturity_calendar": cal,
        "weighted_bbp_pct": round(100 * wbbp, 1),
        "total_es_krw": int(round(total_es)),
        "gross_exposure": {k: round(v, 2) for k, v in gross.items()},
        "net_after_bucket_netting": {k: round(v, 2) for k, v in net_bucket.items()},
        "offset_within_bucket": {k: round(v, 2) for k, v in offset_bucket.items()},
        "timing_mismatch": mismatch,
        "notes": notes,
    }
