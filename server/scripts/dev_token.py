"""개발용 JWT 발급 — 데모 서버연동 패널에 붙여넣을 토큰.

    python scripts/dev_token.py --role rm --sub rm01

DEV_JWT_SECRET 이 설정돼 있어야 하며, 서버와 같은 값이어야 한다.
운영에서는 이 스크립트를 쓰지 않는다(실 IdP 가 토큰을 발급한다).
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, os.pardir)))

from app.auth import ROLES, issue_dev_token      # noqa: E402
from app.config import settings                  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="rm", choices=list(ROLES))
    ap.add_argument("--sub", default="demo-user")
    ap.add_argument("--ttl", type=int, default=8 * 3600, help="유효기간(초)")
    a = ap.parse_args()

    if not settings.dev_hs256_secret:
        print("DEV_JWT_SECRET 이 설정돼 있지 않습니다.")
        print('  PowerShell:  $env:DEV_JWT_SECRET="dev-only-secret"')
        print("  bash      :  export DEV_JWT_SECRET=dev-only-secret")
        return 1

    print(issue_dev_token(a.sub, a.role, ttl_sec=a.ttl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
