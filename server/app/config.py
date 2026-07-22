"""서버 설정 + 엔진 경로 부트스트랩.

이 서버의 존재 이유는 하나다: **판정 권위를 클라이언트에서 서버로 옮기는 것**.
데모 HTML(FX_Sentinel_demo_ui.html)은 브라우저에서 BBP·ELIG를 계산하는데,
그건 사용자가 콘솔에서 얼마든지 바꿀 수 있다. 실서비스에서는 화면이 무엇을 보내든
서버가 다시 계산하고 다시 판정한다.

데모 HTML 은 이 서버 없이도 그대로 동작한다(단일 파일 이식성 보존).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

# ── fx_sentinel 엔진을 import 가능하게 ─────────────────────────────
# 서버는 BBP 를 **다시 구현하지 않는다**. 이미 검증된 fx_sentinel.bbp 를 그대로 쓴다.
# (JS·Python 이중 구현이 이미 drift 위험인데 세 번째를 만들면 안 된다.)
_HERE = os.path.dirname(os.path.abspath(__file__))
_KB_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
if _KB_ROOT not in sys.path:
    sys.path.insert(0, _KB_ROOT)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_list(key: str) -> list[str]:
    return [x.strip() for x in os.environ.get(key, "").split(",") if x.strip()]


@dataclass
class Settings:
    # ── 모델·엔진 버전 스탬프 ────────────────────────────────────
    # 감사로그에 박힌다. "그때 무슨 모델이 그렇게 판정했나"에 답하기 위한 것.
    engine_version: str = "fx_sentinel.bbp/2026-07-06"
    api_version: str = "v1"
    nu: float = 5.0                       # Student-t 자유도 (엔진 기본과 일치)

    # ── 기준일 시세 스냅샷 ───────────────────────────────────────
    # 데모 HTML 과 같은 값. KB 고시환율이 아니다.
    # 실서비스에서는 adapters.market.KBRateAdapter 가 대체한다(현재 미연동 스텁).
    snapshot_date: str = "2026-07-06"
    snapshot_spot: float = 1528.8
    snapshot_sigma_ann: float = 0.098
    snapshot_fx_ewi: float = 38.0
    snapshot_iz: int = 0

    # ── 인증 ─────────────────────────────────────────────────────
    # 자체 호스팅 IdP(Keycloak 등). **KB 기업뱅킹 SSO 가 아니다** — 프로토콜만 실물.
    oidc_issuer: str = field(default_factory=lambda: os.environ.get("OIDC_ISSUER", ""))
    oidc_audience: str = field(default_factory=lambda: os.environ.get("OIDC_AUDIENCE", "fx-sentinel"))
    oidc_jwks_url: str = field(default_factory=lambda: os.environ.get("OIDC_JWKS_URL", ""))
    # 로컬 개발용 대칭키 모드. 운영에서는 반드시 OIDC_ISSUER/JWKS 를 설정한다.
    dev_hs256_secret: str = field(default_factory=lambda: os.environ.get("DEV_JWT_SECRET", ""))

    cors_origins: list[str] = field(default_factory=lambda: _env_list("ALLOW_ORIGINS"))

    # ── 감사로그 ─────────────────────────────────────────────────
    audit_db: str = field(default_factory=lambda: os.environ.get(
        "AUDIT_DB", os.path.join(_KB_ROOT, "server", "var", "audit.sqlite3")))

    # ── 제재 리스트 ──────────────────────────────────────────────
    sanctions_dir: str = field(default_factory=lambda: os.environ.get(
        "SANCTIONS_DIR", os.path.join(_KB_ROOT, "server", "var", "sanctions")))
    # 네트워크 수집을 허용할지. 기본 false — 오프라인/폐쇄망에서도 서버가 떠야 한다.
    sanctions_allow_fetch: bool = field(default_factory=lambda: _env_bool("SANCTIONS_ALLOW_FETCH", False))

    @property
    def auth_configured(self) -> bool:
        return bool(self.oidc_jwks_url or self.dev_hs256_secret)


settings = Settings()
