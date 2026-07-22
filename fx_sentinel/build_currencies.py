"""
FX Sentinel — 다통화 개방: 통화별 σ·금리 실측
==============================================
왜 이 스크립트가 필요한가
------------------------
데모는 오랫동안 USD 결제만 열어두고 EUR/JPY/CNY 를 '준비 중'으로 막아뒀다. 이유는 화면 주석에
그대로 남아 있다 — σ 가 '대표 스냅샷' 상수였고 국면 스트레스는 USD 기준 배수를 곱하는 구조라,
"2022 킹달러 배수를 다른 통화에 곱할 근거가 없다. 통화 수를 늘린 게 아니라 틀린 숫자를 늘린 것."

옳은 판단이었다. 이 스크립트는 그 블로커를 **제거**한다: 통화마다 자기 시계열에서 σ̂·국면·금리를
실측해 `state/currencies.json` 으로 낸다. 배수를 곱하지 않으므로 위 반론이 성립하지 않는다.

방법(USD 와 완전히 동일 — 이게 핵심이다)
--------------------------------------
  σ̂ = ewma_vol(r) × √252   (EWMA λ=0.94 연율화, fx_ewi.py 의 σ̂ 헤드와 같은 식)
  검증: USD 를 이 식으로 재현하면 기준일 σ̂ = 0.098029 → 화면 상수 0.098 과 일치해야 한다.
        일치하지 않으면 나머지 통화도 신뢰할 수 없으므로 **빌드를 중단**한다(self-check).

데이터 소스 (전부 공개·키 불요)
-----------------------------
  USD/KRW · EUR/KRW · JPY/KRW : FinanceDataReader 직접 통화쌍 (전부 OHLC 정상)
  3M 금리 : FRED. USD=DTB3(일별), EUR/JPY=OECD 3개월 은행간(IR3TIB01**M156N, 월별).

CNY 를 왜 열지 않았나 (BLOCKED 참조 — 실제로 시도했다가 접었다)
------------------------------------------------------------
FDR 'KRW/CNY' 역수로 열려고 했다. 역수 자체는 단조감소 변환이라 High↔Low 만 맞바꾸면
정확한 OHLC 이므로 방법은 옳았다. 그런데 산출물이 COVID 국면 σ=8.76(연율 876%)을 뱉었다.
추적해보니 소스의 **호가 관례가 구간마다 100배 바뀐다** — 2018년 전체와 2020년 1~2월이
100KRW 기준으로 찍혀 있다(일수익률이 정확히 ln(100)=±4.61 로 튄다).
무서운 건 이게 **조용히 통과했다**는 점이다: 2018년 *전체*가 같은 오류라 그 구간의 중앙값도
똑같이 틀려서 이상치 필터가 비율 1.0 을 보고 정상으로 판정했다. 통화를 늘리는 일이 왜
"틀린 숫자를 늘리는 일"이 되는지의 교과서적 사례라 기록해 둔다.

정직성 규율
----------
  - USD 스냅샷(spot 1528.8 · sig 0.098 · rf 0.0374)은 **건드리지 않는다**. 문서·테스트·BBP 64.3%
    가 전부 이 값에 걸려 있다. 이 스크립트는 USD 를 재현해 검증만 하고 값은 그대로 둔다.
  - VND 는 열지 않는다. 관리변동환율이라 실현변동성이 시장 리스크를 대표하지 못한다.
  - IZ(정책개입 플래그)는 USD/KRW 라운드넘버(50원 격자) 기반이라 비USD 에 적용하지 않는다.
    JPY/KRW(≈9.4원)에 50원 격자를 씌우면 플래그가 항상 0 이 되고, CNY/KRW(≈225원)는 200/250 이라
    무의미하다 — 통화만 늘리고 의미는 없는 숫자가 된다.
  - 통화별 캘리브레이션(ECE·Brier)을 각각 낸다. EUR 을 열면서 USD 의 ECE 를 들이대면 거짓말이다.

산출: state/currencies.json
"""
from __future__ import annotations
import os, sys, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
STATE = os.path.join(HERE, "state")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(STATE, exist_ok=True)

sys.path.insert(0, HERE)
from components import ewma_vol, component_V, component_J, component_C, component_M, LOGISTIC, _roll_z
from fx_ewi import W_DEFAULT, grade_of
from bbp import budget_breach_probability

START, END = "2018-01-01", "2026-07-08"
BASE_DATE = "2026-07-06"        # 기준일 고정 스냅샷 — 화면 재현성의 기준
N_HORIZON, LOOKBACK, NU = 63, 252, 5.0

