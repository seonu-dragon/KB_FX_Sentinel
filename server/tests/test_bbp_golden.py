"""BBP 이중구현 대조 — 데모 JS vs Python 엔진 (골든벡터).

■ 무엇을 같다고 봐야 하나 (중요)
BBP 와 ES 는 성격이 다르다. 같은 잣대로 재면 안 된다.

  BBP  결정론적 닫힌형(Student-t CDF). **거의 정확히** 같아야 한다.
       차이가 난다면 그건 표본오차가 아니라 수식이 갈라진 것이다.

  ES   몬테카를로. 데모 JS 는 12,000 표본 + 자체 PRNG(mulberry32),
       Python 은 40,000 표본 + numpy default_rng(7). **애초에 같을 수 없다.**
       둘 다 같은 적분의 추정치일 뿐이므로, 표본오차 범위 안에 있는지만 본다.
       여기서 엄격한 동일성을 요구하면 테스트가 거짓말을 하게 된다.

각각 자기 성질에 맞는 허용오차를 쓴다.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))
for p in (ROOT, KB):
    if p not in sys.path:
        sys.path.insert(0, p)

from fx_sentinel.bbp import budget_breach_probability, expected_shortfall  # noqa: E402

SRC = os.path.join(KB, "FX_Sentinel_demo_ui.html")
MARK = "@@GOLD@@"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# 고정 벡터 — 국면(평상/고변동), 방향(수출/수입), 만기, 개입플래그를 고루 덮는다.
VECTORS = [
    {"spot": 1528.8, "K": 1450, "sig": 0.098,  "hbd": 63,  "pos": "export", "iz": 0},
    {"spot": 1528.8, "K": 1500, "sig": 0.098,  "hbd": 63,  "pos": "import", "iz": 0},
    {"spot": 1528.8, "K": 1450, "sig": 0.098,  "hbd": 21,  "pos": "export", "iz": 0},
    {"spot": 1528.8, "K": 1450, "sig": 0.098,  "hbd": 126, "pos": "export", "iz": 0},
    {"spot": 1528.8, "K": 1450, "sig": 0.1598, "hbd": 63,  "pos": "export", "iz": 0},
    {"spot": 1528.8, "K": 1450, "sig": 0.0861, "hbd": 63,  "pos": "export", "iz": 0},
    {"spot": 1528.8, "K": 1450, "sig": 0.098,  "hbd": 63,  "pos": "export", "iz": 1},
    {"spot": 1528.8, "K": 1600, "sig": 0.098,  "hbd": 63,  "pos": "import", "iz": 1},
    {"spot": 1300.0, "K": 1300, "sig": 0.12,   "hbd": 42,  "pos": "export", "iz": 0},
    {"spot": 1450.0, "K": 1520, "sig": 0.07,   "hbd": 252, "pos": "import", "iz": 0},
]

BBP_TOL = 1e-6      # 닫힌형 — 수식이 같으면 이 정도로 붙는다

# ES 허용오차는 **고정 퍼센트로 두면 안 된다.**
# 이탈이 희귀할수록(만기 짧고 K 가 멀수록) 꼬리에 떨어지는 표본이 적어 추정 분산이 커진다.
# 실측: hbd=63 에서 변동계수 3.9%, hbd=21(BBP 6%)에서는 7.3% 까지 벌어진다.
# 그래서 고정 4% 를 쓰면 희귀꼬리에서 거짓 실패하고, 12% 로 늘리면 나머지 벡터의 민감도가 죽는다.
# → 벡터마다 **추정량의 실측 표준편차를 구해** 그 배수로 판정한다.
JS_SAMPLES = 12_000   # 데모 JS 의 표본수 (expShortfall)
ES_SIGMA_K = 4.0      # 4σ — 정상 노이즈로 거짓 실패할 확률은 무시할 수준
ES_SEEDS = 12         # sd 추정용 시드 수


def _chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _run_js() -> list[dict]:
    chrome = _chrome()
    src = io.open(SRC, encoding="utf-8").read()
    probe = (
        "<script>(function(){var V=" + json.dumps(VECTORS) + ";var out=[];"
        "for(var i=0;i<V.length;i++){var v=V[i];"
        "out.push({bbp:bbpProb(v.spot,v.K,v.sig,v.hbd,v.pos,v.iz),"
        "es:expShortfall(v.spot,v.K,v.sig,v.hbd,v.pos)});}"
        "var d=document.createElement('div');d.id='__gold';"
        "d.textContent='" + MARK + "'+JSON.stringify(out)+'" + MARK + "';"
        "document.body.appendChild(d);})();</script>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gold.html")
        io.open(path, "w", encoding="utf-8").write(src.replace("</body>", probe + "\n</body>"))
        proc = subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=8000", "--dump-dom",
             "file:///" + path.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", timeout=180)
        dom = proc.stdout or ""
    dom = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)   # 주입 원문 제외
    found = re.findall(re.escape(MARK) + "(.*?)" + re.escape(MARK), dom, re.S)
    if not found:
        pytest.fail("JS 프로브 미실행 — bbpProb/expShortfall 을 찾을 수 없음")
    import html as _html
    return json.loads(_html.unescape(found[0]))


@pytest.mark.skipif(_chrome() is None, reason="Chrome 미설치 — JS 대조 불가")
def test_bbp_matches_demo_js():
    """닫힌형이므로 사실상 정확히 같아야 한다."""
    js = _run_js()
    bad = []
    for v, j in zip(VECTORS, js):
        py = budget_breach_probability(v["spot"], v["K"], v["sig"], v["hbd"],
                                       v["pos"], 5.0, v["iz"])
        if abs(py - j["bbp"]) > BBP_TOL:
            bad.append(f"{v} · py={py:.10f} js={j['bbp']:.10f} 차이={abs(py-j['bbp']):.2e}")
    assert not bad, "BBP 가 갈라졌습니다 (수식 drift):\n" + "\n".join(bad)


def _es_noise_sd(v: dict) -> float:
    """JS 와 같은 표본수에서 ES 추정량이 시드에 따라 얼마나 흔들리는지 실측."""
    import statistics
    xs = [expected_shortfall(v["spot"], v["K"], v["sig"], v["hbd"], v["pos"], 5.0,
                             n=JS_SAMPLES, seed=s) for s in range(ES_SEEDS)]
    return statistics.pstdev(xs)


@pytest.mark.skipif(_chrome() is None, reason="Chrome 미설치 — JS 대조 불가")
def test_es_agrees_within_monte_carlo_error():
    """표본수·PRNG 가 다르므로 '같음'이 아니라 '노이즈 범위 이내'를 본다.

    허용폭을 임의로 정하지 않고, 같은 표본수에서 추정량이 실제로 얼마나 흔들리는지
    측정해 그 4σ 를 쓴다. 수식이 갈라지면 차이가 σ 단위로 훨씬 크게 벌어진다.
    """
    js = _run_js()
    bad = []
    for v, j in zip(VECTORS, js):
        py = expected_shortfall(v["spot"], v["K"], v["sig"], v["hbd"], v["pos"], 5.0)
        sd = _es_noise_sd(v)
        allowed = ES_SIGMA_K * sd + 1e-9
        diff = abs(py - j["es"])
        if diff > allowed:
            sigmas = diff / sd if sd > 0 else float("inf")
            bad.append(f"{v}\n    py={py:.4f} js={j['es']:.4f} 차이={diff:.4f} "
                       f"= {sigmas:.1f}σ (허용 {ES_SIGMA_K}σ={allowed:.4f})")
    assert not bad, (
        "ES 가 몬테카를로 노이즈 범위를 벗어났습니다 — 수식 또는 스케일 drift 의심:\n"
        + "\n".join(bad))


def _es_quadrature(spot: float, K: float, sig: float, hbd: int, pos: str,
                   nu: float = 5.0, tmax: float = 60.0) -> float:
    """ES 의 결정론적 기준값 — 수치적분.

    JS 대조만으로는 수식 drift 를 잡을 수 없다. JS 쪽 기준값이 12,000 표본이라
    자체 노이즈가 커서, 5% 스케일 오차조차 1σ 안에 묻힌다(실측 확인).
    그래서 Python 수식은 **몬테카를로가 아닌 적분**으로 따로 검증한다.

    수입 방향의 절단(tmax)에 대하여:
      S_T = spot·exp(scale·t), t ~ Student-t 이므로 E[S_T] 는 **수학적으로 발산**한다
      (다항꼬리 × 지수증가). 다만 발산이 지배하려면 t ≈ 6/scale ≈ 150 이 필요하고
      그건 P ≈ 1e-12 라, 어떤 실무 표본수에서도 나타나지 않는다.
      실제로 tmax 를 30→240 으로 바꿔도 값이 45.5488→45.5511 로 사실상 불변이다.
      즉 데모가 쓰는 유한표본 ES 는 이 절단적분과 같은 것을 재고 있다.
    """
    import numpy as np
    from scipy import integrate, stats

    tau = hbd / 252.0
    sd = sig * np.sqrt(tau)
    scale = sd / np.sqrt(nu / (nu - 2))
    z = np.log(K / spot) / scale
    pdf = lambda t: stats.t.pdf(t, nu)          # noqa: E731

    if pos == "export":
        g = lambda t: (K - spot * np.exp(scale * t)) * pdf(t)      # noqa: E731
        val, _ = integrate.quad(g, -np.inf, z, limit=400)
    else:
        g = lambda t: (spot * np.exp(scale * t) - K) * pdf(t)      # noqa: E731
        val, _ = integrate.quad(g, z, tmax, limit=400)
    return float(val)


def test_python_es_matches_quadrature():
    """Python ES 수식 검증 — 결정론적 기준 대비.

    시드 평균으로 MC 노이즈를 √12 만큼 줄여, 작은 스케일 오차도 σ 단위로 드러나게 한다.
    (이 테스트가 JS 대조보다 훨씬 민감하다 — 수식이 틀리면 여기서 죽는다.)
    """
    import statistics

    bad = []
    for v in VECTORS:
        xs = [expected_shortfall(v["spot"], v["K"], v["sig"], v["hbd"], v["pos"],
                                 5.0, n=40_000, seed=s) for s in range(12)]
        mc_mean = statistics.mean(xs)
        se = statistics.pstdev(xs) / (len(xs) ** 0.5)      # 평균의 표준오차
        ref = _es_quadrature(v["spot"], v["K"], v["sig"], v["hbd"], v["pos"])
        diff = abs(mc_mean - ref)
        allowed = 4.0 * se + 1e-9
        if diff > allowed:
            bad.append(f"{v}\n    quad={ref:.5f} mc={mc_mean:.5f} "
                       f"차이={diff:.5f} = {diff/se:.1f}σ (허용 4σ={allowed:.5f})")
    assert not bad, "Python ES 가 적분 기준과 어긋납니다 (수식 오류):\n" + "\n".join(bad)


def test_bbp_direction_is_not_symmetric():
    """수출/수입은 반대 방향 리스크다. 같은 입력에서 두 값이 같으면 방향 로직이 죽은 것."""
    a = budget_breach_probability(1528.8, 1450, 0.098, 63, "export", 5.0, 0)
    b = budget_breach_probability(1528.8, 1450, 0.098, 63, "import", 5.0, 0)
    assert abs((a + b) - 1.0) < 1e-9      # 개입 없으면 정확히 여사건
    assert abs(a - b) > 0.01


def test_intervention_reduces_probability():
    base = budget_breach_probability(1528.8, 1450, 0.098, 63, "export", 5.0, 0)
    iz = budget_breach_probability(1528.8, 1450, 0.098, 63, "export", 5.0, 1)
    assert iz == pytest.approx(base * 0.7, rel=1e-12)
