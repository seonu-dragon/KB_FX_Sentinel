"""상품 마스터 — 로딩·무결성·데모 동기화·자격 위임."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import Catalog, get_catalog     # noqa: E402
from app.elig import KEYS as ELIG_KEYS           # noqa: E402
from app.elig import Facts, eligible             # noqa: E402


# ── 무결성 ──────────────────────────────────────────────────────────
def test_catalog_loads():
    c = get_catalog()
    assert c.products, "상품이 하나도 없다"
    assert c.version, "카탈로그 버전이 비어 있다"


def test_product_codes_are_unique():
    codes = [p.code for p in get_catalog().all()]
    assert len(codes) == len(set(codes))


def test_every_product_has_a_kb_path():
    """어디서 신청하는지 없는 상품 카드는 고객에게 쓸모가 없다."""
    missing = [p.code for p in get_catalog().all() if not p.kb_path]
    assert not missing, f"kb_path 누락: {missing}"


def test_eligibility_keys_exist_in_elig_module():
    """카탈로그가 존재하지 않는 자격 키를 가리키면 판정이 조용히 통과된다."""
    bad = [(p.code, p.eligibility_key) for p in get_catalog().all()
           if p.eligibility_key and p.eligibility_key not in ELIG_KEYS]
    assert not bad, f"elig.py 에 없는 자격 키: {bad}"


def test_catalog_carries_no_rates():
    """요율·한도를 지어내지 않는다 — 미연동 시스템의 값이다."""
    import io
    raw = io.open(os.path.join(ROOT, "catalog", "products.yaml"), encoding="utf-8").read()
    for banned in ("금리:", "rate:", "수수료율:", "limit_krw", "spread:"):
        assert banned not in raw, f"카탈로그에 요율/한도로 보이는 항목: {banned}"


def test_unknown_category_is_rejected():
    d = tempfile.mkdtemp(prefix="fxs_cat_")
    p = os.path.join(d, "bad.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"version": "t", "categories": ["수입금융"],
                        "products": [{"code": "X", "category": "없는분류", "name": "n",
                                      "source": "s"}]}, f, allow_unicode=True)
    with pytest.raises(ValueError):
        Catalog(p)


def test_duplicate_code_is_rejected():
    d = tempfile.mkdtemp(prefix="fxs_cat2_")
    p = os.path.join(d, "dup.yaml")
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump({"version": "t", "categories": ["수입금융"],
                        "products": [{"code": "X", "category": "수입금융", "name": "a", "source": "s"},
                                     {"code": "X", "category": "수입금융", "name": "b", "source": "s"}]},
                       f, allow_unicode=True)
    with pytest.raises(ValueError):
        Catalog(p)


# ── 데모 동기화 ─────────────────────────────────────────────────────
def test_catalog_matches_demo_html():
    """YAML(마스터) 과 데모 HTML 의 KB_PRODUCTS 가 갈라지지 않았는가.

    갈라지면 상품 담당자가 YAML 을 고쳐도 화면은 옛 문안을 계속 보여준다.
    """
    script = os.path.join(ROOT, "scripts", "sync_demo_products.py")
    r = subprocess.run([sys.executable, script, "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=120)
    assert r.returncode == 0, (
        "상품 마스터와 데모 HTML 이 어긋납니다:\n" + (r.stdout or "") + (r.stderr or ""))


# ── 범위·기간형 선물환 라우팅 (P1-4) ──────────────────────────────────
def test_range_and_window_forward_route_by_scenario():
    """기간형/범위형 선물환이 결제일·포지션·여신에 따라 옳게 갈리는가."""
    keys = get_catalog().eligibility_keys()
    assert keys.get("FX-FLEX-FORWARD") == "기간형선물환"
    assert keys.get("FX-RANGE-FORWARD") == "범위선물환"
    # 기간형(윈도우): 확정·여신 + 결제일이 '기간(범위)'일 때만. 결제일 확정이면 고정 선물환이 저렴.
    assert eligible("기간형선물환", Facts(cert="confirmed", credit="yes", settle="window")) is True
    assert eligible("기간형선물환", Facts(cert="confirmed", credit="yes", settle="fixed")) is False
    # 범위형: 확정 수출은 무여신도 가능(K-SURE 범위형), 확정 수입 무여신은 불가.
    assert eligible("범위선물환", Facts(pos="export", cert="confirmed", credit="no")) is True
    assert eligible("범위선물환", Facts(pos="import", cert="confirmed", credit="no")) is False
    # 가결제(실수요 미확정)면 둘 다 불가 — 윈도우 포워드는 실수요 원칙을 우회하지 않는다.
    prov = Facts(pos="export", cert="provisional", credit="yes", settle="window")
    assert eligible("기간형선물환", prov) is False
    assert eligible("범위선물환", prov) is False


# ── 자격 위임 ───────────────────────────────────────────────────────
def test_catalog_does_not_reimplement_eligibility():
    """카탈로그는 자격 '키'만 들고, 조건은 elig.py 가 판정한다."""
    import io
    raw = io.open(os.path.join(ROOT, "catalog", "products.yaml"), encoding="utf-8").read()
    # 조건식이 YAML 로 새어나오면 규칙이 두 곳이 된다
    for leak in ("credit ==", "pos ==", "cert ==", "if ", "ok:"):
        assert leak not in raw, f"자격 조건이 카탈로그로 새어나왔습니다: {leak!r}"