# USD 화면 상수 — self-check 기준(이 값과 어긋나면 방법이 틀린 것)
USD_EXPECT = {"spot": 1528.8, "sig": 0.098, "rf": 0.0374}

REGIME_DATES = [
    ("현재",        "2026-07-06", "실측 기준일 · 평시"),
    ("평온",        "2019-07-25", "저변동성 안정 국면"),
    ("COVID 쇼크",  "2020-03-25", "팬데믹 급락 · 점프 동반"),
    ("2022 킹달러", "2022-11-15", "최고 변동성 · 군집 심화"),
    ("점프 급변",   "2021-03-03", "금리 발작 급변 · 점프 집중"),
]

PAIRS = {
    # unit = 호가단위(원/unit통화). JPY 는 관례상 100엔 호가.
    "USD": dict(fdr="USD/KRW", invert=False, unit=1,   rf_series="DTB3",
                rf_daily=True,  label="USD / KRW",    name="미국달러",
                budget=1500, amount=500_000),
    "EUR": dict(fdr="EUR/KRW", invert=False, unit=1,   rf_series="IR3TIB01EZM156N",
                rf_daily=False, label="EUR / KRW",    name="유로",
                budget=1700, amount=400_000),
    "JPY": dict(fdr="JPY/KRW", invert=False, unit=100, rf_series="IR3TIB01JPM156N",
                rf_daily=False, label="JPY100 / KRW", name="엔",
                budget=950,  amount=50_000_000),
}

# 열지 않는 통화 — 이유를 코드에 남긴다(화면에도 같은 문구가 나간다).
BLOCKED = {
    "CNY": "공개 소스(FDR 'KRW/CNY')의 호가 관례가 구간마다 100배 바뀐다 — 2018년 전체와 "
           "2020년 1~2월이 100KRW 기준, 나머지는 1KRW 기준으로 찍혀 일수익률이 ln(100)=4.61 로 튄다. "
           "구간 전체가 같은 오류라 중앙값 필터로도 걸러지지 않는다(조용히 통과하는 오염). "
           "USD 교차(USD/KRW÷USD/CNY)는 Close 만 나와 Yang-Zhang(V 성분)이 불가능. "
           "→ KB 고시환율이 연동되면 즉시 열 수 있다.",
    "VND": "관리변동환율이라 실현변동성이 시장 리스크를 대표하지 못한다. "
           "베트남 거래도 실제 결제통화는 대부분 USD 다.",
}


