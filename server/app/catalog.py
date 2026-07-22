"""상품 마스터 로더.

카탈로그는 `server/catalog/products.yaml` 이 마스터다. 코드가 아니라 데이터라서
상품 담당자가 HTML 을 열지 않고 고칠 수 있다.

**라우팅 로직은 여기 없다.** "어떤 상황에 어떤 상품을 먼저 제시하는가"는 조건 분기이지
데이터가 아니므로 코드(recommendPackage / triage)에 남는다. 데이터와 로직을 섞으면
YAML 이 결국 프로그래밍 언어가 된다.

요율·한도도 없다 — KB 상품/약관 DB 미연동이므로 지어내지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

from .config import settings

_CATALOG_PATH = os.environ.get(
    "PRODUCT_CATALOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 os.pardir, "catalog", "products.yaml"))


@dataclass(frozen=True)
class Product:
    code: str
    category: str
    name: str
    source: str
    description: str = ""
    requirement: str = ""
    documents: str = ""
    channels: str = ""
    kb_path: str = ""
    reference: str = ""
    eligibility_key: str = ""
    constraints: dict = field(default_factory=dict)
    rm_checks: list = field(default_factory=list)
    not_a_hedge: bool = False


class Catalog:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.version = ""
        self.rate_policy = ""
        self.categories: list[str] = []
        self.products: list[Product] = []
        self._mtime = 0.0
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"상품 마스터를 찾을 수 없습니다: {self.path}")
        with open(self.path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        self._mtime = os.path.getmtime(self.path)
        self.version = str(doc.get("version", ""))
        self.rate_policy = str(doc.get("rate_policy", ""))
        self.categories = list(doc.get("categories") or [])

        seen: set[str] = set()
        items: list[Product] = []
        for raw in (doc.get("products") or []):
            code = str(raw.get("code") or "").strip()
            if not code:
                raise ValueError("code 가 없는 상품 항목이 있습니다")
            if code in seen:
                raise ValueError(f"상품 코드 중복: {code}")
            seen.add(code)
            cat = str(raw.get("category") or "")
            if self.categories and cat not in self.categories:
                raise ValueError(f"[{code}] 알 수 없는 카테고리: {cat}")
            items.append(Product(
                code=code, category=cat,
                name=str(raw.get("name") or ""),
                source=str(raw.get("source") or ""),
                description=str(raw.get("description") or ""),
                requirement=str(raw.get("requirement") or ""),
                documents=str(raw.get("documents") or ""),
                channels=str(raw.get("channels") or ""),
                kb_path=str(raw.get("kb_path") or ""),
                reference=str(raw.get("reference") or ""),
                eligibility_key=str(raw.get("eligibility_key") or ""),
                constraints=dict(raw.get("constraints") or {}),
                rm_checks=list(raw.get("rm_checks") or []),
                not_a_hedge=bool(raw.get("not_a_hedge", False)),
            ))
        self.products = items

    def maybe_reload(self) -> None:
        """상품 담당자가 YAML 을 고치면 재시작 없이 반영된다.
        (제재 리스트에서 겪은 것과 같은 함정 — 캐시해두고 안 읽으면 낡은 문안을 계속 낸다.)"""
        try:
            if os.path.getmtime(self.path) != self._mtime:
                self.load()
        except OSError:
            pass

    # ── 조회 ────────────────────────────────────────────────────
    def all(self) -> list[Product]:
        self.maybe_reload()
        return list(self.products)

    def by_code(self, code: str) -> Optional[Product]:
        self.maybe_reload()
        for p in self.products:
            if p.code == code:
                return p
        return None

    def filter(self, category: str = "", query: str = "") -> list[Product]:
        self.maybe_reload()
        out = self.products
        if category and category != "all":
            out = [p for p in out if p.category == category]
        if query:
            q = query.strip().lower()
            out = [p for p in out
                   if q in p.name.lower() or q in p.description.lower()]
        return list(out)

    def eligibility_keys(self) -> dict[str, str]:
        """상품코드 → ELIG 키. 자격 판정은 elig.py 단일 소스에 위임한다."""
        self.maybe_reload()
        return {p.code: p.eligibility_key for p in self.products if p.eligibility_key}


_catalog: Optional[Catalog] = None


def get_catalog() -> Catalog:
    global _catalog
    if _catalog is None:
        _catalog = Catalog(_CATALOG_PATH)
    return _catalog
