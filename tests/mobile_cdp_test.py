# -*- coding: utf-8 -*-
"""모바일 실측 테스트 (CDP · 헤드리스 Chrome)

실행:  python tests/mobile_cdp_test.py
전제:  Chrome 설치 + websocket-client (pip install websocket-client)

왜 CDP 인가 — **--window-size 로는 모바일 폭을 잴 수 없다.** Windows 크롬은 창 최소 폭(~500px)을
강제해서 --window-size=390 을 줘도 실제 뷰포트가 485~526px 이 된다. 그래서 오랫동안
"390px 통과"라고 믿었던 결과가 전부 485px 측정이었다. Emulation.setDeviceMetricsOverride 만이
창과 무관하게 뷰포트를 강제한다.

무엇을 잡는가 — 문서 scrollWidth 검사로는 못 잡는 종류의 사고:
  .card 안에 min-width:640px 인 표가 있는데 .card 에 min-width:0 이 없으면
  그리드 항목의 기본 min-width:auto(=min-content) 때문에 카드가 화면 밖(right=655px)으로 나간다.
  이때 문서 scrollWidth 는 390 그대로라 **가로 스크롤조차 없이 그냥 잘려서 못 본다.**
"""
import json, subprocess, time, sys, io, os, tempfile, shutil
import urllib.request
import websocket   # websocket-client

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, os.pardir, "FX_Sentinel_demo_ui.html"))
PORT = 9333
URL = "file:///" + SRC.replace("\\", "/")

WIDTHS = [390, 360, 320]          # iPhone 14/15 · 안드로이드 소형 · iPhone SE
MODES = ["customer", "internal"]

proc = subprocess.Popen(
    [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
     "--remote-debugging-port=%d" % PORT, "--remote-allow-origins=*",
     "--user-data-dir=" + os.path.join(tempfile.gettempdir(), "fxs_cdp_profile"),
     "--force-prefers-reduced-motion", URL],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def targets():
    for _ in range(60):
        try:
            r = urllib.request.urlopen("http://127.0.0.1:%d/json" % PORT, timeout=1)
            t = json.loads(r.read().decode())
            pages = [x for x in t if x.get("type") == "page" and x.get("webSocketDebuggerUrl")]
            if pages: return pages[0]
        except Exception:
            pass
        time.sleep(0.5)
    return None

tgt = targets()
if not tgt:
    proc.kill(); print("CDP 연결 실패"); sys.exit(1)

ws = websocket.create_connection(tgt["webSocketDebuggerUrl"], timeout=30)
_id = [0]
def send(method, params=None):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            if "error" in m: raise RuntimeError(method + " :: " + json.dumps(m["error"], ensure_ascii=False))
            return m.get("result", {})

def ev(expr):
    r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
    if r.get("exceptionDetails"):
        raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300])
    return r["result"].get("value")

send("Page.enable"); send("Runtime.enable")
time.sleep(2.5)   # 부팅 스크립트(프리셋·AI요약) 완료 대기

