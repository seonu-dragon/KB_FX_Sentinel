"""
FX Sentinel — 멀티에이전트 오케스트레이션 (S6-1)
=================================================
설계 근거: 청사진 §5.1(하이브리드), §6(에이전트 명세), A §4.3(방향층), F(v2)

하이브리드 구조:
  ① Sentinel  = 결정론 Python 코어 (bbp/fx_ewi 재사용) — 상시/배치
  ② Analyst   = 원인규명 XAI (V/J/C 기여분해 + 과거유사 + 개입국면)
  ③ Advisor   = 정보제공 (knowledge_base RAG-lite, 근거·출처)
  ④ Hedge     = 헤지 시나리오·비용(CIP)·KB상품·트리아지

NL 생성은 구조화 템플릿(실데이터 기반). LLM 어댑터 자리를 남겨, 라이브 API 없이도
end-to-end 데모가 돌아가되 그대로 LLM 강화 가능. (LLM은 이벤트드리븐 호출 대상.)
"""
from __future__ import annotations
import os
from dataclasses import dataclass
import numpy as np
import pandas as pd

from bbp import CompanyProfile, MarketState, assess, report as bbp_report
from backtest import cip_forward
import knowledge_base as kb
import llm  # LLM 자연어 설명층(실패 시 규칙기반 폴백)

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
WEIGHTS = {"V": 0.398, "J": 0.265, "C": 0.337}  # fx_ewi 규칙가중(재정규화됨)


def _load_row(date: str | None = None):
    """state 시계열에서 시장·성분 행 조달."""
    d = pd.read_csv(os.path.join(STATE, "fx_ewi_timeseries.csv"),
                    index_col=0, parse_dates=True).dropna(subset=["FX_EWI", "sigma_hat"])
    row = d.loc[date] if (date and date in d.index.astype(str)) else d.iloc[-1]
    dt = str((d.index[-1] if not (date and date in d.index.astype(str)) else pd.Timestamp(date)).date())
    return row, dt, d


# ───────────────────────────── ① Sentinel (결정론 코어)
class SentinelAgent:
    def run(self, profile: CompanyProfile, date: str | None = None) -> dict:
        row, dt, _ = _load_row(date)
        market = MarketState(date=dt, spot=float(row["Close"]), sigma_ann=float(row["sigma_hat"]),
                             fx_ewi=float(row["FX_EWI"]), iz=int(row["IZ"]))
        a = assess(profile, market)
        a["_row"] = row
        a["_market"] = market
        # 경보 트리거: 게이지 경계+ 또는 BBP 높음
        a["alert"] = (market.fx_ewi >= 60) or (a["BBP"] >= 0.4)
        return a


# ───────────────────────────── LLM 설명층(Analyst) — 숫자 그라운딩
_ANALYST_SYSTEM = (
    "당신은 KB 수출입 금융 AI 코파일럿의 애널리스트다. 아래에 이미 결정론 엔진이 계산한 "
    "사실(숫자)만 주어진다. 이 숫자를 중소 수출입기업 재무담당자가 이해하도록 자연스러운 "
    "한국어 2~3문장으로 설명한다.\n"
    "규칙: (1) 주어진 숫자만 그대로 사용하고 어떤 수치도 새로 만들거나 바꾸지 않는다. "
    "(2) 환율을 예측하지 않는다 — '오른다/내린다' 단정 금지. "
    "(3) 매수·매도 권유·수익 보장 금지, 정보 제공 톤. "
    "(4) 이모지·면책 문구·군더더기 금지, 담백하고 구체적으로. "
    "(5) 예산환율 대비 현재가의 위치와 이탈확률(BBP)의 의미를 우선 설명한다."
)


def _analyst_facts(s: dict, shares: dict, analog: str | None) -> str:
    p, m = s["profile"], s["_market"]
    pos = "수출(달러 롱)" if p["position"] == "export" else "수입(달러 숏)"
    iz = "예 (라운드넘버 부근 정책개입 추정)" if m.iz else "아니오"
    return (
        "[계산된 사실 — 이 숫자만 사용]\n"
        f"- 회사: {p['name']} ({p['sector']}, {pos})\n"
        f"- 기준일: {m.date} · 현재 원/달러 {m.spot:,.1f} · 예산환율 {p['budget_rate']:,.0f} · 결제만기 {p['horizon_days']}영업일\n"
        f"- 시장 위험게이지(FX-EWI): {m.fx_ewi:.0f}/100 [{s['gauge_grade']}]\n"
        f"- 위험 원인 분해: 변동성 국면 {shares['V']}% · 점프 집중 {shares['J']}% · 변동성 군집 {shares['C']}%\n"
        f"- 정책개입 국면: {iz}\n"
        f"- 예산환율 이탈확률(BBP): {s['BBP_pct']:.1f}%\n"
        f"- 이탈 시 기대손실(ES): {s['ES_total_krw']:,}원\n"
        f"- 과거 유사 국면: {analog or '해당 없음'}\n"
        f"- 권고 헤지수단: {s['instrument']}\n"
        "[요청] 위 사실만으로 이 회사의 환리스크 상황을 2~3문장으로 설명하세요."
    )


