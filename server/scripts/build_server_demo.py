"""V2(서버연동 데모) 생성 — V1 을 마스터로 두고 연동 블록을 주입한다.

    python scripts/build_server_demo.py            # 생성
    python scripts/build_server_demo.py --check    # 최신인지만 검사 (테스트가 사용)

■ 왜 생성인가
1MB 짜리 HTML 두 벌을 손으로 유지하면 반드시 갈라진다. 이 프로젝트가 ELIG·상품마스터에서
계속 겪은 문제다. 그래서 **V1 이 마스터, V2 는 생성물**이다. V1 을 고치면 V2 를 다시 만든다.

■ V1 은 절대 건드리지 않는다
V1(FX_Sentinel_demo_ui.html)은 예선 제출물이다. 이 스크립트는 V1 을 읽기만 한다.

■ V2 가 지켜야 하는 것
  · 서버가 없어도 파일 단독으로 V1 과 똑같이 동작한다(폴백)
  · 로드 시 자동 호출하지 않는다 — 버튼을 눌러야 나간다
  · 폴백했으면 '서버 검증됨'이라고 말하지 않는다
  · 고객정보 외부 전송 정책(AI_EXTERNAL_CALL_ENABLED=false)은 그대로다
    (연동 대상은 사용자가 직접 지정한 자기 서버이지 외부 AI 가 아니다)
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
KB = os.path.abspath(os.path.join(ROOT, os.pardir))

SRC = os.path.join(KB, "FX_Sentinel_demo_ui.html")
OUT = os.path.join(KB, "FX_Sentinel_demo_ui_server.html")

ANCHOR = "</div><!-- /dp-judge -->"
MARKER = "<!-- @@SERVER_LINK_V2@@ -->"

# ── 주입할 패널 (④ 심사 자료 탭 안) ─────────────────────────────────
PANEL = MARKER + """
<section class="card" id="srv-card" style="border-left:3px solid var(--brand);margin-top:20px">
  <h3><span class="agent-n">⇄</span> 서버 재검증 (API 연동)
    <span class="dbadge" style="margin-left:auto">V2 · 연동판</span></h3>

  <div class="guard" style="margin-bottom:12px">
    이 화면의 BBP·자격 판정은 <b>브라우저에서 계산</b>됩니다. 실서비스라면 사용자가 콘솔에서
    바꿀 수 있으므로 <b>서버가 다시 판정</b>해야 합니다. 아래에서 실제 API 서버로 같은 입력을 보내
    <b>두 결과가 일치하는지</b> 확인합니다.
    <br><b>서버가 없으면 이 패널만 비활성이고 나머지 화면은 그대로 동작합니다.</b>
  </div>

  <div class="srv-form">
    <label class="srv-l" for="srv-base">서버 주소</label>
    <input class="srv-i" id="srv-base" value="http://127.0.0.1:8000" spellcheck="false"
           aria-label="API 서버 주소">
    <label class="srv-l" for="srv-token">액세스 토큰 (JWT)</label>
    <input class="srv-i" id="srv-token" placeholder="python scripts/dev_token.py --role rm 로 발급"
           spellcheck="false" aria-label="액세스 토큰">
    <div class="srv-act">
      <button type="button" class="go" id="srv-run" style="width:auto;margin-top:0">서버로 재검증</button>
      <button type="button" class="modebtn" id="srv-narr">AI 설명 생성 (서버 LLM)</button>
      <button type="button" class="modebtn" id="srv-ping">연동 현황 조회</button>
    </div>
  </div>

  <div class="guard" style="margin:2px 0 10px">
    <b>LLM 설명층</b>은 <b>서버(행내)</b>에서 돕니다 — 숫자는 결정론 엔진이 만들고 LLM은 그 숫자를 <b>서술만</b> 합니다.
    LLM에는 <b>기업명·예산환율·금액을 보내지 않습니다</b>(리스크 사실만). 서버에 LLM이 없거나 출력이
    가드레일(환각·금지표현)을 못 넘으면 <b>규칙 템플릿으로 폴백</b>합니다. 오프라인 V1은 이 경로 자체가 없습니다(행내 규칙 생성).
  </div>

  <div id="srv-out" class="srv-out" role="status" aria-live="polite" aria-busy="false"></div>
