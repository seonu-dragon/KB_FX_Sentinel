"""인증(OIDC) + 서버측 RBAC.

■ 무엇이 진짜이고 무엇이 아닌가 — 먼저 명확히
진짜: OIDC/JWT 검증(서명·iss·aud·exp), 역할 기반 접근제어를 **서버가 강제**한다는 것.
아님: 이 IdP 는 **KB 기업뱅킹 SSO 가 아니다.** 자체 호스팅(Keycloak 등) 또는 개발용
      대칭키다. KB 신원과 연결하려면 KB 가 OIDC 클라이언트를 발급해야 하고,
      그건 계약·보안심사 사안이라 코드로 앞당길 수 없다.

데모의 `RM_PASS="1234"` 는 클라이언트 변수라 F12 로 읽힌다. 그건 인증이 아니라 연출이다.
여기서는 토큰 서명을 검증하고, 역할을 토큰에서 읽고, 권한 판단을 서버에서 한다.

■ 역할 (데모 HTML 의 RBAC 표와 같은 축)
    customer   고객 — 자기 진단·티켓 생성
    rm         영업점 RM — 고객 건 조회·검증 도구·시장상태 주입
    branch     지점장 — RM 권한 + 승인
    compliance 준법/AML — 제재 조회·감사로그 열람
    admin      관리자 — 감사로그 검증·통합 상태
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional

import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import settings

ROLES = ("customer", "rm", "branch", "compliance", "admin")


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    issuer: str

    def has(self, *allowed: str) -> bool:
        return self.role in allowed


class _JWKSCache:
    """JWKS 를 매 요청마다 받아오면 IdP 를 DDoS 한다. TTL 캐시."""

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._client: Optional[object] = None
        self._at: float = 0.0

    def client(self):
        if not settings.oidc_jwks_url:
            return None
        now = time.time()
        if self._client is None or (now - self._at) > self.ttl:
            self._client = jwt.PyJWKClient(settings.oidc_jwks_url)
            self._at = now
        return self._client


_jwks = _JWKSCache()


def _decode(token: str) -> dict:
    """서명·만료·발급자·수신자 검증. 하나라도 어긋나면 예외."""
    if settings.oidc_jwks_url:
        client = _jwks.client()
        key = client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256", "ES256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer or None,
            options={"require": ["exp", "sub"]},
        )
    if settings.dev_hs256_secret:
        # 개발 전용. 운영에서 이 경로로 뜨면 안 되므로 main 이 기동 시 경고한다.
        return jwt.decode(
            token, settings.dev_hs256_secret, algorithms=["HS256"],
            audience=settings.oidc_audience,
            options={"require": ["exp", "sub"]},
        )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="인증 미설정 — OIDC_JWKS_URL 또는 DEV_JWT_SECRET 이 필요합니다")


def current_principal(authorization: str = Header(default="")) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bearer 토큰이 필요합니다",
                            headers={"WWW-Authenticate": "Bearer"})
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = _decode(token)
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="토큰 만료")
    except Exception as e:
        # 실패 사유를 그대로 흘리면 공격자에게 힌트가 된다. 로그에만 남긴다.
        raise HTTPException(status_code=401, detail="토큰 검증 실패") from e

    role = str(claims.get("role") or "").strip().lower()
    if role not in ROLES:
        # 역할이 없거나 모르는 값이면 **가장 낮은 권한으로 떨어뜨린다**(fail-closed).
        role = "customer"
    return Principal(subject=str(claims.get("sub") or "unknown"),
                     role=role,
                     issuer=str(claims.get("iss") or ""))


def require(*allowed: str):
    """역할 게이트. 화면 숨김이 아니라 서버 거부다."""

    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has(*allowed):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"권한 없음 — 필요 역할: {', '.join(allowed)}")
        return p

    return _dep


def issue_dev_token(subject: str, role: str, ttl_sec: int = 3600) -> str:
    """개발·테스트 전용 토큰 발급. DEV_JWT_SECRET 이 설정된 경우에만 동작한다."""
    if not settings.dev_hs256_secret:
        raise RuntimeError("DEV_JWT_SECRET 미설정 — 개발 토큰을 발급할 수 없습니다")
    if role not in ROLES:
        raise ValueError(f"알 수 없는 역할: {role}")
    now = int(time.time())
    return jwt.encode(
        {"sub": subject, "role": role, "aud": settings.oidc_audience,
         "iat": now, "exp": now + ttl_sec, "iss": "dev-local"},
        settings.dev_hs256_secret, algorithm="HS256")