# ───────────────────────────── ② Analyst (원인 XAI + 거래국)
class AnalystAgent:
    def run(self, sentinel: dict, full: pd.DataFrame) -> dict:
        row = sentinel["_row"]; m = sentinel["_market"]
        # FX-EWI 요인 기여분해 (A §4.3): share_i = w_i·x_i / Σ
        contrib = {k: WEIGHTS[k] * float(row[k]) for k in WEIGHTS}
        tot = sum(contrib.values()) or 1.0
        shares = {k: round(100 * v / tot) for k, v in contrib.items()}
        top = max(shares, key=shares.get)
        names = {"V": "변동성 국면 확대", "J": "점프(급변) 집중", "C": "변동성 군집 심화"}
        # 과거 유사 국면 (성분 벡터 최근접, 60일 이전만)
        past = full.loc[:m.date].iloc[:-60][["V", "J", "C"]].dropna()
        cur = np.array([float(row["V"]), float(row["J"]), float(row["C"])])
        analog_date = None
        if len(past) > 100:
            dist = ((past.values - cur) ** 2).sum(axis=1)
            analog_date = str(past.index[int(np.argmin(dist))].date())
        narrative = (f"현재 위험게이지 {m.fx_ewi:.0f}/100 [{sentinel['gauge_grade']}]. "
                     f"주요 견인: {names[top]}({shares[top]}%)"
                     + (f", 그리고 " + ", ".join(f"{names[k]} {shares[k]}%" for k in shares if k != top) + ".")
                     + (" 원/달러가 라운드넘버 부근 정책개입 국면 — 변동성은 눌렸으나 돌파위험 잠복."
                        if m.iz else ""))
        # ── LLM 자연어 설명층: 결정론 숫자만 그라운딩해 서술 생성. 실패 시 규칙기반 narrative 유지
        source = "template"
        llm_text = llm.narrate(_ANALYST_SYSTEM, _analyst_facts(sentinel, shares, analog_date))
        if llm_text:
            narrative, source = llm_text, "llm"
        return {"shares": shares, "top_factor": names[top], "narrative": narrative,
                "narrative_source": source, "analog_date": analog_date}


# ───────────────────────────── ③ Advisor (정보제공 RAG-lite)
class AdvisorAgent:
    def run(self, profile: CompanyProfile) -> dict:
        tags = kb.situation_tags(profile)
        items = kb.retrieve(tags, top_k=3)
        return {"tags": tags, "items": items, "guardrail": kb.GUARDRAIL}


# ───────────────────────────── ④ Hedge (시나리오·비용·KB상품)
class HedgeAgent:
    def run(self, sentinel: dict, profile: CompanyProfile) -> dict:
        m = sentinel["_market"]
        rd, rf = self._rates(m.date)
        tau = profile.horizon_days / 252
        F = cip_forward(m.spot, rd, rf, tau)
        swap = F - m.spot
        h = sentinel["hedge_ratio"]
        notional = profile.amount_usd * h
        # 헤지 시 예산 대비 확보(선물환 기준, 수출): 만기 F로 고정
        locked_vs_budget = (F - profile.budget_rate) * notional
        kb_products = {
            "선물환": "KB 선물환(대고객)", "제로코스트 칼라 / 환변동보험": "KB 통화옵션 / K-SURE 환변동보험",
            "K-SURE 환변동보험 + 여신창출 체인": "K-SURE 환변동보험 + KB 선물환 특별한도(가정)",
        }.get(sentinel["instrument"], "KB 무역금융 상품")
        return {"forward_rate": round(F, 1), "swap_point": round(swap, 2),
                "rate_diff_pct": round((rd - rf) * 100, 2),
                "hedge_notional_usd": round(notional), "hedge_ratio": h,
                "instrument": sentinel["instrument"], "kb_product": kb_products,
                "locked_vs_budget_krw": round(locked_vs_budget),
                "rationale": sentinel["rationale"]}

    def _rates(self, date: str):
        try:
            from rates import load_rates
            idx = pd.DatetimeIndex([pd.Timestamp(date)])
            rt = load_rates(idx)
            return float(rt["r_d"].iloc[0]), float(rt["r_f"].iloc[0])
        except Exception:
            return 0.028, 0.037  # 폴백(최근 근사)


