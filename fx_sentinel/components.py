"""
FX Sentinel — Phase 1: FX-EWI 구성요소 (V · J · C · M · 개입플래그)
====================================================================
설계 근거: FX_Sentinel_A_FX-EWI_수식설계.md §3

 V  변동성 국면      — Yang-Zhang 단/중기 실현변동성 비율 → 로지스틱 (§3.1)
 J  점프 리스크        — 표준화수익률 임계초과의 지수감쇠 밀도, J_max 유계화 (§3.2)
 C  변동성 군집 강도    — GARCH(1,1) 조건부분산의 가속도(레벨-직교), V에 잔차화 (§3.3)
 M  거시 이벤트 근접도  — 캘린더 근접 커널 (§3.4). 캘린더 미연결 시 0 (MVP 스텁)
 IZ 정책개입 국면 플래그 — 라운드넘버 근접 (§3.6, KRW 특화). 지수를 낮추지 않고 표시·방향층용

주의(누수): C의 GARCH는 MVP에서 전표본 1회 적합(in-sample). Phase 2에서 walk-forward로 교체.
모든 성분은 0~1 스케일. 로지스틱 Φ_L(x)=1/(1+e^-x).
"""
from __future__ import annotations
import numpy as np
import pandas as pd

LOGISTIC = lambda x: 1.0 / (1.0 + np.exp(-x))


