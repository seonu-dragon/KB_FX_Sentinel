"""
FX Sentinel — Advisor 지식베이스 (RAG-lite, 최소구현)
=====================================================
설계 근거: B 문서(Advisor 지식베이스) §2 택소노미 · §5 검색 · §7 가드레일
우선 도메인: D4(수출입 보증·보험) · D5(정책자금) — B §9 구축순서 1.

MVP 원칙:
 - 소규모 큐레이션 KB + 키워드/도메인 매칭(sparse). dense(임베딩)은 고도화.
 - 모든 항목 출처·발행시점 명시. 수치는 '공개자료 기반, 확인 필요'로 라벨(가드레일).
 - 범위 밖(법률·세무 단정)은 회피 문구로.
"""
from __future__ import annotations

# 신뢰등급: A=공공·규범 원문, B=은행/2차 정리(가정 표기), C=뉴스·해설
KB_ENTRIES = [
    {
        "id": "D4-hedge-insurance", "domain": "D4", "trust": "A",
        "title": "환변동보험 (K-SURE)",
        "tags": ["환변동보험", "보험", "무여신", "여신없음", "담보부족", "K-SURE", "환위험"],
        "summary": "한국무역보험공사(K-SURE)의 환변동보험은 수출·수입 기업이 환율 변동 위험을 "
                   "공적 보험으로 관리하는 제도. 보장환율 기준으로, 만기환율이 불리하면 보험금을 받고 "
                   "유리하면 환수금을 납부하는 선물환-유사 정산 구조. 은행 여신 한도가 없어도 이용 가능한 것이 특징.",
        "source": "한국무역보험공사(K-SURE) 환변동보험 안내", "source_url": "https://www.ksure.or.kr",
        "published": "공개자료 기반(시점·요율 확인 필요)",
    },
    {
        "id": "D4-credit-chain", "domain": "D4", "trust": "B",
        "title": "여신창출 상생체인 (K-SURE 보증 → 은행 선물환 한도)",
        "tags": ["여신창출", "무여신", "담보부족", "보증", "선물환한도", "상생금융"],
        "summary": "담보 부족으로 선물환 여신 한도가 없는 기업의 경우, K-SURE 등 공적 보증을 연계해 "
                   "은행의 신용위험을 낮추고 선물환 한도를 창출하는 방식을 검토할 수 있다. 여신 사각지대 "
                   "중소기업 포용(상생금융). ※ 구체 상품·절차·한도는 KB·K-SURE 실제 협약 범위 확인 필요(가정).",
        "source": "KB 기업금융·K-SURE 협약(공개자료 기반 가정)", "source_url": "",
        "published": "가정 — 본선 전 확인 필요",
    },
    {
        "id": "D3-collar", "domain": "D2/D6", "trust": "B",
        "title": "제로코스트 칼라 (통화옵션)",
        "tags": ["칼라", "옵션", "가결제", "취소", "현금부담", "제로코스트", "프리미엄"],
        "summary": "수출기업이 달러 풋 매수(하방 방어) + 달러 콜 매도(상방 일부 양보)를 결합해 "
                   "선납 프리미엄을 0에 가깝게 만드는 헤지 기법. 초기 현금부담이 없고, 계약 취소 시 "
                   "손실이 프리미엄 수준으로 유계라 가결제(취소가능) 단계에 적합. ※ 숏콜 다리는 여신 한도 필요.",
        "source": "일반 외환 실무(개념 안내)", "source_url": "",
        "published": "개념 설명(개별 상품 조건은 영업점 확인)",
    },
    {
        "id": "D1-forward", "domain": "D2", "trust": "B",
        "title": "선물환 (표준 헤지)",
        "tags": ["선물환", "확정", "여신", "표준헤지", "스왑포인트"],
        "summary": "결제 만기에 맞춰 환율을 미리 고정하는 표준 헤지. 커버드 금리평가(CIP)에 따라 "
                   "선물환율은 현물 ± 스왑포인트(내외 금리차)로 결정. 확정 익스포저(인보이스·수출신고필증)에 적합. "
                   "여신 한도가 필요하고, 계약 취소 시 반대매매 비용이 발생할 수 있다.",
        "source": "일반 외환 실무(개념 안내)", "source_url": "",
        "published": "개념 설명",
    },
    {
        "id": "D5-voucher", "domain": "D5", "trust": "A",
        "title": "수출바우처 · 정책자금 (중소 수출기업)",
        "tags": ["정책자금", "수출바우처", "지원사업", "중소기업", "기업마당"],
        "summary": "중소·중견 수출기업 대상 정책자금·지원사업(예: 수출바우처, 무역보험 지원, 지자체 수출지원 등)은 "
                   "업종·규모·수출실적 등 자격요건에 따라 신청 가능. 최신 공고는 기업마당(bizinfo) 등에서 확인. "
                   "회사 조건에 맞는 후보를 매칭해 안내(신청은 해당 기관·영업점).",
        "source": "기업마당(bizinfo)·중진공 등 공개 공고", "source_url": "https://www.bizinfo.go.kr",
        "published": "공고성 — 최신 유효 공고 확인 필요",
    },
    {
        "id": "D5-country-risk", "domain": "D5", "trust": "B",
        "title": "거래국 리스크 · 무역통계 참고",
        "tags": ["거래국", "국가리스크", "무역통계", "수입"],
        "summary": "거래 상대국의 경제상황·환율 변동은 대금 회수·결제 리스크에 영향. 국가별 경제지표·무역통계는 "
                   "한국은행 ECOS·관세청·무역협회(KITA) 등에서 확인 가능하며, 리스크가 높은 거래국은 보증·보험 "
                   "활용을 함께 검토.",
        "source": "한국은행 ECOS·관세청·KITA(공개 통계)", "source_url": "",
        "published": "개념 안내",
    },
]

GUARDRAIL = ("※ 본 안내는 정보 제공이며 법률·세무·계약 효력에 대한 확정 자문이 아닙니다. "
             "수치·요율·자격은 시점에 따라 변동하며 최종 확인·실행은 KB 영업점·해당 기관을 통해 진행하세요.")


def retrieve(tags: list[str], top_k: int = 3) -> list[dict]:
    """상황 태그와 KB 항목 태그의 겹침 점수로 sparse 검색."""
    want = set(t.lower() for t in tags)
    scored = []
    for e in KB_ENTRIES:
        etags = set(t.lower() for t in e["tags"])
        overlap = len(want & etags)
        if overlap:
            scored.append((overlap, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:top_k]]


def situation_tags(profile) -> list[str]:
    """회사 프로필 → 검색 태그 (Advisor가 무엇을 안내해야 하는지)."""
    tags = ["환위험"]
    if not profile.has_credit_line:
        tags += ["무여신", "여신창출", "환변동보험", "담보부족"]
    if profile.certainty == "provisional":
        tags += ["가결제", "취소", "칼라", "옵션"]
    else:
        tags += ["확정", "선물환"]
    if profile.cash_constrained:
        tags += ["현금부담", "칼라"]
    if profile.position == "import":
        tags += ["수입", "거래국"]
    tags += ["정책자금"]  # 항상 정책자금 후보 노출(#6 정보제공)
    return tags