</section>
"""

STYLE = """
<style id="srv-style">
  /* 서버 재검증 패널 (V2 전용) */
  .srv-form{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center;margin-bottom:12px}
  .srv-l{font-size:var(--fs-2);font-weight:700;color:var(--ink-mut);white-space:nowrap}
  .srv-i{padding:8px 10px;border:1px solid var(--line-solid);border-radius:var(--r-sm);
    font-size:var(--fs-2);font-family:inherit;background:var(--panel);color:var(--ink);width:100%}
  .srv-act{grid-column:1/-1;display:flex;gap:8px;flex-wrap:wrap}
  .srv-out{margin-top:10px;font-size:var(--fs-2)}
  .srv-row{display:flex;gap:10px;padding:7px 10px;border-bottom:1px solid var(--line-solid);flex-wrap:wrap}
  .srv-row .k{color:var(--ink-mut);min-width:11em}
  .srv-row .v{font-family:var(--mono,monospace);color:var(--ink);font-weight:700}
  .srv-ok{color:var(--good);font-weight:800}
  .srv-bad{color:var(--crit);font-weight:800}
  .srv-warn{color:var(--elev);font-weight:800}
  .srv-note{margin-top:8px;padding:9px 11px;border-radius:var(--r-sm);
    background:var(--panel-2);color:var(--ink-mut);font-size:var(--fs-1);line-height:1.5}
  @media (max-width:700px){ .srv-form{grid-template-columns:1fr} .srv-l{margin-top:6px} }
  @media (forced-colors: active){ .srv-i,.srv-out,.srv-note{border:1px solid CanvasText} }
