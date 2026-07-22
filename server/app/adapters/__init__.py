"""KB 자산 어댑터 경계 — **여기부터는 우리가 만들 수 없는 영역이다.**

이 패키지의 목적은 기능 제공이 아니라 **경계의 명시**다.

아래 어댑터들은 KB 내부 시스템에 접속해야 동작한다. 그건 코딩 속도의 문제가 아니라
계약·보안심사·망분리의 문제이고, 외부에서는 어떤 방법으로도 얻을 수 없다:

    · 기업뱅킹 SSO(IdP)      — KB 가 OIDC 클라이언트를 발급해야 함
    · 원장·여신 한도 조회     — KB 코어뱅킹. 외부 API 없음
    · KB 고시환율             — KB 내부 고시 테이블
    · 상품 요율·한도          — 상품/약관 DB
    · UNIPASS 수출입신고      — 관세청. 사업자 등록·이용신청 필요

따라서 각 어댑터는 **NotImplementedError 를 던진다.** 그럴듯한 가짜 값을 반환하지 않는다.
가짜 원장 연동은 이 프로젝트의 유일한 진짜 자산(정직성)을 태워먹는 짓이다.

호출부는 `UnavailableAdapter` 예외를 잡아 "RM 확인 필요"로 강등해야 하며,
그 강등 사실이 사용자 응답과 감사로그에 남아야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class UnavailableAdapter(NotImplementedError):
    """KB 연동 전이라 값을 만들 수 없음. 호출부는 이걸 '미확인'으로 강등한다."""

    def __init__(self, system: str, needs: str):
        self.system = system
        self.needs = needs
        super().__init__(f"{system} 미연동 — 필요: {needs}")


@dataclass(frozen=True)
class AdapterStatus:
    system: str
    connected: bool
    blocker: str
    substitute: str      # 연동 전 무엇으로 대신하는가 (숨기지 않고 밝힌다)


class LedgerAdapter(Protocol):
    """원장·여신 한도. 실수요 대조와 한도 확인의 근거."""

    def credit_line(self, customer_id: str) -> dict: ...
    def real_demand_evidence(self, customer_id: str, trade_ref: str) -> dict: ...


class MarketAdapter(Protocol):
    """KB 고시환율. 공개 API 참고시세와는 법적 지위가 다르다."""

    def official_rate(self, pair: str, asof: str) -> dict: ...


class ProductMasterAdapter(Protocol):
    """상품 마스터 — 요율·한도·약관 버전."""

    def quote(self, product_code: str, trade: dict) -> dict: ...


# ── 미연동 스텁 ─────────────────────────────────────────────────────
class UnconnectedLedger:
    SYSTEM = "KB 원장·여신"

    def credit_line(self, customer_id: str) -> dict:
        raise UnavailableAdapter(self.SYSTEM, "코어뱅킹 조회 권한 + 전용망")

    def real_demand_evidence(self, customer_id: str, trade_ref: str) -> dict:
        raise UnavailableAdapter(self.SYSTEM, "원장 실수요 대조 + 증빙 보관소")


class UnconnectedMarket:
    SYSTEM = "KB 고시환율"

    def official_rate(self, pair: str, asof: str) -> dict:
        raise UnavailableAdapter(self.SYSTEM, "고시 테이블 조회 계약")


class UnconnectedProductMaster:
    SYSTEM = "KB 상품 마스터"

    def quote(self, product_code: str, trade: dict) -> dict:
        raise UnavailableAdapter(self.SYSTEM, "상품/약관 DB + 가격엔진")


def status_report() -> list[AdapterStatus]:
    """`GET /v1/integrations` 가 그대로 노출한다. 심사위원이 경계를 눈으로 본다."""
    return [
        AdapterStatus("기업뱅킹 SSO", False,
                      "KB IdP 가 OIDC 클라이언트를 발급해야 함",
                      "자체 호스팅 OIDC(Keycloak) — 프로토콜은 실물, 신원은 KB 것이 아님"),
        AdapterStatus("KB 원장·여신 한도", False,
                      "코어뱅킹 조회 권한·전용망",
                      "고객 입력값(credit=yes/no) — RM 확인 대상으로 강등 표시"),
        AdapterStatus("KB 고시환율", False,
                      "고시 테이블 조회 계약",
                      "기준일 스냅샷 + 공개 API 참고시세(고시환율 아님을 명시)"),
        AdapterStatus("KB 상품 요율·한도", False,
                      "상품/약관 DB·가격엔진",
                      "상품 후보 제시까지만 — 요율은 RM 견적으로 이관"),
        AdapterStatus("관세청 UNIPASS", False,
                      "사업자 등록·이용신청",
                      "증빙 수기 확정 경로"),
        AdapterStatus("RM 업무함/CRM", False,
                      "행내 업무 시스템 연동",
                      "서버 내 티켓 저장 — 실제 RM 에게 전달되지 않음"),
        # 아래는 **실제로 연동된 것**. 위와 섞이지 않게 분리해 표시한다.
        AdapterStatus("제재 리스트(OFAC/UN/EU)", True,
                      "-",
                      "공개 리스트 실수집 + 퍼지매칭 — 실물 구현"),
    ]
