# -*- coding: utf-8 -*-
"""텍스트 확대 테스트 (CDP · WCAG 1.4.4)

실행:  python tests/text_zoom_test.py
전제:  Chrome + websocket-client

왜 필요한가 — 브라우저 확대(Ctrl +)는 px 도 같이 키우지만, 저시력 사용자는 보통
**브라우저 '기본 글꼴 크기' 설정**을 키운다. 폰트가 px 면 이 설정이 통째로 무시된다.
이 제품의 타깃이 50~60대 SME 사장님이라 그 차이가 실제로 중요하다.
rem 이면 글자는 커지지만 레이아웃이 넘칠 수 있다 → 실제로 넘치는지 본다.

Page.setFontSizes 가 필요해서 일반 스모크 테스트로는 잡을 수 없다.
"""
import json, subprocess, time, os, tempfile, sys, shutil
import urllib.request, websocket
CHROME=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
HERE=os.path.dirname(os.path.abspath(__file__))
SRC=os.path.abspath(os.path.join(HERE, os.pardir, "FX_Sentinel_demo_ui.html"))
PORT=9352
proc=subprocess.Popen([CHROME,"--headless=new","--disable-gpu","--no-sandbox",
  "--remote-debugging-port=%d"%PORT,"--remote-allow-origins=*",
  "--user-data-dir="+os.path.join(tempfile.gettempdir(),"fxs_zm"),
  "--force-prefers-reduced-motion","file:///"+SRC.replace("\\","/")],
  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def tgt():
    for _ in range(60):
        try:
            t=json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json"%PORT,timeout=1).read().decode())
            p=[x for x in t if x.get("type")=="page" and x.get("webSocketDebuggerUrl")]
            if p: return p[0]
        except Exception: pass
        time.sleep(0.5)
T=tgt(); ws=websocket.create_connection(T["webSocketDebuggerUrl"],timeout=60)
_i=[0]
def send(m,p=None):
    _i[0]+=1; ws.send(json.dumps({"id":_i[0],"method":m,"params":p or {}}))
    while True:
        r=json.loads(ws.recv())
        if r.get("id")==_i[0]:
            if "error" in r: raise RuntimeError(json.dumps(r["error"],ensure_ascii=False))
            return r.get("result",{})
def ev(e): return send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})["result"].get("value")
send("Page.enable"); send("Runtime.enable"); time.sleep(3)

PROBE = r"""
(function(){
  setMode("customer");
  var views=["copilot","dashboard","trades","products","policy","fx","report","support"];
  var de=document.documentElement, out={rootPx:parseFloat(getComputedStyle(de).fontSize), over:[], clip:[]};
  /* offsetParent 는 가시성 API 가 아니다 — 접힌 <details> 를 보인다고 오판하고,
     position:fixed(.toast)는 보여도 null 이라 검사에서 빠진다. checkVisibility() 가 정확하다. */
  function vis(e){ return e.checkVisibility
    ? e.checkVisibility({contentVisibilityAuto:true,opacityProperty:true,visibilityProperty:true})
    : e.offsetParent!==null; }
  function inScroller(el){
    for(var p=el.parentElement;p&&p!==de;p=p.parentElement){
      var ox=getComputedStyle(p).overflowX; if(ox==="auto"||ox==="scroll") return true;
    } return false;
  }
  var hosts=views.map(function(v){showView(v);return document.getElementById("view-"+v);}).filter(Boolean)
    .concat([document.querySelector(".appbar"),document.querySelector(".sidenav")].filter(Boolean));
  hosts.forEach(function(h){
    var lim=de.clientWidth;
    [].slice.call(h.querySelectorAll("*")).forEach(function(e){
      if(!vis(e)||(e.classList&&e.classList.contains("sr-only"))) return;
      var r=e.getBoundingClientRect();
      if(r.right>lim+1 && !inScroller(e))
        out.over.push("<"+e.tagName+"."+((e.className||"").toString().split(" ")[0]||"-")+"> right="+Math.round(r.right));
      var cs=getComputedStyle(e);
      if(e.scrollWidth>e.clientWidth+1 && cs.overflowX==="hidden" && !inScroller(e))
        out.clip.push("<"+e.tagName+"."+((e.className||"").toString().split(" ")[0]||"-")+"> SW"+e.scrollWidth+">CW"+e.clientWidth);
    });
  });
  showView("copilot");
  out.docOver = de.scrollWidth > de.clientWidth+1;
  out.bbpSize = parseFloat(getComputedStyle(document.getElementById("bbp")).fontSize);
  return JSON.stringify(out);
})()
"""
print("=== 브라우저 기본 글꼴 크기별 (WCAG 1.4.4 텍스트 확대) ===")
print()
fail = 0
for w,label in [(1440,"데스크톱"),(390,"모바일")]:
    for root in [16, 20, 24]:
        send("Emulation.setDeviceMetricsOverride",{"width":w,"height":900 if w>500 else 844,
             "deviceScaleFactor":1,"mobile":w<500})
        send("Page.setFontSizes",{"fontSizes":{"standard":root}})
        time.sleep(0.6)
        r=json.loads(ev(PROBE))
        bad=len(r["over"])+len(r["clip"])
        st = "OK" if (bad==0 and not r["docOver"]) else "FAIL"
        # 기본글꼴을 키웠는데 글자가 안 커지면 rem 이 아니라는 뜻 = 이 테스트의 존재 이유가 무너진다
        if root == 24 and r["bbpSize"] < 50: st = "FAIL"
        if st == "FAIL": fail += 1
        print("[%s %dpx · 기본글꼴 %dpx] 루트=%spx · BBP숫자=%spx · 가로스크롤=%s · 이탈%d 잘림%d → %s"
              % (label,w,root,r["rootPx"],r["bbpSize"],"발생" if r["docOver"] else "없음",
                 len(r["over"]),len(r["clip"]),st))
        for x in r["over"][:4]: print("     이탈 "+x)
        for x in r["clip"][:4]: print("     잘림 "+x)
    print()
ws.close(); proc.kill()
shutil.rmtree(os.path.join(tempfile.gettempdir(), "fxs_zm"), ignore_errors=True)
print("결과: %s" % ("전부 통과" if fail == 0 else "%d개 조합 실패" % fail))
sys.exit(1 if fail else 0)