PROBE = r"""
(function(){
  setMode("%MODE%");
  var views=["copilot","dashboard","trades","products","policy","fx","report","support"];
  var out={vw:window.innerWidth, docSW:document.documentElement.scrollWidth,
           docCW:document.documentElement.clientWidth, over:[], tap:[], clip:[]};
  /* 의도된 가로 스크롤 컨테이너 안이면 화면 밖에 있어도 정상이다(스크롤해서 볼 수 있다).
     인라인 style 만 보면 CSS 로 준 overflow-x 를 놓친다(모바일 .nav 가 그랬다) → 계산값으로 본다. */
  /* offsetParent 는 가시성 API 가 아니다 — 접힌 <details> 를 보인다고 오판하고,
     position:fixed(.toast)는 보여도 null 이라 검사에서 빠진다. checkVisibility() 가 정확하다. */
  function vis(e){ return e.checkVisibility
    ? e.checkVisibility({contentVisibilityAuto:true,opacityProperty:true,visibilityProperty:true})
    : e.offsetParent!==null; }
  function inScroller(el){
    for(var p=el.parentElement; p && p!==document.documentElement; p=p.parentElement){
      var ox=getComputedStyle(p).overflowX;
      if(ox==="auto"||ox==="scroll") return true;
    }
    return false;
  }
  // 앱 셸(앱바·사이드바)은 view-* 컨테이너 밖이라 예전엔 아예 검사되지 않았다 —
  // 그래서 390px 에서 시세카드가 폭 458px(right=470)로 삐져나가 잘린 걸 놓쳤다.
  var hosts=views.map(function(v){ showView(v); return document.getElementById("view-"+v); })
    .filter(Boolean).concat([document.querySelector(".appbar"), document.querySelector(".sidenav")].filter(Boolean));
  hosts.forEach(function(host){
    var v=(host.id||host.className.toString().split(" ")[0]||"?").replace("view-","");
    var lim=document.documentElement.clientWidth;
    [].slice.call(host.querySelectorAll("*")).forEach(function(e){
      if(!vis(e)) return;
      if(e.classList && e.classList.contains("sr-only")) return;
      var r=e.getBoundingClientRect();
      // 뷰포트 밖으로 나갔는가 (스크롤 컨테이너 내부는 제외)
      if(r.right>lim+1 && !inScroller(e)){
        out.over.push(v+" <"+e.tagName+"."+((e.className||"").toString().split(" ")[0]||"-")+"> right="+Math.round(r.right)+" w="+Math.round(r.width));
      }
      // 콘텐츠가 상자보다 넓은데 스크롤도 없으면 잘린다
      var cs=getComputedStyle(e);
      if(e.scrollWidth>e.clientWidth+1 && cs.overflowX==="hidden" && !inScroller(e))
        out.clip.push(v+" <"+e.tagName+"."+((e.className||"").toString().split(" ")[0]||"-")+"> SW"+e.scrollWidth+">CW"+e.clientWidth);
    });
    [].slice.call(host.querySelectorAll("button,select,input,a,summary")).forEach(function(e){
      if(!vis(e)) return;
      var r=e.getBoundingClientRect();
      if(r.height>0&&r.height<44) out.tap.push(Math.round(r.height)+"px <"+e.tagName+"."+((e.className||"").toString().split(" ")[0]||"-")+"> "+(e.textContent||"").trim().slice(0,18));
    });
  });
  showView("copilot");
  out.bbp=(document.getElementById("bbp")||{}).textContent;
  var wsp=document.querySelector(".workspace");
  out.chrome=wsp?Math.round(wsp.getBoundingClientRect().top):0;   // 콘텐츠 전 크롬 높이
  return JSON.stringify(out);
})()
"""

print("=== CDP Emulation.setDeviceMetricsOverride 실측 ===")
print("(--window-size 로는 Windows 크롬 최소폭 ~500px 때문에 불가능한 구간)")
print()
fail = 0
for w in WIDTHS:
    send("Emulation.setDeviceMetricsOverride",
         {"width": w, "height": 844, "deviceScaleFactor": 2, "mobile": True})
    time.sleep(0.4)
    for mode in MODES:
        r = json.loads(ev(PROBE.replace("%MODE%", mode)))
        bad = len(r["over"]) + len(r["clip"])
        status = "OK" if (bad == 0 and r["docSW"] <= r["docCW"] + 1) else "FAIL"
        if status == "FAIL": fail += 1
        chrome_pct = r.get("chrome", 0) / 844.0 * 100
        if r.get("chrome", 0) > 300: status = "FAIL"; 
        print("[%dpx · %-8s] vw=%s  가로스크롤=%s  BBP=%s  크롬 %spx(%.0f%%)  → %s"
              % (w, mode, r["vw"], "발생" if r["docSW"] > r["docCW"] + 1 else "없음",
                 r["bbp"], r.get("chrome", 0), chrome_pct, status))
        if r["over"]:
            print("   [뷰포트 이탈 %d건]" % len(r["over"]))
            for x in r["over"][:8]: print("      " + x)
        if r["clip"]:
            print("   [내용 잘림 %d건]" % len(r["clip"]))
            for x in r["clip"][:8]: print("      " + x)
        if r["tap"]:
            g = {}
            for t in r["tap"]: g[t.split("px")[0]] = g.get(t.split("px")[0], 0) + 1
            print("   [터치 44px 미만] " + ", ".join("%spx×%d" % (k, v) for k, v in sorted(g.items(), key=lambda x: int(x[0]))))
            for x in r["tap"][:3]: print("      " + x)
    print()

ws.close(); proc.kill()
print("결과: %s" % ("전부 통과" if fail == 0 else "%d개 조합 실패" % fail))
shutil.rmtree(os.path.join(tempfile.gettempdir(), "fxs_cdp_profile"), ignore_errors=True)
sys.exit(1 if fail else 0)
