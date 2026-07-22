"""
FX Sentinel — Phase 2: 금리 로더 (선물환 CIP 스왑포인트용)
=========================================================
r_d = 원화 3M (한국 3M 은행간, FRED IR3TIB01KRM156N, 월별→일별 ffill)
r_f = 달러 3M (미국 3M T-bill, FRED DTB3, 일별)
설계 근거: FX_Sentinel_C_검증_경제성설계.md §1.1
"""
from __future__ import annotations
import os, warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data"); os.makedirs(DATA_DIR, exist_ok=True)
CACHE = os.path.join(DATA_DIR, "rates.csv")


def load_rates(index: pd.DatetimeIndex, use_cache: bool = True) -> pd.DataFrame:
    """일별 r_d(KRW), r_f(USD) 연율(소수). index에 정렬(ffill)."""
    if use_cache and os.path.exists(CACHE):
        rt = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        from pandas_datareader import data as web
        krw = web.DataReader("IR3TIB01KRM156N", "fred", "2017-01-01", "2026-07-08")
        usd = web.DataReader("DTB3", "fred", "2017-01-01", "2026-07-08")
        rt = pd.concat([krw.rename(columns={krw.columns[0]: "r_d"}),
                        usd.rename(columns={usd.columns[0]: "r_f"})], axis=1)
        rt = rt.sort_index().ffill()
        rt.to_csv(CACHE)
    rt.index = pd.to_datetime(rt.index)
    out = rt.reindex(index.union(rt.index)).sort_index().ffill().reindex(index)
    return (out / 100.0).rename(columns={"r_d": "r_d", "r_f": "r_f"})  # % → 소수


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    from data_loader import load_usdkrw
    df = load_usdkrw()
    rt = load_rates(df.index, use_cache=False)
    rt["diff(r_d-r_f)"] = rt["r_d"] - rt["r_f"]
    print("[금리 최근]\n", (rt.tail(3) * 100).round(2))
    print("\n[금리차 통계 %]\n", (rt["diff(r_d-r_f)"] * 100).describe().round(2))
    neg = (rt["diff(r_d-r_f)"] < 0).mean()
    print(f"\n[수출기업 선물 디스카운트(비용) 구간 비율] r_d<r_f: {neg:.2%}")