</style>
"""

SCRIPT = """
<script>
/* ══════════ V2 서버 재검증 (생성물 — build_server_demo.py) ══════════
   V1 에는 없는 블록이다. V1 은 서버 없이 도는 단일 파일 데모이고,
   V2 는 같은 화면에 "서버가 다시 판정한다"를 실증하는 패널을 붙인 것이다.

   원칙:
     · 로드 시 자동 호출 없음 — 버튼을 눌러야 나간다(재현성 테스트 보호)
     · 서버가 없으면 조용히 폴백하고, 폴백했다고 분명히 말한다
     · 서버 값과 화면 값이 다르면 숨기지 않고 '불일치'로 표시한다
*/
(function(){
  var SRV_TIMEOUT = 8000;

  function $id(x){ return document.getElementById(x); }
  function _esc(s){ return (typeof esc==="function") ? esc(s) : String(s); }

  function base(){ return ($id("srv-base").value||"").trim().replace(/\\/$/,""); }
  function token(){ return ($id("srv-token").value||"").trim(); }

  function out(html){ $id("srv-out").innerHTML = html; }
  function busy(on){ $id("srv-out").setAttribute("aria-busy", on ? "true" : "false"); }

  function row(k,v,cls){
    return '<div class="srv-row"><span class="k">'+_esc(k)+'</span>'
         + '<span class="v '+(cls||"")+'">'+_esc(v)+'</span></div>';
  }

  function note(msg){ return '<div class="srv-note">'+_esc(msg)+'</div>'; }

  async function call(path, body){
    var ctl = new AbortController();
    var t = setTimeout(function(){ ctl.abort(); }, SRV_TIMEOUT);
    try{
      var h = {"Content-Type":"application/json"};
      if(token()) h["Authorization"] = "Bearer " + token();
      var r = await fetch(base()+path, {
        method: body ? "POST" : "GET",
        headers: h,
        body: body ? JSON.stringify(body) : undefined,
        signal: ctl.signal, cache: "no-store"
      });
      var data = null;
      try{ data = await r.json(); }catch(e){}
      return { ok: r.ok, status: r.status, data: data };
    } finally { clearTimeout(t); }
  }

  /* 화면 폼 → 서버 스키마. 필드명을 서버와 같게 맞춰뒀다(schemas.TradeInput). */
  function payload(){
    var f = readForm();
    return { trade: {
      name: f.name||"", party: (typeof gateInfo!=="undefined" && gateInfo && gateInfo.party)||"",
      pos: f.pos, cert: f.cert, credit: f.credit, cash: f.cash, biz: f.biz,
      budget_rate: +f.budget, amount: +f.amount, horizon: +f.horizon,
      currency: f.currency||"USD", country: f.country||""
    }};
  }

  async function verify(){
    if(!base()){ out(note("서버 주소를 입력하세요.")); return; }
    busy(true);
    out(note("서버에 재검증을 요청하는 중…"));
    var localBBP = null;
    try{
      var f = readForm();
      localBBP = +(bbpOf(f, CUR[f.currency||"USD"].spot, MARKET.sigAnn, MARKET.iz) * 100).toFixed(1);
    }catch(e){}

    var res;
    try{ res = await call("/v1/assess", payload()); }
    catch(e){
      busy(false);
      out(note("서버에 연결하지 못했습니다 ("+(e && e.name==="AbortError" ? "타임아웃" : "네트워크 오류")
        +"). 화면은 로컬 계산 결과를 그대로 사용합니다 — 이 값은 서버로 검증되지 않았습니다."));
      if(typeof logAudit==="function") logAudit("서버 재검증 실패","연결 불가 · 로컬 계산 유지");
      return;
    }
    busy(false);

    if(!res.ok){
      var d = (res.data && (res.data.detail || res.data.message)) || "";
      var hint = res.status===401 ? " — 토큰이 없거나 유효하지 않습니다(dev_token.py 로 발급)"
               : res.status===403 ? " — 이 역할에는 권한이 없습니다"
               : res.status===422 ? " — 입력값이 서버 검증을 통과하지 못했습니다"
               : "";
      out(row("서버 응답","HTTP "+res.status,"srv-bad")
        + (d ? row("사유", d) : "")
        + note("서버 재검증에 실패했습니다"+hint
             + ". 화면 값은 로컬 계산이며 서버로 검증되지 않았습니다."));
      if(typeof logAudit==="function") logAudit("서버 재검증 거부","HTTP "+res.status+" "+d);
      return;
    }

    var s = res.data;
    var match = (localBBP !== null) && (Math.abs(s.bbp_pct - localBBP) < 0.05);
    var h = "";
    h += row("화면 계산 BBP", (localBBP===null?"—":localBBP+"%"));
    h += row("서버 재판정 BBP", s.bbp_pct+"%");
    h += row("일치 여부", match ? "일치 — 서버가 같은 결론" : "불일치 — 서버 값이 정답",
             match ? "srv-ok" : "srv-bad");
    h += row("부담액 상한(ES)", Number(s.es_total_krw).toLocaleString()+"원");
    h += row("헤지비율", Math.round(s.hedge_ratio*100)+"%");
    if(s.hedge_schedule && s.hedge_schedule.tranches && s.hedge_schedule.tranches.length){
      h += row("분할 체결", s.hedge_schedule.tranches.length+"회 · 목표 "
             + Math.round(s.hedge_schedule.target_ratio*100)+"%");
    }
    h += row("1순위 수단", s.instrument);
    h += row("엔진 버전", s.engine_version);
    h += row("시세 출처", s.market_source);
    h += row("감사 ID", s.audit_id);

    if(s.eligibility && s.eligibility.length){
      var no = s.eligibility.filter(function(e){ return !e.eligible; });
      h += row("서버 자격판정", (s.eligibility.length-no.length)+"/"+s.eligibility.length+" 적격");
      no.slice(0,4).forEach(function(e){ h += row("· "+e.key, e.reason, "srv-warn"); });
    }
    if(s.blocked && s.blocked.length){
      s.blocked.forEach(function(b){ h += row("서버 차단", b, "srv-warn"); });
    }
    h += note(s.disclaimer || "");
    if(!match){
      /* note() 는 HTML 을 이스케이프한다 — 마크다운 강조 기호를 쓰면 화면에 그대로 보인다. */
      h += note("화면과 서버 값이 다릅니다. 실서비스에서는 서버 판정이 권위이며, "
              + "화면 값은 참고입니다. 클라이언트 조작·버전 차이를 이렇게 잡아냅니다.");
    }
    out(h);
    if(typeof logAudit==="function")
      logAudit("서버 재검증", "BBP 화면="+localBBP+" 서버="+s.bbp_pct+" · "+(match?"일치":"불일치"));
  }

  async function ping(){
    if(!base()){ out(note("서버 주소를 입력하세요.")); return; }
    busy(true); out(note("연동 현황을 조회하는 중…"));
    var res;
    try{ res = await call("/v1/integrations", null); }
    catch(e){ busy(false); out(note("서버에 연결하지 못했습니다. 서버가 실행 중인지 확인하세요.")); return; }
    busy(false);
    if(!res.ok || !res.data){ out(note("연동 현황을 가져오지 못했습니다 (HTTP "+res.status+").")); return; }
    var d = res.data, h = "";
    (d.connected||[]).forEach(function(x){ h += row("연동됨", x, "srv-ok"); });
    (d.not_connected||[]).forEach(function(x){ h += row("미연동 · "+x.system, x.blocker, "srv-warn"); });
    h += note(d.note||"");
    out(h);
  }

  async function narrate(){
    if(!base()){ out(note("서버 주소를 입력하세요.")); return; }
    busy(true); out(note("서버 LLM 설명층에 요청하는 중… (숫자는 서버가 다시 계산하고, LLM은 서술만 합니다)"));
    var res;
    try{ res = await call("/v1/narrate", payload()); }
    catch(e){
      busy(false);
      out(note("서버에 연결하지 못했습니다 ("+(e && e.name==="AbortError" ? "타임아웃" : "네트워크 오류")
        +"). 오프라인 화면의 행내 요약(규칙 생성)은 그대로 동작합니다."));
      return;
    }
    busy(false);
    if(!res.ok){
      var d = (res.data && (res.data.detail || res.data.message)) || "";
      var hint = res.status===401 ? " — 토큰이 없거나 유효하지 않습니다(dev_token.py 로 발급)"
               : res.status===403 ? " — 이 역할에는 권한이 없습니다"
               : res.status===422 ? " — 입력값이 서버 검증을 통과하지 못했습니다" : "";
      out(row("서버 응답","HTTP "+res.status,"srv-bad")+(d?row("사유",d):"")
        + note("AI 설명 생성에 실패했습니다"+hint+"."));
      return;
    }
    var s = res.data, isLLM = (s.source === "llm");
    var h = "";
    h += '<div class="srv-note" style="background:var(--panel);border-left:3px solid '
       + (isLLM ? "var(--brand)" : "var(--ink-faint)") + ';color:var(--ink);line-height:1.7">'
       + _esc(s.narrative) + '</div>';
    h += row("생성 방식", isLLM ? "LLM 생성 (서버 · 가드레일 통과)" : "규칙 템플릿 (LLM 미구성/폴백)",
             isLLM ? "srv-ok" : "srv-warn");
    h += row("모델", s.model || "— (템플릿)");
    h += row("숫자 그라운딩", s.grounded ? "통과 — 출력 수치가 전부 계산값 근거" : "실패", s.grounded ? "srv-ok" : "srv-bad");
    h += row("근거 BBP (서버 계산)", s.bbp_pct + "%");
    h += row("엔진 버전", s.engine_version);
    h += row("감사 ID", s.audit_id);
    h += note("LLM에는 기업명·예산환율·금액을 보내지 않았습니다(리스크 사실만). "
            + (isLLM ? "" : "서버에 LLM이 구성되지 않아(AI_NARRATE_ENABLED·API 키) 규칙 템플릿으로 응답했습니다 — 그래도 유효한 설명입니다. ")
            + (s.disclaimer || ""));
    out(h);
    if(typeof logAudit==="function") logAudit("서버 AI 설명", (isLLM?"LLM":"템플릿")+" · BBP "+s.bbp_pct+"%");
  }

  function boot(){
    var r = $id("srv-run"), p = $id("srv-ping"), n = $id("srv-narr");
    if(!r || !p) return;
    r.addEventListener("click", verify);
    p.addEventListener("click", ping);
    if(n) n.addEventListener("click", narrate);
    /* 자동 호출하지 않는다 — 로드만으로 네트워크가 나가면
       '서버 없이 재현 가능'이라는 데모의 전제가 깨진다. */
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
</script>
"""


def build(src_text: str) -> str:
    if ANCHOR not in src_text:
        raise SystemExit(f"V1 에서 앵커를 찾지 못했습니다: {ANCHOR}")
    if MARKER in src_text:
        raise SystemExit("V1 에 이미 V2 블록이 있습니다 — V1 이 오염됐는지 확인하십시오")
    out = src_text.replace(ANCHOR, PANEL + "\n" + ANCHOR, 1)
    out = out.replace("</head>", STYLE + "</head>", 1)
    out = out.replace("</body>", SCRIPT + "\n</body>", 1)
    # 제목으로 두 판을 구분한다 — 심사위원이 탭만 보고도 안다
    out = out.replace("<title>KB Star FX 수출입 금융 AI 코파일럿</title>",
                      "<title>KB Star FX 수출입 금융 AI 코파일럿 · 서버연동판(V2)</title>", 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="최신인지 검사만 (생성하지 않음)")
    a = ap.parse_args()

    src = io.open(SRC, encoding="utf-8", newline="").read()
    want = build(src)

    if a.check:
        if not os.path.exists(OUT):
            print("V2 가 없습니다 — build_server_demo.py 를 실행하십시오")
            return 1
        got = io.open(OUT, encoding="utf-8", newline="").read()
        if got != want:
            print("V2 가 V1 과 어긋납니다 — build_server_demo.py 를 다시 실행하십시오")
            return 1
        print(f"V2 최신 — {os.path.basename(OUT)} ({len(got):,} bytes)")
        return 0

    io.open(OUT, "w", encoding="utf-8", newline="").write(want)
    print(f"생성 완료: {OUT}")
    print(f"  V1 {len(src):,} bytes → V2 {len(want):,} bytes (+{len(want)-len(src):,})")
    print("  V1 은 수정하지 않았습니다.")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main())
