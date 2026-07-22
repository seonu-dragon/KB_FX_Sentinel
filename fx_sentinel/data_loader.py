"""
FX Sentinel — Phase 0: 데이터 로더
====================================
원/달러(USD/KRW) 일봉 OHLC 히스토리를 수집·정제하고 로그수익률을 계산한다.

설계 근거: FX_Sentinel_A_FX-EWI_수식설계.md §2, §8
 - 룩어헤드 금지: 모든 입력은 t 종가까지.
 - 결측·휴장: 유효하지 않은 행(0/NaN)은 제거, 보간 금지(누수 위험) → 직전 관측 유지.
 - Yang-Zhang 변동성(A §3.1)을 위해 OHLC 4개 컬럼을 모두 확보한다.

소스: FinanceDataReader 'USD/KRW' (공개, 키 불요). 폴백: yfinance 'KRW=X'.
"""
from __future__ import annotations
import os
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CACHE = os.path.join(DATA_DIR, "usdkrw_ohlc.csv")

START = "2018-01-01"
END = "2026-07-08"


def _fetch_fdr(start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    df = fdr.DataReader("USD/KRW", start, end)
    df = df.rename(columns=str.title)  # Open/High/Low/Close
    return df[["Open", "High", "Low", "Close"]].copy()


def _fetch_yahoo(start: str, end: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download("KRW=X", start=start, end=end, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].copy()


def load_usdkrw(start: str = START, end: str = END, use_cache: bool = True,
                source: str = "fdr") -> pd.DataFrame:
    """USD/KRW 일봉 OHLC + 로그수익률.

    Returns DataFrame(index=Date) with columns: Open, High, Low, Close, r
      r_t = ln(Close_t / Close_{t-1})
    """
    if use_cache and os.path.exists(CACHE):
        df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        try:
            df = _fetch_fdr(start, end) if source == "fdr" else _fetch_yahoo(start, end)
        except Exception as e:  # pragma: no cover
            print(f"[data_loader] {source} 실패({e!r}) → yahoo 폴백")
            df = _fetch_yahoo(start, end)
        df.to_csv(CACHE)

    df = _clean(df)
    df["r"] = np.log(df["Close"]).diff()
    df = df.dropna(subset=["r"])
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """유효하지 않은 행 제거 (보간 금지)."""
    df = df.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # OHLC 중 하나라도 <=0 또는 NaN이면 그 행 제거 (휴장/데이터 결측)
    ohlc = ["Open", "High", "Low", "Close"]
    valid = (df[ohlc] > 0).all(axis=1) & df[ohlc].notna().all(axis=1)
    dropped = (~valid).sum()
    if dropped:
        print(f"[data_loader] 유효하지 않은 행 {dropped}개 제거 (0/NaN)")
    df = df[valid]
    # High/Low 정합성 보정: High>=max(O,C), Low<=min(O,C)
    df["High"] = df[["High", "Open", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)
    return df[ohlc]


if __name__ == "__main__":
    df = load_usdkrw(use_cache=False)
    print(f"\n[shape] {df.shape}  범위 {df.index.min().date()} ~ {df.index.max().date()}")
    print(f"[결측 없음] r NaN={df['r'].isna().sum()}")
    print("\n[최근 5일]")
    print(df.tail())
    print("\n[로그수익률 통계]")
    print(df["r"].describe())
    # 2022 킹달러 구간 최고 종가
    k2022 = df.loc["2022-01-01":"2022-12-31", "Close"]
    print(f"\n[2022 킹달러] 최고 종가 {k2022.max():.1f}원 ({k2022.idxmax().date()})")
