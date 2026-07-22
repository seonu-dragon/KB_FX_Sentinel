"""동기 테스트 클라이언트 (starlette.TestClient 대체).

이 환경은 starlette 0.27 + httpx 0.28 조합인데, httpx 0.28 이 `Client(app=...)` 를
제거해서 starlette 의 TestClient 가 import 시점에 죽는다. 사용자 전역 파이썬 환경을
버전 고정으로 흔드는 대신, ASGITransport 를 직접 쓰는 얇은 동기 래퍼를 둔다.

의존성 없음(httpx + anyio 는 이미 fastapi 가 끌고 온다).
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx


class ASGIClient:
    def __init__(self, app, base_url: str = "http://test"):
        self._app = app
        self._base = base_url

    def _run(self, method: str, url: str, **kw) -> httpx.Response:
        async def go() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url=self._base) as c:
                return await c.request(method, url, **kw)

        return asyncio.run(go())

    def get(self, url: str, **kw) -> httpx.Response:
        return self._run("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self._run("POST", url, **kw)

    def put(self, url: str, **kw) -> httpx.Response:
        return self._run("PUT", url, **kw)

    def patch(self, url: str, **kw) -> httpx.Response:
        return self._run("PATCH", url, **kw)

    def delete(self, url: str, **kw) -> httpx.Response:
        return self._run("DELETE", url, **kw)