# ───────────────────────────── 오케스트레이터
class Orchestrator:
    def __init__(self):
        self.sentinel = SentinelAgent(); self.analyst = AnalystAgent()
        self.advisor = AdvisorAgent(); self.hedge = HedgeAgent()

    def run(self, profile: CompanyProfile, date: str | None = None) -> dict:
        _, _, full = _load_row(date)
        s = self.sentinel.run(profile, date)
        out = {"sentinel": s}
        if s["alert"] or True:  # 데모: 항상 전 플로우(실서비스는 alert 시 LLM 호출)
            out["analyst"] = self.analyst.run(s, full)
            out["advisor"] = self.advisor.run(profile)
            out["hedge"] = self.hedge.run(s, profile)
        return out


def render(o: dict, profile: CompanyProfile) -> str:
    s = o["sentinel"]; m = s["_market"]
    L = ["=" * 66,
         f" FX SENTINEL · 원스톱 환리스크 관제  —  {profile.name}",
         f" {m.date} · 원/달러 {m.spot:,.1f} · 예산환율 {profile.budget_rate:,.0f} · 만기 {profile.horizon_days}영업일",
         "=" * 66,
         f"[① Sentinel · 경보]  위험게이지 {m.fx_ewi:.0f}/100 [{s['gauge_grade']}]"
         + ("  🔔 경보 발동" if s["alert"] else "  (평상)"),
         f"    · 예산환율 이탈확률(BBP) {s['BBP_pct']:.1f}%  |  이탈 시 기대손실 {s['ES_total_krw']:,}원"]
    if "analyst" in o:
        a = o["analyst"]
        tag = "LLM" if a.get("narrative_source") == "llm" else "규칙기반"
        L += ["", f"[② Analyst · 원인 XAI · {tag}]", f"    {a['narrative']}"]
        if a["analog_date"]:
            L += [f"    · 과거 유사 국면: {a['analog_date']} (성분 최근접)"]
    if "advisor" in o:
        L += ["", f"[③ Advisor · 정보제공]"]
        for it in o["advisor"]["items"]:
            L += [f"    ▸ {it['title']} [{it['domain']}·신뢰{it['trust']}]",
                  f"        {it['summary'][:90]}…",
                  f"        (출처: {it['source']})"]
        L += [f"    {o['advisor']['guardrail']}"]
    if "hedge" in o:
        h = o["hedge"]
        L += ["", f"[④ Hedge · 실행 제안]",
              f"    · 권고수단: {h['instrument']}  →  {h['kb_product']}",
              f"    · 헤지비율 {h['hedge_ratio']:.0%} ({h['hedge_notional_usd']:,} USD)",
              f"    · 선물환율(CIP) {h['forward_rate']:,.1f}  (스왑포인트 {h['swap_point']:+.2f}, 금리차 {h['rate_diff_pct']:+.2f}%)",
              f"    · 근거: {h['rationale']}"]
    L += ["=" * 66]
    return "\n".join(L)


if __name__ == "__main__":
    import sys, argparse
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    ap = argparse.ArgumentParser(description="FX Sentinel 멀티에이전트 데모")
    ap.add_argument("--no-llm", action="store_true", help="LLM 비활성화(규칙기반 서술만)")
    args = ap.parse_args()
    if args.no_llm:
        llm.disable()
        print("[LLM] 비활성화(--no-llm) → 규칙기반 서술\n")
    else:
        print(f"[LLM] {'활성 · ' + llm.MODEL if llm.available() else '자격증명/패키지 없음 → 규칙기반 폴백'}\n")
    orch = Orchestrator()
    demos = [
        CompanyProfile("한빛정밀(수출·확정)", "export", 1450, 500_000, 63, "confirmed", True, False),
        CompanyProfile("대성무역(가결제)", "export", 1480, 300_000, 63, "provisional", True, True),
        CompanyProfile("소망전자(무여신)", "export", 1500, 200_000, 42, "confirmed", False, False),
        CompanyProfile("나래상사(수입)", "import", 1500, 400_000, 63, "confirmed", True, False),
    ]
    for pf in demos:
        print(render(orch.run(pf), pf))
        print()
