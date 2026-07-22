"""
FX Sentinel — 거래국 매크로 리스크 (설계 A §7)
==============================================
환율 변동성 리스크(FX-EWI)와 **분리된** 국가 구조 리스크 점수.
성장률·물가·정책금리·경상수지·국가신용등급·정치안정 지표를 투명 가중으로 0~100 점수화
(높을수록 위험). FX-EWI(통화 변동성 축)와 대시보드에서 '결합 표시'한다(별도 산출 유지).

대표 스냅샷 파라미터 — 실데이터(World Bank/IMF/S&P) 연동 시 동일 코드로 확장.
"""
from __future__ import annotations
from dataclasses import dataclass

# S&P 등급 → 리스크 점수(0~100, 높을수록 위험)
_RATING = {"AAA": 5, "AA+": 12, "AA": 18, "AA-": 24, "A+": 30, "A": 38, "A-": 44,
           "BBB+": 50, "BBB": 56, "BBB-": 62, "BB+": 68, "BB": 75, "BB-": 80, "B+": 85, "B": 90}

# 지표 → 서브 리스크 점수(0~100). 각 매핑은 투명·단조.
def _s_rating(r: str) -> float: return float(_RATING.get(r, 60))
def _s_curracct(ca: float) -> float:  return max(0.0, min(100.0, 50 - ca * 6))      # 흑자↓위험·적자↑위험
def _s_infl(p: float) -> float:       return max(0.0, min(100.0, 20 + abs(p - 2.5) * 18))  # 목표 2.5% 이탈(양방향)
def _s_polit(stab: float) -> float:   return max(0.0, min(100.0, 100 - stab))       # 정치안정지수(高=안정)→위험
def _s_growth(g: float) -> float:     return max(0.0, min(100.0, 80 - g * 10))      # 저성장↑위험(완만)

_W = {"신용등급": 0.35, "경상수지": 0.20, "정치안정": 0.20, "물가": 0.15, "성장": 0.10}


@dataclass
class Country:
    name: str; rating: str; growth: float; inflation: float
    policy_rate: float; current_acct: float; political: float
    demand_yoy: str; gdp_yoy: str; fx: str; source: str


COUNTRIES: dict[str, Country] = {
    "미국":   Country("미국",   "AA+", 2.0, 2.8, 4.00, -3.0, 82, "+3.1%",  "+2.0%", "DXY 103.2",      "S&P·IMF·World Bank(대표 스냅샷)"),
    "베트남": Country("베트남", "BB+", 5.7, 3.5, 4.50,  2.0, 55, "+14.2%", "+5.7%", "USD/VND 24,650", "S&P·IMF·World Bank(대표 스냅샷)"),
    "중국":   Country("중국",   "A+",  4.6, 0.8, 3.10,  1.5, 60, "-1.2%",  "+4.6%", "USD/CNY 7.24",   "S&P·IMF·World Bank(대표 스냅샷)"),
    "일본":   Country("일본",   "A+",  0.9, 2.2, 0.50,  3.5, 85, "+1.8%",  "+0.9%", "USD/JPY 152.6",  "S&P·IMF·World Bank(대표 스냅샷)"),
}


def _grade(s: float) -> str:
    return "양호" if s < 30 else ("보통" if s < 45 else ("주의" if s < 60 else "경계"))


def macro_score(name: str) -> dict:
    """거래국 구조 리스크 0~100 + 성분 분해(성장·물가·금리·경상수지·신용·정치)."""
    c = COUNTRIES.get(name)
    if c is None:
        return {"name": name, "score": None, "grade": "정보 확인 필요", "components": {}}
    comp = {
        "신용등급": _s_rating(c.rating),
        "경상수지": _s_curracct(c.current_acct),
        "정치안정": _s_polit(c.political),
        "물가":     _s_infl(c.inflation),
        "성장":     _s_growth(c.growth),
    }
    score = sum(_W[k] * comp[k] for k in _W)
    return {"name": name, "score": round(score, 1), "grade": _grade(score),
            "rating": c.rating, "components": {k: round(v) for k, v in comp.items()}}


def combine(fx_ewi: float, macro: float) -> dict:
    """환율 변동성 축(FX-EWI) + 거래국 구조 축(매크로)을 결합 표시. 두 축을 반씩 반영하되
    원 성분(fx_axis·macro_axis)을 함께 노출해 축을 뭉개지 않는다."""
    c = 0.5 * fx_ewi + 0.5 * macro
    return {"combined": round(c, 1), "grade": _grade(c),
            "fx_axis": round(fx_ewi, 1), "macro_axis": round(macro, 1)}


def _demo():
    print("거래국 구조 리스크(국가 매크로 점수) — 높을수록 위험\n")
    print(f"{'거래국':8}{'점수':>6} {'등급':>6}  성분(신용·경상·정치·물가·성장)")
    print("─" * 66)
    ordered = sorted(COUNTRIES, key=lambda n: macro_score(n)["score"])
    for name in ordered:
        r = macro_score(name)
        cc = r["components"]
        comp = " ".join(f"{cc[k]:>3}" for k in ["신용등급", "경상수지", "정치안정", "물가", "성장"])
        print(f"{name:8}{r['score']:>6.1f} {r['grade']:>6}  {comp}")
    print("─" * 66)
    print("\n[결합 표시] 환율 변동성 축(FX-EWI) × 거래국 구조 축(매크로) — 베트남 예시")
    for ewi, label in [(38, "현재 정상"), (91, "2022 달러 강세")]:
        m = macro_score("베트남")["score"]
        cb = combine(ewi, m)
        print(f"  FX-EWI {ewi:>3}({label}) + 거래국 {m} → 결합 {cb['combined']} [{cb['grade']}]")


if __name__ == "__main__":
    import sys
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    _demo()