def _roll_z(s: pd.Series, win: int = 252) -> pd.Series:
    """롤링 z-score (평균/표준편차는 과거 창에서만)."""
    mu = s.rolling(win, min_periods=win // 2).mean()
    sd = s.rolling(win, min_periods=win // 2).std(ddof=1)
    return (s - mu) / sd.replace(0, np.nan)


# ---------------------------------------------------------------- V
def yang_zhang_vol(df: pd.DataFrame, window: int) -> pd.Series:
    """Yang-Zhang 일별 변동성 추정 (시가갭+장중+종가 결합, 드리프트 독립). A §3.1"""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    c_prev = c.shift(1)
    log_oc_prev = np.log(o / c_prev)      # overnight (전일종가→당일시가 갭)
    log_co = np.log(c / o)                # open→close
    log_ho, log_hc = np.log(h / o), np.log(h / c)
    log_lo, log_lc = np.log(l / o), np.log(l / c)
    rs = log_hc * log_ho + log_lc * log_lo   # Rogers-Satchell (per-day)

    n = window
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    var_o = log_oc_prev.rolling(n, min_periods=n).var(ddof=1)
    var_c = log_co.rolling(n, min_periods=n).var(ddof=1)
    var_rs = rs.rolling(n, min_periods=n).mean()
    yz_var = var_o + k * var_c + (1 - k) * var_rs
    return np.sqrt(yz_var.clip(lower=0))


def component_V(df: pd.DataFrame, short: int = 5, med: int = 20, zwin: int = 252) -> pd.Series:
    """단기 대 중기 실현변동성 팽창 국면. >0.5면 변동성 확장."""
    rv_short = yang_zhang_vol(df, short)
    rv_med = yang_zhang_vol(df, med)
    ratio = rv_short / rv_med.replace(0, np.nan)
    return LOGISTIC(_roll_z(ratio, zwin)).rename("V")


# ---------------------------------------------------------------- J
def ewma_vol(r: pd.Series, lam: float = 0.94) -> pd.Series:
    """RiskMetrics EWMA 변동성 (재귀). σ_t."""
    r2 = r.values ** 2
    s2 = np.empty(len(r2))
    s2[0] = np.nanvar(r.values[:20]) if len(r2) > 20 else r2[0]
    for t in range(1, len(r2)):
        s2[t] = lam * s2[t - 1] + (1 - lam) * r2[t - 1]  # t-1까지 정보 (무누수)
    return pd.Series(np.sqrt(s2), index=r.index, name="ewma_sigma")


def component_J(r: pd.Series, k: float = 3.0, m: int = 20, tau: float = 5.0) -> pd.Series:
    """최근 점프(꼬리사건) 집중도. J_max 유계화(§3.2 — z-score 금지)."""
    sigma = ewma_vol(r)
    u = r / sigma.replace(0, np.nan)       # 표준화수익률 (직전 변동성 정규화)
    jump = (u.abs() > k).astype(float)
    w = np.exp(-np.arange(1, m + 1) / tau)  # 지수감쇠 가중 (i=1 최근)
    j_max = w.sum()
    def wsum(x):
        return np.dot(x[::-1], w[:len(x)])  # 최근일이 w[0]
    j_raw = jump.rolling(m, min_periods=1).apply(lambda x: wsum(x.values), raw=False)
    return (j_raw / j_max).rename("J")


# ---------------------------------------------------------------- C
def garch_cond_var(r: pd.Series, scale: float = 100.0) -> tuple[pd.Series, dict]:
    """GARCH(1,1) 조건부분산 σ²_g,t 시계열 + 모수. arch 사용.
    정상성 제약(α+β≤0.999)·수렴실패 시 EWMA 폴백. A §3.3
    반환 cond_var는 원 스케일(수익률²) 기준.
    """
    from arch import arch_model
    x = (r * scale).dropna()
    try:
        am = arch_model(x, mean="Zero", vol="GARCH", p=1, q=1, dist="normal")
        res = am.fit(disp="off", show_warning=False)
        w, a, b = res.params["omega"], res.params["alpha[1]"], res.params["beta[1]"]
        persistence = a + b
        if not np.isfinite(persistence) or persistence >= 0.999:
            raise ValueError(f"정상성 위반 persistence={persistence}")
        cv = res.conditional_volatility ** 2 / (scale ** 2)  # 원 스케일 분산
        cvar = pd.Series(cv.values, index=x.index).reindex(r.index)
        return cvar, {"omega": float(w), "alpha": float(a), "beta": float(b),
                      "persistence": float(persistence), "fallback": False}
    except Exception as e:
        print(f"[C] GARCH 폴백(EWMA): {e}")
        cvar = ewma_vol(r) ** 2
        return cvar, {"persistence": np.nan, "fallback": True}


def component_C(r: pd.Series, V: pd.Series, m: int = 20, pctwin: int = 252):
    """분산 가속도(레벨-직교) → 백분위 → V에 잔차화. A §3.3 재정의."""
    cvar, gp = garch_cond_var(r)
    c_raw = cvar / cvar.shift(m)           # m일 전 대비 조건부분산 변화율(군집 축적)
    c_pct = c_raw.rolling(pctwin, min_periods=pctwin // 2).apply(
        lambda x: (x.values[-1] > x.values[:-1]).mean(), raw=False)  # 과거창 백분위
    # V에 잔차화: C의 V-독립 성분만 (corr 리포트용 raw도 반환)
    df = pd.concat([c_pct.rename("C"), V.rename("V")], axis=1).dropna()
    if len(df) > 30:
        beta = np.polyfit(df["V"], df["C"], 1)
        resid = c_pct - (beta[0] * V + beta[1])
        # 잔차를 0~1로: 롤링 백분위
        C = resid.rolling(pctwin, min_periods=pctwin // 2).apply(
            lambda x: (x.values[-1] > x.values[:-1]).mean(), raw=False)
    else:
        C = c_pct
    return C.rename("C"), gp, c_pct.rename("C_raw_pct")


# ---------------------------------------------------------------- M (스텁)
def component_M(index: pd.DatetimeIndex, calendar: dict | None = None,
                tau: float = 3.0) -> pd.Series:
    """거시 이벤트 근접도. calendar={date: weight}. 미연결 시 0 (MVP). A §3.4"""
    M = pd.Series(0.0, index=index, name="M")
    if not calendar:
        return M  # 캘린더 미연결 → 0, 합성에서 가중 재정규화
    ev = pd.Series(calendar)
    ev.index = pd.to_datetime(ev.index)
    for t in index:
        future = ev[ev.index >= t]
        if len(future):
            d = (future.index - t).days
            M.loc[t] = float((future.values * np.exp(-d / tau)).max())
    return M.clip(0, 1)


# ---------------------------------------------------------------- 개입 플래그 (§3.6)
def intervention_flag(df: pd.DataFrame, levels: list[float] | None = None,
                      tau_r: float = 15.0, theta: float = 0.5) -> pd.DataFrame:
    """정책개입 국면 플래그 (KRW 특화). 라운드넘버 근접도 기반.
    지수를 낮추지 않고 '눌린 변동성+높은 돌파위험'을 표시 + 방향층 절단용.
    news/flow 텐션은 MVP 스텁(0). A §3.6
    """
    p = df["Close"]
    if levels is None:
        lo, hi = int(p.min() // 50 * 50), int(p.max() // 50 * 50 + 50)
        levels = list(range(lo, hi + 1, 50))  # 50원 단위 심리적 레벨
    lv = np.array(levels, dtype=float)
    dist = p.apply(lambda x: np.min(np.abs(x - lv)))
    round_prox = np.exp(-dist / tau_r)      # 0~1, 라운드넘버 근접
    interv = round_prox                     # + w_news*뉴스텐션 + w_flow*일방향흐름 (스텁)
    iz = (interv > theta).astype(int)
    return pd.DataFrame({"round_prox": round_prox, "interv": interv, "IZ": iz}, index=df.index)


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    from data_loader import load_usdkrw
    df = load_usdkrw()
    r = df["r"]
    V = component_V(df)
    J = component_J(r)
    C, gp, c_raw = component_C(r, V)
    M = component_M(df.index)
    IZ = intervention_flag(df)
    print("GARCH 모수:", {k: round(v, 4) if isinstance(v, float) else v for k, v in gp.items()})
    comp = pd.concat([V, J, C, M, IZ["IZ"]], axis=1)
    print("\n[성분 통계]\n", comp.describe().round(3))
    print("\n[corr(C,V)] (증분성 확인, 낮을수록 좋음):", round(comp[["C", "V"]].corr().iloc[0, 1], 3))
    print("\n[개입국면(IZ=1) 비율]:", round(IZ["IZ"].mean(), 3),
          "| 1400±근접 예:", df.loc[IZ["IZ"] == 1, "Close"].round(0).value_counts().head(3).to_dict())