def _fetch_ohlc(cur: str, spec: dict) -> pd.DataFrame:
    """통화쌍 OHLC. invert=True 면 역수 변환(High↔Low 교환)."""
    cache = os.path.join(DATA_DIR, f"{cur.lower()}krw_ohlc.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
    else:
        import FinanceDataReader as fdr
        raw = fdr.DataReader(spec["fdr"], START, END)
        raw = raw.rename(columns=str.title)[["Open", "High", "Low", "Close"]].copy()
        if spec["invert"]:
            # 1/x 는 단조감소 → 그날의 최고가는 원계열 최저가의 역수다.
            df = pd.DataFrame({
                "Open":  1.0 / raw["Open"],
                "High":  1.0 / raw["Low"],
                "Low":   1.0 / raw["High"],
                "Close": 1.0 / raw["Close"],
            }, index=raw.index)
        else:
            df = raw
        df.to_csv(cache)
    # 정제: 보간 금지, 유효행만(data_loader._clean 과 같은 규율)
    df = df[(df > 0).all(axis=1)].dropna()
    df = df[df["High"] >= df["Low"]]
    df["r"] = np.log(df["Close"]).diff()
    df = df.dropna(subset=["r"])
    _assert_clean(df, cur)
    return df


def _assert_clean(df: pd.DataFrame, cur: str) -> None:
    """데이터 위생 게이트 — 더러우면 **버리지 말고 세운다**.

    처음엔 이상치를 조용히 드롭했는데, 그게 정확히 CNY 사고를 가린 원인이었다:
    5행을 버리고 "정제 완료"라고 넘어갔지만 진짜 오염은 2018년 통째(300행)였고,
    구간 전체가 같은 오류라 중앙값 대비 비율이 1.0 이라 필터를 통과했다.
    드롭은 "몇 건 튀었네"를 "처리했음"으로 바꿔 사람이 원인을 안 보게 만든다.
    그래서 여기서는 조용히 고치지 않고 예외를 던진다 — 통화를 열지 말지는 사람이 정한다.

    판정 ①: 일수익률 |r| > 25%. FX 일간 변동이 이 범위를 넘을 수 없다(스케일 오류의 지문).
    판정 ②: Close 가 전체 중앙값의 5배↑ 또는 1/5↓ — 구간 통째 오염을 잡기 위해
            **국소(rolling) 가 아니라 전역(global) 중앙값**과 비교한다. CNY 를 놓친 게
            국소 중앙값이었다.
    """
    r = df["r"]
    ext = r.abs() > 0.25
    med = float(df["Close"].median())
    scale = (df["Close"] / med > 5) | (df["Close"] / med < 0.2)
    if ext.any() or scale.any():
        d1 = [str(d.date()) for d in df.index[ext]][:5]
        d2 = [str(d.date()) for d in df.index[scale]][:5]
        raise ValueError(
            f"[{cur}] 데이터 위생 실패 — 통화를 열지 않는다.\n"
            f"   |일수익률|>25%: {int(ext.sum())}건 {d1}\n"
            f"   전역중앙값 대비 5배 이탈: {int(scale.sum())}건 {d2}\n"
            f"   → 소스 호가 관례가 구간마다 바뀌는지 확인할 것(BLOCKED 주석 참조)."
        )


def _sigma_hat(df: pd.DataFrame) -> pd.Series:
    """fx_ewi.py 의 σ̂ 헤드와 동일: 연율화 EWMA."""
    return (ewma_vol(df["r"]) * np.sqrt(252)).rename("sigma_hat")


def _ewi_frame(df: pd.DataFrame) -> pd.DataFrame:
    """FX-EWI 확률헤드 + 성분 기여분해. compute_fx_ewi 와 같은 식이나
    기여분해(shares)를 함께 내기 위해 여기서 성분을 직접 잡는다."""
    r = df["r"]
    V = component_V(df); J = component_J(r)
    C, garch, _ = component_C(r, V)
    M = component_M(df.index)
    comp = pd.concat([V, J, C, M], axis=1)
    w = dict(W_DEFAULT)
    if M.abs().sum() == 0:
        w.pop("M", None)
    wsum = sum(w.values()); w = {k: v / wsum for k, v in w.items()}
    contrib = pd.DataFrame({k: w[k] * comp[k] for k in w})
    score = contrib.sum(axis=1)
    ewi = 100.0 * LOGISTIC(_roll_z(score, 252))
    out = contrib.add_prefix("c_")
    out["score"] = score; out["FX_EWI"] = ewi
    out.attrs["garch"] = garch
    return out


def _shares_at(ewi_df: pd.DataFrame, date: str) -> dict:
    """그날 FX-EWI 를 무엇이 밀어올렸는가 — 성분 기여 비중(%)."""
    row = ewi_df.loc[date]
    cols = [c for c in ewi_df.columns if c.startswith("c_")]
    vals = {c[2:]: float(row[c]) for c in cols}
    tot = sum(abs(v) for v in vals.values())
    if tot <= 0:
        return {k: 0 for k in vals}
    return {k: int(round(abs(v) / tot * 100)) for k, v in vals.items()}


def _calibrate(close: np.ndarray, sig: np.ndarray) -> dict:
    """calibrate_bbp.py 와 동일한 무누수 절차 — 통화별로 따로 잰다."""
    preds, ys = [], []
    n = len(close)
    for i in range(LOOKBACK, n - N_HORIZON):
        K = float(np.mean(close[i - LOOKBACK:i]))
        spot = float(close[i]); s = float(sig[i]); settle = float(close[i + N_HORIZON])
        if not (np.isfinite(K) and np.isfinite(s) and s > 0):
            continue
        preds.append(budget_breach_probability(spot, K, s, N_HORIZON, "export", NU))
        ys.append(1 if settle < K else 0)
        preds.append(budget_breach_probability(spot, K, s, N_HORIZON, "import", NU))
        ys.append(1 if settle > K else 0)
    p = np.array(preds); y = np.array(ys, dtype=float)
    brier = float(np.mean((p - y) ** 2))
    edges = np.linspace(0, 1, 11); ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        c = int(m.sum())
        if c == 0:
            continue
        ece += (c / len(p)) * abs(float(p[m].mean()) - float(y[m].mean()))
    return {"n_obs": len(p), "brier": round(brier, 4), "ece": round(ece, 4),
            "base_rate": round(float(y.mean()), 4)}


def _rates(spec: dict) -> float:
    """기준일 3M 금리(연율 소수). 월별 시리즈는 ffill — rates.py 와 같은 처리."""
    import pandas_datareader.data as web
    s = web.DataReader(spec["rf_series"], "fred", "2017-01-01", END).dropna()
    s = s[s.index <= pd.Timestamp(BASE_DATE)]
    return float(s.iloc[-1, 0]) / 100.0, s.index[-1].date().isoformat()


def _asof(idx: pd.DatetimeIndex, date: str) -> pd.Timestamp:
    """해당일 없으면 직전 영업일(과거 방향)."""
    c = idx[idx <= pd.Timestamp(date)]
    if not len(c):
        raise KeyError(date)
    return c[-1]


def build() -> dict:
    out = {"base_date": BASE_DATE, "method": "sigma_hat = ewma_vol(r)*sqrt(252) · fx_ewi.py σ̂ 헤드와 동일",
           "blocked": BLOCKED, "currencies": {}}
    for cur, spec in PAIRS.items():
        df = _fetch_ohlc(cur, spec)
        sh = _sigma_hat(df)
        ewi_df = _ewi_frame(df)
        bd = _asof(df.index, BASE_DATE)

        spot_unit = float(df.loc[bd, "Close"]) * spec["unit"]
        sig_base = float(sh.loc[bd])
        rf, rf_asof = _rates(spec)

        regimes = []
        for key, date, note in REGIME_DATES:
            try:
                d = _asof(df.index, date)
            except KeyError:
                continue
            regimes.append({
                "key": key, "date": str(d.date()),
                "sigAnn": round(float(sh.loc[d]), 4),
                "ewi": int(round(float(ewi_df.loc[d, "FX_EWI"]))),
                "iz": 0,   # 개입플래그는 USD/KRW 전용 — 비USD 미적용(모듈 docstring)
                "shares": _shares_at(ewi_df, str(d.date())),
                "note": note,
            })

        d2 = df.join(sh).dropna(subset=["sigma_hat"])
        calib = _calibrate(d2["Close"].values, d2["sigma_hat"].values)

        out["currencies"][cur] = {
            "label": spec["label"], "name": spec["name"], "unit": spec["unit"],
            "spot": round(spot_unit, 4), "sig": round(sig_base, 4), "rf": round(rf, 4),
            "rf_series": spec["rf_series"], "rf_asof": rf_asof,
            "budget": spec["budget"], "amount": spec["amount"],
            "rows": int(len(df)), "span": [str(df.index[0].date()), str(df.index[-1].date())],
            "calib": calib, "regimes": regimes,
            "garch_persistence": round(float(ewi_df.attrs["garch"].get("persistence", float("nan"))), 4),
        }
    return out


def _selfcheck(o: dict) -> list[str]:
    """USD 가 화면 상수를 재현하지 못하면 나머지 통화도 못 믿는다."""
    u = o["currencies"]["USD"]; errs = []
    if abs(u["spot"] - USD_EXPECT["spot"]) > 0.5:
        errs.append(f"USD spot {u['spot']} ≠ 화면 {USD_EXPECT['spot']}")
    if abs(u["sig"] - USD_EXPECT["sig"]) > 0.0005:
        errs.append(f"USD sig {u['sig']} ≠ 화면 {USD_EXPECT['sig']}")
    if abs(u["rf"] - USD_EXPECT["rf"]) > 0.0005:
        errs.append(f"USD rf {u['rf']} ≠ 화면 {USD_EXPECT['rf']}")
    return errs


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    o = build()
    errs = _selfcheck(o)
    print(f"기준일 {o['base_date']} · 방법: {o['method']}\n")
    print(f"{'통화':<5}{'라벨':<15}{'spot':>10}{'σ̂':>8}{'rf':>8}{'행':>7}{'ECE':>8}{'Brier':>8}{'관측':>9}")
    print("─" * 80)
    for c, v in o["currencies"].items():
        print(f"{c:<5}{v['label']:<15}{v['spot']:>10.2f}{v['sig']:>8.4f}{v['rf']:>8.4f}"
              f"{v['rows']:>7}{v['calib']['ece']:>8.4f}{v['calib']['brier']:>8.4f}{v['calib']['n_obs']:>9,}")
    print("─" * 80)
    if errs:
        print("\n✗ USD self-check 실패 — 방법이 어긋났다. currencies.json 을 쓰지 않는다:")
        for e in errs: print("   -", e)
        sys.exit(1)
    print("\n✓ USD self-check 통과 — 화면 상수(spot·sig·rf)를 그대로 재현. 같은 식으로 EUR/JPY 산출.")
    print("✓ 위생 게이트 통과 — 세 통화 모두 |r|>25%·스케일 이탈 0건.")
    for c, why in BLOCKED.items():
        print(f"✗ {c} 미개방 — {why.splitlines()[0]}")
    with open(os.path.join(STATE, "currencies.json"), "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)
    print(f"→ state/currencies.json 저장")
