"""FX Sentinel 데모 UI 스모크 테스트 (헤드리스 Chrome)

실행:  python tests/ui_smoke_test.py
전제:  Google Chrome 설치. 그 외 의존성 없음(표준 라이브러리만).

무엇을 지키는 테스트인가 — 이 데모는 '파일로 배포'되므로 다음이 깨지면 안 된다:
  1. 재현성   — 자동 시세조회 없이 기준일 스냅샷으로 고정(누가 언제 열어도 같은 숫자)
  2. 복원력   — 워커/네트워크가 죽어도 오류문구 없이 로컬 폴백 요약이 뜬다
  3. 정합성   — 코파일럿·대시보드·목록·리포트가 같은 국면/거래국 등급으로 계산된다
  4. 정직성   — 'KB 고시환율' 오표기 없음, 데모 데이터에 배지, 캘리브레이션 수치는 상수 단일출처

주의: BBP 게이지(#bbp)는 animNum()이 600ms 트윈하므로 DOM 값을 읽으려면
      --force-prefers-reduced-motion 이 필요하다(없으면 중간값이 잡힌다).
"""
import io
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 기본은 V1(예선 제출물). FXS_TARGET 으로 V2(서버연동판)도 같은 검사를 돌린다 —
# V2 는 V1 에서 생성되므로 두 판이 같은 불변식을 지키는지 확인할 수 있어야 한다.
#   python tests/ui_smoke_test.py                        → V1
#   FXS_TARGET=FX_Sentinel_demo_ui_server.html ...        → V2
SRC = os.path.join(HERE, os.pardir,
                   os.environ.get("FXS_TARGET", "FX_Sentinel_demo_ui.html"))

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

MARK = "@@FXS@@"

# 페이지에 주입해 동기적으로 단언을 수행하는 프로브.
# 구분자는 ' ;; ' — JS 문자열에 개행을 넣지 않기 위함(이스케이프 사고 방지).
PROBE = r"""
<script>
(function(){
  var L = [];
  function ok(name, cond, info){ L.push((cond ? "PASS" : "FAIL") + " | " + name + " | " + info); }
  /* offsetParent 는 가시성 API 가 아니다 — 접힌 <details>(content-visibility:hidden) 를
     보인다고 오판하고, position:fixed 요소(.toast)는 보여도 null 이라 검사에서 빠진다.
     checkVisibility() 가 정확하다. 구형 브라우저 대비 폴백만 남긴다. */
  function _vis(e){
    if (e.checkVisibility) return e.checkVisibility(
      { contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true });
    return e.offsetParent !== null;
  }

  /* ── 재현성: 자동 시세조회 없이 스냅샷 고정 ── */
  ok("시세 기본=스냅샷",
     _ratesInfo.live === false && CUR.USD.spot === 1528.8 && _ratesInfo.asof === MARKET.date,
     "live=" + _ratesInfo.live + " spot=" + CUR.USD.spot + " asof=" + _ratesInfo.asof);
  ok("사이드바 기준일 표기",
     $("rate-status").textContent.indexOf("기준일 시세") >= 0,
     $("rate-status").textContent);
  /* ── 시세 라벨: 스냅샷을 "현재"라고 부르지 않는가 ──
     같은 카드에 "현재 USD/KRW"와 "기준일 스냅샷"이 함께 뜨면 자기모순이다(11일 전 값을 현재라고 표기). */
  fillPreset(PRESETS[3]);
  ok("스냅샷=기준일 표기", $("tk-pair").textContent.indexOf("기준일") >= 0
     && $("tk-pair").textContent.indexOf("현재") < 0, $("tk-pair").textContent);
  ok("스냅샷 ctx 표기", $("ctx").textContent.indexOf("기준일 시세") >= 0, "현재로 단정 안 함 · 회사 기준환율과 구분");
  var _riSave = _ratesInfo;
  _ratesInfo = { live: true, asof: "2026-07-16", source: "test" };   // 당일 조회 상태 흉내
  _setRateStatus(); compute();
  ok("당일조회=현재 표기", $("tk-pair").textContent.indexOf("현재") >= 0
     && $("ctx").textContent.indexOf("현재 시세") >= 0, $("tk-pair").textContent);
  _ratesInfo = _riSave; _setRateStatus(); compute();

  /* ── 정직성: 라벨 정확성 + opt-in 버튼 ── */
  showView("fx");
  ok("당일 참고시세 버튼", document.getElementById("fx-live") !== null, "외환/시세 opt-in");
  /* 어미 변화("아니며"/"아닙니다")에 안 걸리도록 어간으로 검사 */
  ok("KB 고시환율 아님 고지",
     /KB 고시환율이 아(니|닙)/.test(document.getElementById("view-fx").textContent),
     "공개 API 라벨 명시");
  showView("copilot");

  /* 실서비스 화면 방향으로 '데모 데이터' 배지·시연 표기는 제거했다
     (정직성 근거는 심사 자료 탭·README 에 유지). 전 뷰를 한 번씩 렌더해
     아래 label·명도대비 검사가 JS 렌더분까지 포함하도록만 한다. */
  ["dashboard", "trades", "report", "policy", "products", "support"].forEach(showView);
  showView("copilot");

  /* ── 접근성: label ↔ 입력 연결 (정적 HTML + JS 렌더 뷰 전부) ── */
  var labs = [].slice.call(document.querySelectorAll("label"));
  var withFor = labs.filter(function(l){ return l.getAttribute("for"); });
  var broken = withFor.filter(function(l){ return !document.getElementById(l.getAttribute("for")); });
  ok("label for= 전수 연결", labs.length > 0 && withFor.length === labs.length,
     withFor.length + "/" + labs.length + " 연결");
  ok("label for= 대상 존재", broken.length === 0,
     broken.length ? broken.map(function(l){ return l.getAttribute("for"); }).join(",") : "깨진 참조 없음");

  /* ── 정합성 ①: 국면 스트레스가 목록/대시보드까지 전파 ── */
  setRegime(0); var d0 = _deal(PRESETS[0]);
  setRegime(3); var d3 = _deal(PRESETS[0]);          // 2022 달러 강세(σ 0.164, EWI 91)
  ok("국면 전파 · BBP", d3.bbp > d0.bbp,
     "평시=" + d0.bbp.toFixed(1) + "% → 달러 강세=" + d3.bbp.toFixed(1) + "%");
  ok("국면 전파 · 경보", d3.alert === true, "달러 강세 EWI=91 → alert=true");
  setRegime(3); renderDashboard();
  var king = document.querySelector("#view-dashboard .tiles").textContent;
  setRegime(0); renderDashboard();
  ok("대시보드 타일 국면 반영",
     king !== document.querySelector("#view-dashboard .tiles").textContent, "달러 강세 ≠ 평시");
  setRegime(0); showView("copilot");

  /* ── 정합성 ②: 민감도 표와 본 카드가 같은 헤지 규율(옛 b>=50 게이트 잔존 금지) ── */
  $("in-pos").value = "import"; $("in-horizon").value = "63"; compute();
  var S = CUR.USD.spot, lo = S * 0.9, hi = S * 1.1, mid;
  for (var k = 0; k < 60; k++) {                      // BBP≈45%가 되는 예산환율 이분탐색
    mid = (lo + hi) / 2;
    var sig = REG.USD[regimeIdx].sigAnn;   // 통화별 실측 σ̂ (구조상 배수가 사라졌다)
    if (bbpProb(S, mid, sig, 63, "import", MARKET.iz) * 100 > 45) lo = mid; else hi = mid;
  }
  $("in-budget").value = String(mid); compute();
  var bbp = parseFloat($("bbp").textContent);
  var row = $("sens-table").querySelectorAll("tbody tr")[0];
  var cell = row.cells[row.cells.length - 1].textContent;
  var cardHedge = $("decide-act").textContent.indexOf("부분") >= 0;
  ok("민감도 40~50 구간 진입", bbp > 40 && bbp < 50, "BBP=" + bbp.toFixed(1) + "%");
  ok("민감도 표 = 본 카드", (cell.indexOf("부분") >= 0) === cardHedge,
     "표='" + cell + "' 카드부분헤지=" + cardHedge);
  var hr = hedgeRatio((MARKET.ewi / 100) * (bbp / 100));
  ok("옛 b>=50 게이트 없음", !(bbp < 50 && hr > 0 && cell.indexOf("모니터링") >= 0),
     "BBP=" + bbp.toFixed(1) + "% hr=" + hr);

  /* ── 입력 검증: 잘못된 값에 숫자를 지어내지 않는가 ──
     은행 폼에서 오타 하나(만기 −30)로 BBP=NaN이 뜨면 실서비스 불가. 계산을 멈추고 이유를 말해야 한다. */
  [["in-horizon", "-30"], ["in-horizon", "0"], ["in-horizon", "63.5"], ["in-budget", "-1500"],
   ["in-amount", "-500000"], ["in-amount", "0"], ["in-margin", "150"], ["in-amount", ""]].forEach(function(c){
    fillPreset(PRESETS[3]);
    $(c[0]).value = c[1]; compute();
    var shown = $("bbp").textContent + " " + $("hf-es").textContent + " " + $("h-ratio").textContent;
    var e = document.getElementById("err-" + c[0]);
    ok("검증 " + c[0] + "=" + (c[1] === "" ? "(빈값)" : c[1]),
       $("verdict").textContent.indexOf("입력을 확인해 주세요") >= 0        /* 계산 중단 */
       && e && e.style.display === "block"                                  /* 인라인 사유 */
       && $(c[0]).getAttribute("aria-invalid") === "true"                   /* 스크린리더 인지 */
       && !/NaN|Infinity|undefined/.test(shown),                            /* 틀린 숫자 미노출 */
       "차단·사유·aria·NaN없음");
  });
  fillPreset(PRESETS[3]);
  ok("오류 후 정상 복구", $("bbp").textContent === "64.3", "BBP=" + $("bbp").textContent);
  ok("프리셋이 마진율 리셋", $("in-margin").value === "8", "값=" + $("in-margin").value);
  ok("aria-describedby 연결", document.querySelectorAll("[aria-describedby]").length >= 4,
     document.querySelectorAll("[aria-describedby]").length + "개");

  /* ── 정합성 ③: 수출/수입 예산 유불리 문구 ── */
  $("in-pos").value = "export"; $("in-budget").value = "1600"; compute();
  var t = $("hf-dir").textContent;
  ok("수출 불리 문구", t.indexOf("이미 불리") >= 0 && t.indexOf("미달") >= 0 && t.indexOf("이내") < 0, t);
  $("in-pos").value = "import"; $("in-budget").value = "1450"; compute();
  t = $("hf-dir").textContent;
  ok("수입 불리 문구", t.indexOf("이미 불리") >= 0 && t.indexOf("상회") >= 0, t);

  /* ── 정합성 ④: 목록/리포트가 거래국 등급을 실제 반영 ── */
  ok("베트남 등급=주의", macroScore("베트남").grade === "주의", macroScore("베트남").grade);
  var vn = PRESETS[3];
  ok("거래국 리스크 반영(베트남)",
     _recsOf(vn, _deal(vn)).some(function(r){ return r.t.indexOf("거래국 리스크 대응") >= 0; }),
     "후보 포함");
  var us = PRESETS[0];
  ok("미국(보통)은 미포함",
     !_recsOf(us, _deal(us)).some(function(r){ return r.t.indexOf("거래국 리스크 대응") >= 0; }),
     "grade=" + macroScore("미국").grade);

  /* ── 라우팅: 제안서 §6.2 대표 라우팅과 일치 ── */
  fillPreset(PRESETS[3]);
  var p3 = primaryProduct(_recsOf(PRESETS[3], _deal(PRESETS[3])), PRESETS[3].biz);
  var f3 = p3 ? fitCount(p3) : null;
  ok("나래상사 1순위", p3 && p3.t === "KB Payment Usance" && p3.st === "추천",
     p3 ? p3.t + "[" + p3.st + "]" : "-");
  /* '적합도 86' 같은 하드코딩 점수는 근거 없는 정밀도였다 → 확인된 자격요건 수(사실)만 표시 */
  ok("자격요건은 점수 아닌 사실", p3 && p3.fit && p3.fit.score === undefined
     && f3.tot > 0 && f3.np + f3.nu === f3.tot, f3 ? f3.np + "/" + f3.tot + " 확인" : "-");
  var p2 = primaryProduct(_recsOf(PRESETS[2], _deal(PRESETS[2])), PRESETS[2].biz);
  ok("소망전자 1순위", p2 && p2.t.indexOf("특별출연") >= 0 && p2.st === "RM확인",
     p2 ? p2.t + "[" + p2.st + "]" : "-");
  var p4 = primaryProduct(_recsOf(PRESETS[4], _deal(PRESETS[4])), PRESETS[4].biz);
  var soleRecs = _recsOf(PRESETS[4], _deal(PRESETS[4]));
  ok("하늘샵 1순위=글로벌셀러", p4 && p4.t === "KB 글로벌셀러 우대서비스", p4 ? p4.t : "-");
  ok("하늘샵 특별출연 제외",
     !soleRecs.some(function(r){ return r.t.indexOf("특별출연") >= 0; }), "개인사업자 자동 제외");

  /* ── 프리셋 5종 전수: 화면 두 곳이 반대로 말하지 않는가 ──
     triage(헤지 카드)와 recommendPackage(상품 라우팅)는 별개 함수라 규칙이 갈라지기 쉽다.
     한 프리셋만 보면 절대 안 잡히는 종류의 사고(하늘샵=개인사업자에서 실제로 발생했다). */
  PRESETS.forEach(function(p){
    fillPreset(p);
    var recs = _recsOf(p, _deal(p));
    var routed = recs.some(function(r){ return r.t.indexOf("특별출연") >= 0; });
    var hedged = ($("h-inst").textContent + $("h-prod").textContent).indexOf("특별출연") >= 0;
    ok("라우팅↔헤지 일치 " + p.name, !(hedged && !routed),
       (hedged && !routed) ? "헤지가 라우팅 제외 상품을 권함!" : "일치");
    /* 관리비율 0%면 상품을 헤드라인에 올리지 않는다(하라는 건지 말라는 건지 모호해짐) */
    var r0 = ($("h-ratio").textContent === "0%");
    ok("0% 구간 표현 " + p.name,
       !r0 || ($("h-inst").textContent.indexOf("관리 불필요") >= 0
               && $("h-why").textContent.indexOf("지켜보셔도") >= 0
               && $("h-why").textContent.indexOf("검토. ·") < 0),
       r0 ? "모니터링 문구" : "관리 구간(" + $("h-ratio").textContent + ")");
    /* 조사: "○○은(는)" 같은 미완성 표기가 남으면 안 된다 */
    ok("조사 처리 " + p.name, $("narr").textContent.indexOf("은(는)") < 0, "정상");
  });
  fillPreset(PRESETS[3]);

  /* ── 목록/문서 뷰: 프리셋 전수로 뷰 간 숫자·추천이 갈라지지 않는가 ──
     같은 회사가 코파일럿과 대시보드에서 다른 BBP를 보이면 신뢰가 한 번에 무너진다. */
  var _cp = {};
  PRESETS.forEach(function(p){ fillPreset(p); _cp[p.name] = $("bbp").textContent; });
  showView("dashboard");
  var _dash = {};
  /* 거래 목록만 집는다(.dtable). 예전엔 "#view-dashboard tbody tr" 로 훑었는데,
     대시보드에 표가 하나라도 추가되면(반복결제 카드 등) 칸 수가 달라 cells[5] 에서 터진다. */
  [].slice.call(document.querySelectorAll("#view-dashboard .dtable tbody tr")).forEach(function(tr){
    _dash[tr.cells[0].textContent.trim()] = tr.cells[5].textContent.trim().replace("%", "");
  });
  showView("trades");
  var _tr = {};
  [].slice.call(document.querySelectorAll("#trade-body tr[data-i]")).forEach(function(tr){
    _tr[tr.cells[0].textContent.trim()] = { bbp: tr.cells[5].textContent.trim().replace("%", ""), rec: tr.cells[6].textContent.trim() };
  });
  showView("report");
  var _rp = {};
  [].slice.call(document.querySelectorAll("#rpt-body tr")).forEach(function(tr){
    _rp[tr.cells[0].textContent.replace(" 진단 리포트", "").trim()] = tr.cells[2].textContent.trim().replace("%", "");
  });
  PRESETS.forEach(function(p){
    var c = _cp[p.name], dd = _dash[p.name], t = (_tr[p.name] || {}).bbp, r = _rp[p.name];
    ok("BBP 뷰간 일치 " + p.name, c === dd && dd === t && t === r,
       "코파일럿 " + c + " / 대시보드 " + dd + " / 거래조회 " + t + " / 리포트 " + r);
    var prim = primaryProduct(_recsOf(p, _deal(p)), p.biz);
    var want = prim ? prim.t.split("(")[0].split(" /")[0].trim() : "-";
    ok("1순위 뷰간 일치 " + p.name, want === ((_tr[p.name] || {}).rec || "-"), want);
  });
  /* 용어: 전 화면 '부담액(보수적 상한)'으로 통일했는지 */
  ["dashboard", "trades", "report"].forEach(function(v){
    showView(v);
    var t = document.getElementById("view-" + v).textContent;
    var bad = ["기대손실", "예상 부담액", "이탈확률", "적합도"].filter(function(w){ return t.indexOf(w) >= 0; });
    ok("용어 통일 " + v, bad.length === 0, bad.length ? "잔존: " + bad.join(",") : "정상");
  });
  /* 리포트는 PDF로 은행 밖으로 나간다 — 숫자만 남고 근거가 빠지면 안 된다
     (검사 문구에 '보수'만 넣으면 '보수적 상한'에 걸려 거짓 통과한다 → 배수·이유를 함께 확인) */
  showView("report");
  var _rpv = document.getElementById("rpt-prev").textContent;
  ok("리포트 상한 근거 고지",
     _rpv.indexOf("보수적 상한") >= 0 && _rpv.indexOf(ES_BIAS.ratio.toFixed(1) + "배") >= 0
     && _rpv.indexOf("실제 부담은 이보다 작을 수 있습니다") >= 0, "배수·이유 포함");
  /* 'ECE'라는 용어가 아니라 사실이 고지됐는가 — 실제 초과 빈도 + 방향별 오차 배수.
     고객에게 지표 약칭은 의미가 없어 평문으로 바꿨다(용어를 다시 넣어도 이 검사는 통과한다). */
  ok("리포트 방향별 고지",
     _rpv.indexOf((CALIB_DIR.exportRate * 100).toFixed(1) + "%") >= 0
     && _rpv.indexOf("평균의 4배") >= 0, "실제 초과빈도·오차 배수 명시");
  /* BBP 표기는 전 화면 소수 1자리(스크린리더 aria 포함) */
  showView("copilot"); fillPreset(PRESETS[3]);
  ok("BBP 표기 일관(aria 포함)",
     $("bbp-gauge-wrap").getAttribute("aria-label").indexOf("64.3퍼센트") >= 0, "화면=SR 동일");
  setMode("internal");

  /* ── 상품 자격 규칙 단일 소스(ELIG) ──
     같은 규칙이 라우팅·헤지·정책자금 3곳에 흩어져 매번 갈라졌다(개인사업자·여신보유·수입 건에서 각각 사고).
     이제 판정은 ELIG 한 곳에서만 한다. 조합을 돌려 세 화면이 같은 답을 내는지 확인한다. */
  ok("ELIG 단일 소스 존재", typeof ELIG === "object" && typeof eligible === "function", "-");
  ["export", "import"].forEach(function(pos){
    ["yes", "no"].forEach(function(credit){
      ["corp", "sole"].forEach(function(biz){
        var f = { pos: pos, credit: credit, biz: biz, cert: "confirmed", cash: "ok", name: "테스트", party: "" };
        var elig = eligible("특별출연", f);
        /* 헤지 트리아지가 자격 없는 특별출연을 권하면 안 된다 */
        var t = triage(f)[0] + " " + triage(f)[1];   // 권고 수단·상품명만(설명문 제외 — 제외 사유에 이름이 나올 수 있음)
        ok("특별출연 자격↔헤지 " + pos + "/" + credit + "/" + biz,
           !(t.indexOf("특별출연") >= 0 && !elig),
           elig ? "자격 O" : "자격 X — " + denyReason("특별출연", f));
        /* 라우팅도 같은 답 */
        var routed = recommendPackage(f, { bbp: 50, alert: true, badBudget: true, macroGrade: "보통", gate: false })
          .some(function(r){ return r.t.indexOf("특별출연") >= 0; });
        ok("특별출연 자격↔라우팅 " + pos + "/" + credit + "/" + biz, routed === elig,
           "라우팅 " + (routed ? "추천" : "제외") + " = ELIG " + (elig ? "O" : "X"));
      });
    });
  });
  /* 수출 전용 상품이 수입 건에 새지 않는가(프리셋엔 없던 조합 — 실제로 있던 버그) */
  var _imp = { pos: "import", credit: "no", biz: "corp", cert: "confirmed", cash: "ok", name: "테스트", party: "" };
  ok("수입 건에 수출 전용 상품 미노출", (triage(_imp)[0]+triage(_imp)[1]).indexOf("특별출연") < 0,
     denyReason("특별출연", _imp));

  /* 외화상품(예금·대출)도 같은 단일 소스를 따르는가 — 주제 #6의 '외화 금융 상품' 축.
     외화예금은 외화를 '받는' 수출·정산 건, 외화대출은 외화를 '내는' 수입 건 + 여신 한도가 전제다.
     방향을 뒤집어 권하면 창구에서 바로 들통나는 종류의 오류라 조합으로 고정한다. */
  ["export", "import"].forEach(function(pos){
    ["yes", "no"].forEach(function(credit){
      var f = { pos: pos, credit: credit, biz: "corp", cert: "confirmed", cash: "ok",
                name: "테스트", party: "", currency: "USD", pay: "TT", hs: "ETC" };
      var routed = recommendPackage(f, { bbp: 50, alert: true, badBudget: true, macroGrade: "보통", gate: false });
      var hasDep = routed.some(function(r){ return r.t.indexOf("외화보통예금") >= 0; });
      var hasLoan = routed.some(function(r){ return r.t.indexOf("외화대출") >= 0; });
      ok("외화예금 자격↔라우팅 " + pos + "/" + credit, hasDep === eligible("외화예금", f),
         hasDep ? "추천(외화 수취 건)" : denyReason("외화예금", f));
      ok("외화대출 자격↔라우팅 " + pos + "/" + credit, hasLoan === eligible("외화대출", f),
         hasLoan ? "검토(수입 결제자금·여신 O)" : denyReason("외화대출", f));
    });
  });
  /* 카탈로그와 라우팅이 같은 세그먼트 이름을 쓰는가(탭이 비면 상품이 사라진 것처럼 보인다) */
  ok("외화상품 세그 등록", SEG_ORDER.indexOf("외화상품") >= 0, SEG_ORDER.join("/"));
  /* ── 분할 결제 스케줄 ──
     분할선적은 무역 실무에서 흔한데 도구는 한 건을 단일 만기로만 봤다.
     핵심 불변식: 일시불이면 회차 1개라 기존 계산과 '정확히' 같아야 한다(엄밀한 일반화). */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  var _sigE = REG.USD[regimeIdx].sigAnn;   // 통화별 실측 σ̂ (구조상 배수가 사라졌다)
  ok("일시불 = 단일 회차", schedOf({ horizon: 63 }).length === 1
     && Math.abs(bbpOf({ budget: 1500, pos: "import", horizon: 63 }, MARKET.spot, _sigE, MARKET.iz)
                 - bbpProb(MARKET.spot, 1500, _sigE, 63, "import", MARKET.iz)) < 1e-12,
     "tranches 없으면 기존 bbpProb 와 동일");
  var _single = parseFloat($("bbp").textContent);
  ok("일시불 BBP 불변", _single === 64.3, "나래상사 " + _single + "%");

  $("in-split").value = "3"; renderSched(true); compute();
  var _sch = schedOf(readForm());
  ok("3회 분할 스케줄", _sch.length === 3
     && Math.abs(_sch.reduce(function(a, t){ return a + t.w; }, 0) - 1) < 1e-9
     && _sch[2].hbd === 63,
     _sch.map(function(t){ return Math.round(t.w * 100) + "%@" + t.hbd; }).join(" "));
  /* 화면 숫자가 금액가중 정의와 실제로 일치하는가(정의만 써놓고 다르게 계산하면 무의미) */
  var _f3 = readForm();
  var _manual = schedOf(_f3).reduce(function(a, t){
    return a + t.w * bbpProb(MARKET.spot, _f3.budget, _sigE, t.hbd, _f3.pos, MARKET.iz); }, 0) * 100;
  ok("합산 = 금액가중 평균", Math.abs(_manual - parseFloat($("bbp").textContent)) < 0.05,
     "수동 " + _manual.toFixed(1) + "% = 화면 " + $("bbp").textContent + "%");
  ok("회차별 분해 노출", $("tbreak").style.display !== "none"
     && $("tbreak").textContent.indexOf("금액가중") >= 0, "왜 그 값인지 펼쳐 보임");

  /* 만기↔BBP 방향은 현재 시세 위치가 가른다 — "만기 길수록 위험"은 절반만 맞다.
     이 사실이 틀리게 설명되면 카드가 거짓말이 된다(실제로 처음엔 그렇게 썼다). */
  var _imp = bbpProb(MARKET.spot, 1500, _sigE, 21, "import", MARKET.iz);
  var _imp63 = bbpProb(MARKET.spot, 1500, _sigE, 63, "import", MARKET.iz);
  var _exp = bbpProb(MARKET.spot, 1450, _sigE, 21, "export", MARKET.iz);
  var _exp63 = bbpProb(MARKET.spot, 1450, _sigE, 63, "export", MARKET.iz);
  ok("이미 불리 → 만기 길수록 BBP 하락", _imp63 < _imp,
     "나래상사 21일 " + (_imp * 100).toFixed(1) + "% > 63일 " + (_imp63 * 100).toFixed(1) + "%");
  ok("여유 구간 → 만기 길수록 BBP 상승", _exp63 > _exp,
     "한빛정밀 21일 " + (_exp * 100).toFixed(1) + "% < 63일 " + (_exp63 * 100).toFixed(1) + "%");
  ok("이미 불리 문구 = 짧은 회차", $("tbreak").textContent.indexOf("만기가 짧은 회차일수록") >= 0,
     "나래상사는 이미 불리 → 짧을수록 위험이라 말해야 함");
  /* ES 는 방향과 무관하게 만기가 길수록 커진다 → 확률과 금액이 다른 방향일 수 있다 */
  ok("ES 는 만기 길수록 증가",
     expShortfall(MARKET.spot, 1500, _sigE, 63, "import") > expShortfall(MARKET.spot, 1500, _sigE, 21, "import"),
     "확률과 반대 방향 가능");
  ok("확률·금액 방향 차이 고지", $("tbreak").textContent.indexOf("같은 방향으로 움직이지 않") >= 0, "-");

  /* 모순 입력이면 계산을 멈춘다 — 숫자를 지어내지 않는다 */
  var _sp = document.querySelectorAll("#sched-rows .sched-r:not(.fixed) .sp");
  _sp[0].value = "90"; _sp[1].value = "90"; compute();
  ok("비율 합계 100% 이상 차단", $("verdict").textContent.indexOf("입력을 확인해 주세요") >= 0
     && !/NaN|Infinity/.test($("bbp").textContent + $("hf-es").textContent), "BBP=" + $("bbp").textContent);
  renderSched(true);
  var _sh = document.querySelectorAll("#sched-rows .sched-r:not(.fixed) .sh");
  _sh[0].value = "50"; _sh[1].value = "20"; compute();
  ok("만기 역순 차단", $("verdict").textContent.indexOf("입력을 확인해 주세요") >= 0, "50→20 영업일");
  /* 프리셋을 바꾸면 직전 회사의 스케줄이 남으면 안 된다(남의 조건으로 계산된다) */
  renderSched(true); fillPreset(PRESETS[0]);
  ok("프리셋 전환 시 스케줄 초기화",
     $("in-split").value === "1" && $("tbreak").style.display === "none" && $("bbp").textContent === "16.5",
     "한빛정밀 BBP=" + $("bbp").textContent);
  fillPreset(PRESETS[3]);

  /* ── 환변동보험 = 수출 전용 ──
     K-SURE 환변동보험은 수출 거래의 환위험을 보장하는 제도인데, ELIG 가 credit 만 보고
     pos 를 안 봐서 수입 건에도 추천됐다. 게다가 환변동보험은 애초에 ELIG 에 연결돼 있지 않았고
     라우팅·트리아지·정책자금이 각자 f.credit==="no" 를 인라인으로 검사하고 있었다
     (ELIG 통합 때 놓친 네 번째 갈래). 네 화면이 같은 답을 내는지 조합으로 고정한다. */
  ["export", "import"].forEach(function(pos){
    ["no", "yes"].forEach(function(credit){
      var f = { pos: pos, credit: credit, biz: "corp", cert: "confirmed", cash: "ok", name: "테스트",
                party: "", currency: "USD", pay: "TT", hs: "ETC", budget: 1500, amount: 500000,
                horizon: 63, country: "미국", margin: 8 };
      var elig = eligible("환변동보험", f);
      ok("환변동보험 수출 전용 " + pos + "/" + credit, elig === (pos === "export" && credit === "no"),
         elig ? "자격 O" : denyReason("환변동보험", f));
      /* 라우팅: 자격 없으면 추천하지 않는다(제외 카드로 사유를 말하는 건 허용) */
      var eb = recommendPackage(f, { bbp: 50, alert: true, badBudget: true, macroGrade: "보통", gate: false })
        .find(function(r){ return r.t.indexOf("환변동보험") >= 0; });
      ok("환변동보험 자격↔라우팅 " + pos + "/" + credit,
         !eb || (["추천", "상담후보", "RM확인"].indexOf(eb.st) >= 0) === elig,
         eb ? eb.t + "[" + eb.st + "]" : "(카드 없음)");
      /* 헤지 트리아지: 자격 없는데 1순위로 권하면 안 된다 */
      ok("환변동보험 자격↔트리아지 " + pos + "/" + credit,
         !(triage(f)[0].indexOf("환변동보험") >= 0 && !elig), triage(f)[0]);
    });
  });
  /* 정책자금 화면도 같은 답 — pos:"both" 라 수입 건에 '참고 대상'으로 뜬 적이 있다.
     이 뷰는 showView 에서만 갱신되므로 프리셋 교체 후 반드시 다시 열어야 한다. */
  [[3, false], [2, true], [0, false]].forEach(function(pair){
    fillPreset(PRESETS[pair[0]]); showView("copilot"); showView("policy");
    var cards = [].slice.call(document.querySelectorAll("#pol-grid .prodc"));
    var c = cards.filter(function(x){
      var n = x.querySelector(".pc-n"); return n && n.textContent.indexOf("환변동보험") >= 0; })[0];
    var tagged = !!c && c.querySelector(".pc-cat").textContent.indexOf("참고 대상") >= 0;
    ok("정책자금 환변동보험 " + PRESETS[pair[0]].name, tagged === pair[1],
       (c ? c.querySelector(".pc-cat").textContent.trim() : "(카드 없음)"));
  });
  showView("copilot"); fillPreset(PRESETS[3]);

  /* ── 상품 카드 업무 깊이 ──
     KB 공식상품인데 자격요건·신청채널·RM 확인사항이 비어 있으면 브로슈어지 은행 업무가 아니다.
     한때 fit 이 19종 중 2종에만 있었고, 하필 대성무역 1순위(무역금융)가 가장 얕은 카드였다. */
  setMode("internal");
  var _all = {}, _kbThin = [], _splitFit = [];
  PRESETS.forEach(function(p){
    fillPreset(p);
    _recsOf(p, _deal(p)).forEach(function(r){
      _all[r.t] = r;
      if (r.src === "KB 공식상품" && !r.block
          && !(r.fit && r.fit.pass && r.fit.unknown && r.channels && r.rmChecks && r.kbPath)) _kbThin.push(r.t);
      /* 같은 사실을 두 곳에 적으면 반드시 갈라진다 → unknown 과 rmChecks 는 한 소스여야 한다 */
      if (r.fit && r.fit.unknown && r.rmChecks
          && r.fit.unknown.join("|") !== r.rmChecks.join("|")) _splitFit.push(r.t);
    });
  });
  ok("KB 공식상품 자격요건 완비", _kbThin.length === 0,
     _kbThin.length ? "누락: " + _kbThin.join(", ") : Object.keys(_all).length + "종 중 KB 공식상품 전부 fit·채널·RM확인·경로 보유");
  ok("fit.unknown = rmChecks 단일 소스", _splitFit.length === 0,
     _splitFit.length ? "갈라짐: " + _splitFit.join(", ") : "전부 일치");

  /* 충족 항목은 폼에서 파생돼야 한다 — 하드코딩하면 입력이 바뀔 때 카드가 거짓말한다 */
  var _lc = _recsOf(PRESETS[0], _deal(PRESETS[0])).find(function(r){ return r.t.indexOf("Nego") >= 0; });
  var _oaP = Object.assign({}, PRESETS[0], { pay: "OA" });
  var _oa = _recsOf(_oaP, _deal(_oaP)).find(function(r){ return r.t.indexOf("O/A") >= 0; });
  ok("자격 충족 = 폼 파생",
     _lc.fit.pass.join().indexOf("L/C") >= 0 && _oa.fit.pass.join().indexOf("O/A") >= 0,
     "결제방식만 바꿔도 충족 문구가 따라감");
  /* 가결제는 '충족'이 아니라 '확인 대상'이다 — 상태를 자격으로 세면 안 된다 */
  var _pv = Object.assign({}, PRESETS[0], { cert: "provisional" });
  var _pvr = _recsOf(_pv, _deal(_pv)).find(function(r){ return r.t.indexOf("Nego") >= 0; });
  ok("가결제는 충족 아님",
     _pvr.fit.pass.join().indexOf("가결제") < 0 && _pvr.fit.unknown.join().indexOf("가결제") >= 0,
     "충족에서 빠지고 미확인으로 올라감");
  /* 금리·수수료·요율·금액은 데모가 지어낼 수 없는 은행 내부값 → '충족'에 오면 안 된다.
     주의: "여신 한도 보유"는 폼에서 사용자가 고른 사실이므로 정상이다 —
     '한도'라는 낱말만으로 거르면 그게 오탐이 된다(실제로 한 번 걸렸다). */
  var _madeUp = [];
  Object.keys(_all).forEach(function(t){
    var r = _all[t];
    if (!r.fit || !r.fit.pass) return;
    if (r.fit.pass.some(function(s){ return /금리|수수료|요율/.test(s) || /\d+\s*(%|원|억)/.test(s); }))
      _madeUp.push(t);
  });
  ok("금리·수수료·요율은 충족 아님", _madeUp.length === 0,
     _madeUp.length ? "지어낸 값: " + _madeUp.join(", ") : "전부 RM 확인 대상으로 유지");

  /* '충족'은 카드 가드(또는 ELIG)가 보장하는 사실만이어야 한다.
     Payment Usance 는 여신을 안 보는데 credit 을 충족에 넣어 무여신 기업에게
     "무여신(보증 기반 대상)"을 자격으로 표시한 적이 있다 — 이 상품은 여신이 필요한데 정반대다. */
  var _ung = recommendPackage(
    { pos:"import", pay:"TT", cert:"confirmed", credit:"no", cash:"ok", biz:"corp",
      name:"테스트", party:"", currency:"USD", hs:"ETC", budget:1500, amount:500000, horizon:63, country:"미국", margin:8 },
    { bbp:50, alert:true, badBudget:true, macroGrade:"보통", gate:false });
  var _pu = _ung.find(function(r){ return r.t.indexOf("Payment Usance") >= 0; });
  ok("가드 안 하는 조건은 충족 아님",
     !_pu || !_pu.fit || _pu.fit.pass.join().indexOf("무여신") < 0,
     _pu && _pu.fit ? "충족: " + _pu.fit.pass.join(" · ") : "-");

  /* ── 예산환율 궤적: 실측인가, 예측을 얹지 않았는가 ──
     대시보드 추이 차트는 _series() 합성 랜덤워크다(=대표 추이(데모) 라벨). 이 궤적은 달라야 한다:
     fx_ewi_timeseries.csv 실측이고, 화면의 spot·σ 와 같은 출처의 같은 파일에서 나온다. */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  ok("궤적 = 실측 데이터", typeof HIST_USD === "object" && HIST_USD.close.length > 200
     && HIST_USD.close[HIST_USD.close.length - 1] === MARKET.spot,
     HIST_USD.n + "영업일 · 마지막 " + HIST_USD.close[HIST_USD.close.length - 1] + " = spot " + MARKET.spot);
  ok("궤적 기준일 = 시세 기준일", HIST_USD.to === MARKET.date, HIST_USD.from + "~" + HIST_USD.to);
  /* 과거 사실만 — 미래 구간·예측선을 그리면 무예측 원칙(F 문서)이 깨진다 */
  var _tsvg = $("track-svg").innerHTML;
  ok("궤적에 예측선 없음", /polyline/.test(_tsvg)
     && !/예상|forecast|predict|dashed-future/i.test(_tsvg), "실측 꺾은선 + 예산환율 기준선만");
  ok("궤적 무예측 고지", $("track-note").textContent.indexOf("예측하지 않습니다") >= 0, "-");
  ok("궤적 스크린리더 대안", ($("track-alt").textContent || "").indexOf("영업일") >= 0,
     "차트는 시각 정보 → 텍스트 대안 필수");
  /* 불리했던 날 수가 실제로 K·방향에 따라 달라지는가(상수를 박아두지 않았는가) */
  var _pcts = PRESETS.map(function(p){
    fillPreset(p);
    var m = ($("track-note").textContent || "").match(/(\d+)일\((\d+)%\)/);
    return m ? m[2] : "?";
  });
  ok("궤적 빈도 = 회사별 계산", new Set(_pcts).size >= 4, "프리셋별 불리한 날 % = " + _pcts.join(" / "));
  /* 궤적 카드는 '실측 시계열이 있는 통화'에만 그린다.
     예전엔 USD 만 있어서 "비USD 는 숨김"이 규칙이었다. 2026-07-17 에 EUR/JPY 12개월 실적을
     같은 소스로 실었으므로 이제 엔진 통화면 그린다. 엔진에 없는 통화는 여전히 숨긴다 —
     불변식은 "USD 냐"가 아니라 "그 통화의 실측이 있느냐"다. */
  fillPreset(PRESETS[0]); $("in-cur").value = "EUR"; compute();
  ok("엔진 통화 궤적 표시", document.querySelector(".trackchart").style.display !== "none"
     && ($("track-note").textContent || "").indexOf("영업일") > 0,
     "EUR 도 12개월 실측 궤적을 그린다");
  var _eurNote = $("track-note").textContent;
  $("in-cur").value = "CNY"; compute();
  ok("실측 없는 통화 궤적 숨김", document.querySelector(".trackchart").style.display === "none",
     "CNY 는 HIST 가 없다 → 없는 시계열을 그리지 않음");
  $("in-cur").value = "USD"; compute();
  ok("궤적은 통화마다 다름", $("track-note").textContent !== _eurNote,
     "USD 와 EUR 의 불리 빈도가 같을 수 없다");
  $("in-cur").value = "USD"; fillPreset(PRESETS[3]);

  /* ── 타이포그래피 ──
     예전 스케일은 12/13/14/15/17/19 로 본문 구간이 1px 계단이었다. 1px 차이는 눈이 구분하지
     못해 위계가 생기지 않는다 — 실측상 전체 텍스트의 92%가 12~13px 로 사실상 한 크기였다. */
  /* 토큰이 rem 이라 parseFloat 로 읽으면 0.75 가 나온다 — 실제 px 로 환산해서 본다.
     (rem 인 이유: px 는 브라우저 '기본 글꼴 크기' 설정을 무시한다 — 타깃이 50~60대 사장님이다) */
  var _probe = document.createElement("span");
  _probe.style.cssText = "position:absolute;visibility:hidden";
  document.body.appendChild(_probe);
  var _fs = function(n){
    _probe.style.fontSize = "var(--fs-" + n + ")";
    return parseFloat(getComputedStyle(_probe).fontSize);
  };
  var _scale = [1, 2, 3, 4, 5, 6].map(_fs);
  _probe.remove();   /* 이 뒤로는 _fs() 대신 _scale[] 을 쓴다 */
  ok("타입 스케일 rem 단위",
     /rem/.test(getComputedStyle(document.documentElement).getPropertyValue("--fs-1")),
     "브라우저 글꼴 설정을 따라간다 · " + getComputedStyle(document.documentElement).getPropertyValue("--fs-1").trim());
  ok("타입 스케일 단조 증가", _scale.every(function(v, i){ return i === 0 || v > _scale[i - 1]; }),
     _scale.join(" / ") + "px (16px 기준)");
  ok("본문·캡션이 갈린다", _scale[1] - _scale[0] >= 2,
     "캡션 " + _scale[0] + "px vs 본문 " + _scale[1] + "px — 1px 계단이면 위계가 없다");

  /* 정직 고지가 깨알글씨면 그건 fine print 다.
     청사진 결정 #2 는 "심사 탭에만 있으면 알리바이 → 숫자 옆에서 고지"였는데,
     숫자 옆에 두고 화면 최소 크기·흐린 색으로 쓰면 같은 알리바이의 다른 형태다. */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  var _hn = document.querySelector("#bbp-honesty .hn");
  ok("정직 고지 = 본문 크기", !!_hn && parseFloat(getComputedStyle(_hn).fontSize) >= _scale[1],
     _hn ? getComputedStyle(_hn).fontSize + " (캡션 " + _scale[0] + "px 아님)" : "-");
  ok("정직 고지 = 본문 색", !!_hn
     && getComputedStyle(_hn).color === getComputedStyle(document.body).color,
     _hn ? "흐린 색 아님" : "-");

  /* 한글 가독 한계는 40~50자/줄. 컨테이너가 1111px 이라 12px 문단이 93자/줄까지 늘어났었다. */
  var _cv = document.createElement("canvas"), _cx = _cv.getContext("2d");
  var _wide = [];
  /* 심화 탭은 display:none 이라 활성 패널(dp-a)만 검사됐다 → showDeep 도 순회한다 */
  function _scanWide(host, tag){
    if (!host) return;
    [].slice.call(host.querySelectorAll("p,div,span,li")).forEach(function(e){
        if (!_vis(e) || e.classList.contains("sr-only")) return;
        var own = [].slice.call(e.childNodes).filter(function(n){ return n.nodeType === 3 && n.nodeValue.trim(); });
        if (!own.length) return;
        var txt = e.textContent.replace(/\s+/g, " ").trim();
        if (txt.length < 60) return;
        var cs = getComputedStyle(e), fs = parseFloat(cs.fontSize), r = e.getBoundingClientRect();
        _cx.font = fs + "px " + cs.fontFamily;
        var per = r.width / _cx.measureText("가").width;
        if (per > 70) _wide.push(tag + " " + Math.round(per) + "자 :: " + txt.slice(0, 26));
    });
  }
  ["customer", "internal"].forEach(function(md){
    setMode(md);
    ["copilot", "dashboard", "trades", "products", "policy", "fx", "report", "support"].forEach(function(v){
      showView(v);
      _scanWide(document.getElementById("view-" + v), md + "/" + v);
    });
    showView("copilot");
    if (md === "internal")
      ["dp-a", "dp-c", "dp-e", "dp-judge"].forEach(function(dp){
        showDeep(dp); _scanWide(document.getElementById(dp), md + "/" + dp); });
    showDeep("dp-a");
  });
  setMode("internal"); showView("copilot");
  ok("문단 줄 길이 제한", _wide.length === 0,
     _wide.length ? _wide.slice(0, 3).join(" | ") : "70자/줄 초과 없음");

  /* 전 시트의 CSS 규칙 — ⚠ styleSheets[0] 를 쓰면 안 된다:
     폰트 @font-face 를 문서 맨 앞에 넣은 뒤로 0번은 폰트 시트라 본 스타일을 못 읽고
     규칙 0개 → 그 위에 세운 검사가 통째로 무의미하게 통과한다(실제로 고도 검사가 그랬다). */
  var _allRules = [].slice.call(document.styleSheets).reduce(function(a, s){
    try { return a.concat([].slice.call(s.cssRules)); } catch(e){ return a; } }, []);
  ok("스타일 규칙 로드", _allRules.length > 200, _allRules.length + "개 — 시트 0번만 읽으면 0개다");

  /* 뷰 키는 showView 가 받는 이름 — 외환/시세는 "rates" 가 아니라 "fx"(컨테이너 view-fx).
     틀리면 showView 가 조용히 view-copilot 으로 폴백해 그 화면을 영영 검사하지 않는다. */
  var VIEWS = ["copilot", "dashboard", "trades", "products", "policy", "fx", "report", "support"];

  /* ── 실렌더 대비 (토큰 대비로는 못 잡는 것) ──
     기존 대비 검사는 **토큰을 --panel(흰색) 위에서만** 쟀다. 그런데 실제 텍스트는
     --panel-2·--bg·의미색 틴트 위에 얹힌다 → 배경이 어두워져 대비가 깎인다.
     실측으로 34+22건이 나왔다: 의미색 칩이 자기 색 15~18% 틴트 위(3.8~4.3:1),
     다크 --ink-faint 가 --panel-2 위(4.15:1), .bn 배지가 밝은 토큰 배경에 흰 글자(2.34:1).
     ⚠ 반투명 배경은 알파를 합성해야 실제 색이 나온다 — 원색으로 계산하면 엉터리다. */
  /* ⚠ Chrome 은 color-mix() 를 color(srgb 0.98 0.95 0.95 / .5) 로 **0~1 실수** 반환한다.
     rgb(0-255) 로 알고 /255 하면 거의 검정이 되어 1.33:1 같은 유령 실패가 나온다. */
  function _rgba(c){ c = (c || "").trim(); if (!c) return null;
    var s = /^color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.%]+))?\s*\)$/i.exec(c);
    if (s){ var a = s[4] == null ? 1 : (/%$/.test(s[4]) ? parseFloat(s[4]) / 100 : +s[4]);
      return [+s[1] * 255, +s[2] * 255, +s[3] * 255, a]; }
    var m = c.match(/[\d.]+/g); if (!m) return null;
    return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1]; }
  function _over(fg, bg){ var a = fg[3];
    return [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a), fg[2]*a + bg[2]*(1-a), 1]; }
  function _lum(c){ var f = c.slice(0,3).map(function(v){ v = v/255;
    return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*f[0] + 0.7152*f[1] + 0.0722*f[2]; }
  function _bgOf(e){
    var stack = [];
    for (var q = e; q; q = q.parentElement){ var c = _rgba(getComputedStyle(q).backgroundColor);
      if (c && c[3] > 0){ stack.push(c); if (c[3] >= 1) break; } }
    var base = _rgba(getComputedStyle(document.documentElement).backgroundColor);
    if (!base || base[3] < 1) base = [255,255,255,1];
    var acc = (stack.length && stack[stack.length-1][3] >= 1) ? stack.pop() : base;
    while (stack.length) acc = _over(stack.pop(), acc);
    return acc;
  }
  function _ratio(fgc, bg){ var fg = _rgba(fgc); if (!fg) return null;
    if (fg[3] < 1) fg = _over(fg, bg);
    var a = _lum(fg), b = _lum(bg);
    return (Math.max(a,b) + 0.05) / (Math.min(a,b) + 0.05);
  }
  var _saveTh = document.documentElement.getAttribute("data-theme");
  var _lowC = [];
  ["light", "dark"].forEach(function(th){
    document.documentElement.setAttribute("data-theme", th);
    setMode("internal");
    VIEWS.forEach(function(v){ showView(v); });
    showView("copilot");
    ["dp-a", "dp-c", "dp-e", "dp-judge"].forEach(function(dp){
      showDeep(dp);
      [].slice.call(document.querySelectorAll("#view-copilot *, .appbar *, .sidenav *")).forEach(function(e){
        if (!_vis(e) || e.classList.contains("sr-only")) return;
        var own = [].slice.call(e.childNodes).filter(function(n){ return n.nodeType === 3 && n.nodeValue.trim(); });
        if (!own.length) return;
        var cs = getComputedStyle(e), fs = parseFloat(cs.fontSize), bold = +(cs.fontWeight) >= 700;
        var need = (fs >= 18 || (fs >= 14 && bold)) ? 3.0 : 4.5;
        var r = _ratio(cs.color, _bgOf(e));
        if (r !== null && r < need - 0.05)
          _lowC.push(th + "/" + dp + " " + r.toFixed(2) + ":1(요구 " + need + ") <"
            + e.tagName + "." + ((e.className || "").toString().split(" ")[0] || "-") + ">");
      });
    });
    showDeep("dp-a");
  });
  document.documentElement.setAttribute("data-theme", _saveTh || "light");
  ok("실렌더 대비 (양 테마·심화 탭 포함)", _lowC.length === 0,
     _lowC.length ? _lowC.slice(0, 4).join(" | ") : "미달 0건 — 틴트·패널2·bg 위 전부 4.5:1 이상");

  /* ── 소형 위젯(알림·검색·필터) ──
     본화면·심화탭을 다 훑고 나서야 본 곳인데 흠이 몰려 있었다:
     알림 항목이 <div> 라 **키보드로 아예 못 눌렀고**, 벨에 aria-expanded 가 없어 열림/닫힘을
     스크린리더가 몰랐고, Escape 로 못 닫아 키보드 사용자는 '바깥 클릭'만 남았다(키보드로 불가).
     검색창은 placeholder 만 있고 접근 이름이 없었으며, 결과가 바뀌어도 낭독되지 않았다. */
  setMode("internal"); showView("copilot");
  var _bell = document.getElementById("ab-bell"), _np = document.getElementById("notif-panel");
  ok("알림 벨 팝오버 시맨틱",
     !!_bell && _bell.getAttribute("aria-haspopup") === "true" && _bell.hasAttribute("aria-expanded"),
     _bell ? "haspopup=" + _bell.getAttribute("aria-haspopup") + " expanded=" + _bell.getAttribute("aria-expanded") : "-");
  ok("알림 패널 이름", !!_np && _np.getAttribute("role") === "dialog" && !!_np.getAttribute("aria-label"),
     _np ? "role=" + _np.getAttribute("role") + " label=" + _np.getAttribute("aria-label") : "-");
  _bell.click();
  ok("알림 열면 aria-expanded=true", _bell.getAttribute("aria-expanded") === "true", "-");
  var _ni = _np.querySelector(".notif-i");
  ok("알림 항목이 키보드로 눌린다", !!_ni && _ni.tagName === "BUTTON",
     _ni ? "<" + _ni.tagName + "> (div 면 Tab·Enter 가 안 먹는다)" : "항목 없음");
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  ok("알림 Escape 닫힘", !_np.classList.contains("on") && _bell.getAttribute("aria-expanded") === "false",
     "키보드 사용자는 '바깥 클릭'을 할 수 없다");

  /* 검색창 — placeholder 는 접근 이름이 아니다(입력 시 사라진다) */
  var _noName = [];
  ["products", "trades"].forEach(function(v){
    showView(v);
    [].slice.call(document.querySelectorAll("#view-" + v + " .searchbox")).forEach(function(s){
      var lbl = s.id && document.querySelector('label[for="' + s.id + '"]');
      if (!s.getAttribute("aria-label") && !lbl) _noName.push(v + "/" + (s.id || "-"));
    });
  });
  ok("검색창 접근 이름", _noName.length === 0, _noName.length ? _noName.join(", ") : "aria-label 보유");
  /* 검색 결과가 바뀌면 스크린리더가 알아야 한다 */
  showView("products");
  ok("검색 결과 aria-live", (document.getElementById("prod-grid") || {}).getAttribute
     && document.getElementById("prod-grid").getAttribute("aria-live") === "polite", "결과 변화 낭독");

  /* 필터 칩은 토글 버튼 — .on 클래스는 화면을 보는 사람만 안다 */
  var _noPressed = [];
  ["products", "trades", "policy"].forEach(function(v){
    showView(v);
    [].slice.call(document.querySelectorAll("#view-" + v + " .fchip")).forEach(function(c){
      if (!c.hasAttribute("aria-pressed")) _noPressed.push(v + ":" + c.textContent.trim().slice(0, 8));
    });
  });
  showView("copilot");
  ok("필터 칩 선택 상태 노출", _noPressed.length === 0,
     _noPressed.length ? _noPressed.slice(0, 4).join(", ") : "aria-pressed 보유");

  /* ── 심사 자료 점프 네비 ──
     dp-judge 는 6,400px(7화면)에 대섹션 4개다. 청사진에 '§12 점프네비'가 기록돼 있었는데
     정리 패스 중 사라져 스크롤로만 훑어야 했다. */
  showDeep("dp-judge");
  var _jn = document.querySelector(".jumpnav");
  ok("심사 자료 점프 네비", !!_jn && _jn.querySelectorAll("a").length >= 4 && !!_jn.getAttribute("aria-label"),
     _jn ? _jn.querySelectorAll("a").length + "개 · " + _jn.getAttribute("aria-label") : "없음");
  ok("점프 대상 전부 존재",
     !!_jn && [].slice.call(_jn.querySelectorAll("a")).every(function(a){
       return document.querySelector(a.getAttribute("href")); }),
     _jn ? [].slice.call(_jn.querySelectorAll("a")).map(function(a){ return a.getAttribute("href"); }).join(" ") : "-");
  showDeep("dp-a");

  /* ── 모달(RM 인증) ──
     포커스 트랩 자체는 잘 돼 있었다(Tab 8번·Shift+Tab 모두 모달 안, Escape 로 닫힘 — CDP 실측).
     그런데 **닫을 때 초점이 <body> 로 떨어졌다**(WAI-ARIA APG 위반) — RM 화면을 열려다 취소하면
     키보드 사용자가 페이지 맨 위로 내던져져 네비 8개를 다시 통과해야 했다. */
  var _dlg = document.getElementById("rm-auth");
  ok("모달 dialog 시맨틱",
     !!_dlg && _dlg.getAttribute("role") === "dialog" && _dlg.getAttribute("aria-modal") === "true"
     && !!_dlg.getAttribute("aria-label"),
     _dlg ? "role=dialog aria-modal aria-label=" + _dlg.getAttribute("aria-label") : "-");
  setMode("customer");
  $("modebtn").focus();
  openRmAuth();
  ok("모달이 연 요소를 기억", _rmAuthOpener === $("modebtn"),
     "opener=" + ((_rmAuthOpener && _rmAuthOpener.id) || "-"));
  closeRmAuth();
  ok("모달 닫으면 초점 복귀", document.activeElement === $("modebtn"),
     "→ " + (document.activeElement.id || document.activeElement.tagName) + " (body 로 떨구면 안 된다)");
  /* ── 인증 성공 = '이동'이다(2026-07-17) ──
     예전엔 setMode 만 하고 제자리에 뒀다. RM 전용 영역(심화 탭)은 화면 2,400px 아래라
     뷰포트에서는 아무 일도 안 일어난 것처럼 보였고, 사용자는 "안 됐네" 하고 모드 버튼을 또 눌렀다 —
     그 버튼은 이미 '고객 화면 보기'로 바뀐 뒤라 오히려 고객 모드로 되돌아갔다(세 번 눌러야 들어가졌다). */
  setMode("customer"); _rmAuthed = false; window.scrollTo(0, 0);
  $("modebtn").click();
  ok("미인증 RM 진입 → 모달", $("rm-auth").classList.contains("on")
     && document.documentElement.getAttribute("data-mode") === "customer", "인증 전엔 모드 그대로");
  $("rm-pass").value = "1234"; $("rm-auth-ok").click();
  ok("인증 즉시 모드 전환", document.documentElement.getAttribute("data-mode") === "internal",
     "한 번의 passcode 로 들어간다");
  ok("인증 즉시 RM 탭 활성", (document.querySelector(".deeppane.active") || {}).id === "dp-a",
     "① 결과·근거 로 착지");
  var _dtr = document.querySelector(".deeptabs").getBoundingClientRect();
  ok("인증 후 RM 화면이 보인다", _dtr.top >= -10 && _dtr.top < window.innerHeight,
     "심화탭 top=" + Math.round(_dtr.top) + " (뷰포트 밖이면 아무 일도 안 일어난 것처럼 보인다)");
  ok("인증 후 초점 = 도착지", (document.activeElement.getAttribute("data-dp") || "") === "dp-a",
     "인증은 취소와 달리 '이동'이라 연 버튼으로 되돌리면 안 된다");
  setMode("customer"); _rmAuthed = false; window.scrollTo(0, 0);

  /* 연 요소가 DOM 에서 사라진 경우에도 body 로 떨구지 않는다 */
  openRmAuth();
  _rmAuthOpener = document.createElement("button");
  closeRmAuth();
  ok("연 요소 소실 시 대체 초점", document.activeElement !== document.body,
     "→ " + (document.activeElement.id || document.activeElement.tagName));
  setMode("internal");

  /* ── 토스트 = 스크린리더에도 들리는가 ──
     토스트는 이 앱의 주요 피드백 수단인데(서류 채움·시세 조회 실패·제재 재조회)
     aria-live 도 role 도 없어 스크린리더 사용자는 아무것도 듣지 못했다.
     ⚠ position:fixed 라 offsetParent 가 null → 여태 모든 가시성 검사에서 통째로 빠져 있었다.
     그래서 이 흠은 가시성 판정을 checkVisibility() 로 고치고 나서야 드러났다. */
  toast("테스트 알림");
  var _toast = document.querySelector(".toast");
  ok("토스트 존재", !!_toast, _toast ? _toast.textContent : "-");
  ok("토스트 스크린리더 낭독",
     !!_toast && (_toast.getAttribute("role") === "status" || /polite|assertive/.test(_toast.getAttribute("aria-live") || "")),
     _toast ? "role=" + _toast.getAttribute("role") + " live=" + _toast.getAttribute("aria-live") : "-");
  /* offsetParent 로는 안 보이지만 실제로는 보인다 — 이 사실 자체를 고정해 둔다 */
  ok("토스트는 offsetParent 로 못 잡는다",
     !!_toast && _toast.offsetParent === null && _vis(_toast),
     "position:fixed — 가시성 판정에 offsetParent 를 쓰면 안 되는 이유");
  if (_toast) _toast.classList.remove("on");

  /* ── 포커스 가시성 ──
     전역 :focus-visible 링(2px)이 있었는데도 폼 입력엔 안 보였다 —
     `.f input:focus{outline:none}`(특정성 0,2,1)이 `input:focus-visible`(0,1,1)을 이겨 지웠다.
     마우스 클릭 때 링을 안 띄우는 건 :focus-visible 이 이미 해준다 → :focus 로 지울 이유가 없다. */
  var _killRing = _allRules.filter(function(r){
    return r.selectorText && /:focus(?!-visible)/.test(r.selectorText)
      && /outline\s*:\s*none/.test(r.style.outline || r.cssText || "");
  });
  ok("포커스 링을 지우는 규칙 없음", _killRing.length === 0,
     _killRing.length ? _killRing.map(function(r){ return r.selectorText; }).join(" | ") : "outline:none 없음");
  ok("전역 포커스 링 존재",
     _allRules.some(function(r){ return r.selectorText && /input:focus-visible/.test(r.selectorText)
       && /2px/.test(r.style.outline || r.cssText || ""); }), "2px 링");

  /* ── 큰 금액 표기 ──
     won() 에 조 단위가 없어 45조가 "458236.16억원"으로 나왔다.
     in-amount 상한이 1e12 라 **유효한 입력이 읽을 수 없는 숫자**를 만든 것이다. */
  ok("조 단위 표기", won(4.58e13).indexOf("조원") > 0, "45.8조 → " + won(4.58e13));
  ok("억 단위 천단위 구분", won(1.23456e11).indexOf(",") > 0, "1,234억 → " + won(1.23456e11));
  ok("만원 단위 유지", won(1.8e7).indexOf("만원") > 0, "1,800만 → " + won(1.8e7));
  /* 입력 상한(1e12 외화)에서도 읽히는가 — 실제로 넣어 본다 */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  $("in-amount").value = "999999999999"; compute();
  ok("입력 상한 금액 표기 가능", !/\d{5,}\./.test($("hf-es").textContent),
     "부담액 " + $("hf-es").textContent);
  fillPreset(PRESETS[3]);

  /* ── 키보드·인쇄 ──
     키보드 사용자는 매 화면마다 앱바 4개 + 네비 8개를 지나야 본문에 닿았다(WCAG 2.4.1). */
  var _skip = document.querySelector("a.skip");
  ok("스킵 링크 존재", !!_skip && _skip.getAttribute("href") === "#view-copilot",
     _skip ? _skip.textContent.trim() + " → " + _skip.getAttribute("href") : "없음");
  ok("스킵 링크 대상 존재", !!_skip && !!document.querySelector(_skip.getAttribute("href")), "-");
  /* 평소엔 화면 밖 — 항상 보이면 그것대로 소음이다 */
  ok("스킵 링크 평소 숨김", !!_skip && _skip.getBoundingClientRect().bottom < 0,
     _skip ? "top=" + Math.round(_skip.getBoundingClientRect().top) + "px" : "-");
  ok("<main> 랜드마크", !!document.querySelector("main"), "스크린리더 본문 건너뛰기 대상");

  /* 인쇄 — 심사위원이 결과를 종이로 남길 수 있다. 화면 크롬은 종이에서 의미가 없다. */
  var _print = _allRules.filter(function(r){ return r.media && /print/.test(r.media.mediaText || ""); });
  ok("인쇄 스타일 존재", _print.length > 0, _print.length + "개 @media print");
  ok("인쇄 시 카드 분리 방지",
     _print.some(function(m){ return /break-inside\s*:\s*avoid/.test(m.cssText || ""); }),
     "카드가 페이지 중간에서 잘리면 못 읽는다");

  /* ── 본문 서체 ──
     시각 품질의 천장이 시스템 기본(Windows 한국어 = Malgun Gothic)이었다.
     단일 파일·무의존 배포라 웹폰트를 못 쓰는 게 아니라 base64 로 파일 안에 넣으면 된다. */
  ok("본문 서체 = 임베드 폰트",
     getComputedStyle(document.body).fontFamily.indexOf("KBFXSans") === 0,
     getComputedStyle(document.body).fontFamily.slice(0, 46));
  /* data: URI 여야 오프라인(file://)에서 뜬다 — 외부 URL 이면 파일 배포가 깨진다.
     주의: Chrome 은 src 를 url("data:…) 로 따옴표를 붙여 노출한다 → 따옴표를 허용해야 한다. */
  var _face = [].slice.call(document.styleSheets)
    .reduce(function(a, s){ try { return a.concat([].slice.call(s.cssRules)); } catch(e){ return a; } }, [])
    .filter(function(r){ return r.type === CSSRule.FONT_FACE_RULE; });
  ok("폰트는 data: URI", _face.length > 0
     && _face.every(function(r){ return /url\(["']?data:/.test(r.style.src || r.cssText); }),
     _face.length + "개 @font-face · 외부 URL 없음");
  /* 가변축이 있어야 400~900 을 한 파일로 덮는다(이 화면은 6가지 굵기를 쓴다) */
  ok("가변 굵기축", _face.some(function(r){ return /\d+\s+\d+/.test(r.style.fontWeight || ""); }),
     _face.length ? "font-weight: " + _face[0].style.fontWeight : "-");

  /* ── 서체 라이선스(SIL OFL 1.1) ──
     조건 2: 저작권 고지와 라이선스를 사본에 포함해야 한다(human-readable header 허용).
     조건 3: 서브셋은 Modified Version 이므로 Reserved Font Name 'Pretendard' 를 쓸 수 없다.
     이건 취향이 아니라 배포 적법성 문제다 — 빠지면 라이선스 위반이다. */
  var _src = document.documentElement.outerHTML;
  ok("OFL 저작권 고지 포함", _src.indexOf("Kil Hyung-jin") >= 0 && _src.indexOf("SIL Open Font License") >= 0,
     "OFL 조건 2 — 사본에 고지·라이선스 포함");
  ok("Reserved Font Name 미사용",
     !_face.some(function(r){ return /Pretendard/i.test(r.style.fontFamily || ""); }),
     "OFL 조건 3 — 수정본(서브셋)에 'Pretendard' 이름 금지 → " + (_face[0] || {style:{}}).style.fontFamily);

  /* ── 고도(elevation) ──
     예전엔 그림자가 실질 1종(`0 1px 2px rgba(23,35,59,.05)`×6)이라 화면이 평면이었고,
     베이스 색도 3가지로 제각각이었으며, **다크 모드에 재정의가 없어 고도가 통째로 사라졌다**
     (잉크색 그림자는 #0f1622 배경에서 보이지 않는다). */
  var _root = document.documentElement, _saveTheme = _root.getAttribute("data-theme");
  function _shadows(){
    return [1, 2, 3, 4].map(function(n){
      return getComputedStyle(_root).getPropertyValue("--sh-" + n).trim();
    });
  }
  _root.setAttribute("data-theme", "light");
  var _shL = _shadows();
  ok("고도 토큰 4단계", _shL.every(function(v){ return v.length > 0; }), _shL.length + "단계 정의");
  /* 블러 반경이 단계마다 커져야 '더 떠 있다'가 읽힌다 */
  var _blur = _shL.map(function(v){ var m = v.match(/(\d+)px\s+(\d+)px/); return m ? +m[2] : 0; });
  ok("고도 단계별 블러 증가", _blur.every(function(v, i){ return i === 0 || v >= _blur[i - 1]; }), _blur.join(" → ") + "px");
  /* 2겹(접촉+주변광)이어야 스티커처럼 안 보인다 — sh-2 이상은 반드시 */
  ok("고도 2겹 구성", _shL.slice(1).every(function(v){ return v.split(/\),/).length >= 2; }),
     "--sh-2~4 각각 " + _shL.slice(1).map(function(v){ return v.split(/\),/).length + "겹"; }).join(" "));

  _root.setAttribute("data-theme", "dark");
  var _shD = _shadows();
  ok("다크 모드 고도 재정의", _shD.every(function(v, i){ return v !== _shL[i]; }),
     "라이트와 다른 그림자 — 잉크색은 어두운 배경에서 안 보인다");
  _root.setAttribute("data-theme", _saveTheme || "light");

  /* 고도는 토큰으로만 — 인셋 링(.seg-tab.on 등)·포커스 링은 고도가 아니라 표시자다 */
  var _hard = [];
  _allRules.forEach(function(r){
    if (!r.style || !r.style.boxShadow) return;
    var v = r.style.boxShadow;
    if (v.indexOf("var(--sh-") >= 0 || v.indexOf("inset") >= 0) return;
    if (/^0(px)? 0(px)? 0/.test(v.trim())) return;          /* 링 */
    _hard.push((r.selectorText || "?").slice(0, 40) + " :: " + v.slice(0, 40));
  });
  ok("고도 그림자는 토큰만", _hard.length === 0,
     _hard.length ? "하드코딩: " + _hard.join(" | ") : "인셋 링·포커스 링 외 하드코딩 없음");

  /* ── 모션 ──
     이 테스트는 --force-prefers-reduced-motion 으로 돈다(BBP 게이지 animNum 을 읽어야 하므로).
     그래서 여기서 검사할 수 있는 건 "모션을 원치 않는 사용자에게 전부 꺼지는가"다.
     예전엔 transition 만 껐고 animation 은 .skel 하나만 꺼서 .ocr-filled 플래시가 그대로 돌았다. */
  ok("reduced-motion 감지", matchMedia("(prefers-reduced-motion:reduce)").matches, "테스트 전제");
  var _motionOn = [];
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  $("in-amount").classList.add("ocr-filled");   /* 애니메이션 요소를 일부러 만든다 */
  [".go", ".nav-i", ".prodc", ".view.active", ".seg-pane.on", ".arc", ".ocr-filled", ".skel i"]
    .forEach(function(sel){
      var e = document.querySelector(sel); if (!e) return;
      var cs = getComputedStyle(e);
      var dur = function(s){ return (s || "").split(",").some(function(x){ return parseFloat(x) > 0; }); };
      if (dur(cs.transitionDuration) || (cs.animationName !== "none" && dur(cs.animationDuration)))
        _motionOn.push(sel + "(t=" + cs.transitionDuration + " a=" + cs.animationName + ")");
    });
  $("in-amount").classList.remove("ocr-filled");
  ok("reduced-motion 이면 전부 정지", _motionOn.length === 0,
     _motionOn.length ? "살아있음: " + _motionOn.join(" | ") : "transition·animation 모두 0s");

  /* 모션 토큰이 있고 은행 화면에 맞게 빠른가 — 느린 모션은 고장으로 읽힌다 */
  var _d = [1, 2, 3].map(function(n){
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--dur-" + n));
  });
  ok("모션 토큰 단조 증가", _d.every(function(v, i){ return v > 0 && (i === 0 || v > _d[i - 1]); }), _d.join(" / ") + "ms");
  ok("모션 지속시간 상한", _d[2] <= 400, "뷰 진입 " + _d[2] + "ms (은행 화면은 400ms 이하)");

  /* ── 고대비 모드 대응 ──
     이 화면은 위험도를 색(--good/--warn/--crit)과 color-mix 반투명 배경으로 전달한다.
     Windows 고대비 모드는 그 색을 시스템 색으로 통째로 덮어써서 구분이 사라진다.
     테두리로 형태를 되살리는 규칙이 실제로 스타일시트에 있는지 확인한다. */
  var _fc = 0, _pc = 0;
  for (var _i = 0; _i < document.styleSheets.length; _i++) {
    var _rules;
    try { _rules = document.styleSheets[_i].cssRules; } catch (e) { continue; }
    for (var _j = 0; _j < _rules.length; _j++) {
      var _t = _rules[_j].conditionText
        || (_rules[_j].media && _rules[_j].media.mediaText) || "";
      if (/forced-colors/.test(_t)) _fc++;
      if (/prefers-contrast/.test(_t)) _pc++;
    }
  }
  ok("forced-colors 대응", _fc > 0, "미디어쿼리 " + _fc + "블록");
  ok("prefers-contrast 대응", _pc > 0, "미디어쿼리 " + _pc + "블록");

  /* ── 공간 그리드(4pt) ──
     예전엔 간격 토큰이 0개였고 padding 20종·gap 16종·margin 18종이 뒤섞여
     573회 중 338회(59%)가 그리드 밖이었다(9/10/11px 공존). 개별로는 안 보이지만 누적되면
     아무것도 정렬되지 않는다 — 옛 타입스케일의 1px 계단과 같은 병이다.
     0~3px 는 헤어라인·광학 보정이라 그리드에서 제외한다. */
  var _sp = [1, 2, 3, 4, 5, 6, 7, 8].map(function(n){
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sp-" + n));
  });
  ok("공간 토큰 = 4의 배수", _sp.every(function(v){ return v > 0 && v % 4 === 0; }), _sp.join(" / ") + "px");
  ok("공간 토큰 단조 증가", _sp.every(function(v, i){ return i === 0 || v > _sp[i - 1]; }), "-");
  /* 반경: 예전엔 --r-lg 와 --radius 가 둘 다 14px 로 같은 값에 두 이름이었다 */
  var _rl = getComputedStyle(document.documentElement).getPropertyValue("--r-lg").trim();
  var _rd = getComputedStyle(document.documentElement).getPropertyValue("--radius").trim();
  ok("--radius 는 --r-lg 를 참조", _rd === _rl, "--radius=" + _rd + " · --r-lg=" + _rl);

  /* 3컬럼 카드 상단이 맞는가 — 설명문이 1줄/2줄로 갈리면 첫 카드가 17px 어긋난다.
     대시보드에서 카드 top 이 안 맞는 건 눈에 바로 걸리는 종류의 흠이다. */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]);
  var _cols = [].slice.call(document.querySelectorAll("#view-copilot .appcol"));
  if (_cols.length === 3) {   /* 1열로 무너진 뷰포트에서는 의미 없는 검사 */
    var _tops = _cols.map(function(c){ return Math.round(c.querySelector(".card").getBoundingClientRect().top); });
    ok("3컬럼 첫 카드 상단 정렬", Math.max.apply(null, _tops) - Math.min.apply(null, _tops) === 0,
       _tops.join(" / ") + "px");
  }
  setMode("internal");

  /* ── 3컬럼 구조: 번호 체계가 하나인가 ──
     한때 세 개가 동시에 돌았다 — 컬럼 .bn(1·2·3), 하단 flow .fn(1·2·3·4, 컬럼 헤드를 그대로 복창),
     카드 ①②③④(옛 세로 1열 시절 잔재). 같은 화면에서 "1"=거래 정보 입력인데 "①"=BBP였다. */
  ok("중복 flow 스트립 없음", document.querySelector(".flow") === null, "컬럼 헤드와 중복이던 스트립 제거");
  var _cardMarks = [].slice.call(document.querySelectorAll("#view-copilot .agent-n"))
    .map(function(e){ return e.textContent; });
  ok("카드 마커에 숫자 없음", !_cardMarks.some(function(m){ return /[①②③④0-9]/.test(m); }),
     "컬럼 번호 1·2·3 과 충돌 금지 · 현재: " + _cardMarks.join(" "));

  /* ── 컬럼 균형: 한 컬럼만 홀로 길어지지 않는가 ──
     거래국·품목을 컬럼2에 두던 시절 RM 모드에서 컬럼2=2434px인데 컬럼1=1137px이라
     입력 폼 아래로 1297px(화면 한 장 반)가 빈 채 컬럼2만 이어졌다(grid + align-items:start). */
  ["customer", "internal"].forEach(function(md){
    setMode(md); showView("copilot"); fillPreset(PRESETS[3]);
    var hs = [].slice.call(document.querySelectorAll("#view-copilot .appcol"))
      .map(function(c){ return c.getBoundingClientRect().height; });
    var spread = Math.max.apply(null, hs) - Math.min.apply(null, hs);
    ok("컬럼 높이 균형 " + md, spread < 700,
       hs.map(function(h){ return Math.round(h); }).join(" / ") + "px · 편차 " + Math.round(spread) + "px");
  });
  setMode("internal"); showView("copilot");

  /* 참고 정보(거래국·무역통계)는 주제 #6 요건 — 컬럼2에서 뺐지만 접거나 숨기지 않았다 */
  ok("참고 행 렌더", document.querySelector(".refrow") !== null
     && $("cty-stat").textContent.length > 0 && $("tradestat").textContent.length > 0,
     "거래국·품목 통계 유지");
  /* 결론(다음 단계)이 배경(참고 정보)보다 먼저 온다 */
  ok("다음 단계 → 참고 정보 순서",
     ($("nextsteps").compareDocumentPosition(document.querySelector(".refrow"))
      & Node.DOCUMENT_POSITION_FOLLOWING) !== 0, "결론 먼저, 배경 나중");

  /* ── 화면 용어: IT 용어가 고객·RM 화면에 새지 않는가 ──
     '스냅샷'은 은행 UI 어디에도 없는 개발 용어인데 시세·통계·제재 라벨 전반에 퍼져 있었다.
     소스 grep 으로는 못 잡는다(주석에도 쓰이고, internal-only 로 숨은 것도 있다) → 렌더된 텍스트로 본다. */
  /* 뷰 키는 showView 가 받는 이름이어야 한다 — 외환/시세는 "fx"(컨테이너 view-fx).
     "rates" 로 부르면 showView 가 조용히 view-copilot 으로 폴백해 그 화면을 영영 검사하지 않는다.
     비활성 뷰는 display:none 이라 innerText 에 안 잡히므로 화면마다 따로 읽는다. */
  VIEWS.forEach(function(v){
    ok("뷰 컨테이너 존재 " + v, !!document.getElementById("view-" + v), "view-" + v);
  });
  /* ⚠ 심화 탭(dp-a/dp-c/dp-e/dp-judge)은 display:none 이라 showView 만으로는 innerText 에 안 잡힌다.
     그래서 예전 de-jargon 패스가 기본 탭(dp-a) 외 패널을 **검사조차 못 했고**,
     dp-e·dp-judge 에 '폴백'·'stale' 이 그대로 살아 있었다 → showDeep 도 순회한다. */
  var DEEP = ["dp-a", "dp-c", "dp-e", "dp-judge"];
  ["customer", "internal"].forEach(function(md){
    setMode(md);
    var hits = [];
    function scan(where){
      var body = document.body.innerText || "";
      ["스냅샷", "폴백", "stale"].forEach(function(w){
        var i = body.indexOf(w);
        if (i >= 0) hits.push(where + ":" + w + " → " + body.substr(Math.max(0, i - 25), 60).replace(/\s+/g, " "));
      });
    }
    VIEWS.forEach(function(v){ showView(v); scan(v); });
    showView("copilot");
    if (md === "internal") DEEP.forEach(function(dp){ showDeep(dp); scan(dp); });
    showDeep("dp-a");
    ok("화면 IT용어 미노출 " + md, hits.length === 0, hits.length ? hits.join(" | ") : "스냅샷·폴백·stale 없음(심화 탭 포함)");
  });

  /* 심화 탭 ARIA — tablist/tab/tabpanel 은 있었지만 aria-controls·aria-labelledby 가 없어
     스크린리더가 "탭 4개 중 2번째"까지만 읽고 어느 내용이 그 탭의 것인지 잇지 못했다. */
  setMode("internal"); showView("copilot");
  var _dts = [].slice.call(document.querySelectorAll(".deeptab"));
  ok("심화 탭 aria-controls",
     _dts.length === 4 && _dts.every(function(t){ return document.getElementById(t.getAttribute("aria-controls")); }),
     _dts.map(function(t){ return t.getAttribute("aria-controls"); }).join(" "));
  ok("심화 패널 aria-labelledby",
     DEEP.every(function(id){
       var p = document.getElementById(id), lb = p && p.getAttribute("aria-labelledby");
       return lb && document.getElementById(lb); }),
     "패널→탭 역참조");
  /* roving tabindex — 탭 목록은 화살표로 이동하고 Tab 은 한 번만 걸려야 한다 */
  ok("심화 탭 roving tabindex",
     _dts.filter(function(t){ return t.tabIndex === 0; }).length === 1,
     "tabIndex=0 인 탭 " + _dts.filter(function(t){ return t.tabIndex === 0; }).length + "개");
  setMode("internal");

  /* ── 품목 무역통계(주제 #6 요건) ──
     고객 화면에 떠야 하고, 품목(HS) 단위 통계를 거래국별 수치인 척 말하면 안 된다.
     TRADE_STATS 는 HS 로만 키가 잡혀 있어 같은 HS·다른 거래국이면 같은 숫자가 나온다. */
  setMode("customer"); fillPreset(PRESETS[0]); showView("copilot");
  ok("무역통계 고객 노출", _vis($("tradestat")), "고객 모드 렌더");
  var _n0 = $("tradestat-note").textContent;
  ok("무역통계 출처·범위 고지", _n0.indexOf("거래국별 수치가 아닙니다") >= 0 && _n0.indexOf("출처") >= 0, "-");
  var _sameHs = PRESETS.filter(function(p){ return p.hs === "8542"; });
  if (_sameHs.length > 1) {
    var _texts = _sameHs.map(function(p){
      fillPreset(p); return $("tradestat-note").textContent;
    });
    ok("무역통계에 거래국 미귀속",
       _sameHs.every(function(p){ return _texts.join(" ").indexOf(p.country + " " + "반도체") < 0; }),
       "같은 HS·다른 거래국(" + _sameHs.map(function(p){ return p.country; }).join("/") + ") 동일 수치를 국가별로 표기하지 않음");
  }
  setMode("internal");

  /* 외화상품은 보조 상품 — 주력이 '검토'뿐인 건에서도 1순위를 가로채면 안 된다.
     실제로 대성무역(수출금융이 '검토' 하나뿐)에서 수취계좌가 1순위로 올라온 적이 있다. */
  PRESETS.forEach(function(p){
    var pr = primaryProduct(_recsOf(p, _deal(p)), p.biz);
    ok("1순위 ≠ 외화상품 " + p.name, !pr || pr.cat !== "외화상품", pr ? pr.t : "-");
  });

  /* ── 카탈로그 뷰(금융상품·정책자금·고객센터) ──
     정책자금의 '참고 대상' 판정은 라우팅과 별개 코드 → 세 번째 규칙 갈라짐이 실제로 있었다.
     (여신 보유 기업·개인사업자에게도 특별출연을 '참고 대상'으로 표시) */
  PRESETS.forEach(function(p){
    fillPreset(p); showView("policy");
    var tagged = [].slice.call(document.querySelectorAll("#pol-grid .prodc"))
      .filter(function(c){ return c.textContent.indexOf("참고 대상") >= 0; })
      .some(function(c){ return c.querySelector(".pc-n").textContent.indexOf("특별출연") >= 0; });
    var routed = _recsOf(p, _deal(p)).some(function(r){ return r.t.indexOf("특별출연") >= 0; });
    ok("정책자금↔라우팅 " + p.name, !(tagged && !routed),
       tagged && !routed ? "정책자금이 라우팅 제외 상품을 권함!" : "일치");
  });
  /* KB 상품이 '외부기관' 배지로 표시되면 안 된다(SRC_CLASS 매핑 누락 시 ext 로 폴백) */
  showView("products");
  ok("KB 상품 배지 분류",
     [].slice.call(document.querySelectorAll("#prod-grid .src"))
       .filter(function(s){ return s.textContent.indexOf("KB") >= 0 && s.classList.contains("ext"); }).length === 0,
     "KB 상품은 official/service");
  /* 고객센터: 시세 정책 변경이 FAQ에 반영됐는가 + esc() 렌더인데 태그를 넣지 않았는가 */
  showView("support");
  var _sup = document.getElementById("view-support").textContent;
  ok("FAQ 시세 안내 최신", _sup.indexOf("기준일 시세로 고정") >= 0
     && _sup.indexOf("KB 고시환율이 아닙니다") >= 0
     && /최신 고시 환율로 갱신|실시간\/스냅샷 상태/.test(_sup) === false, "기준일 고정 + 직접 조회 반영");
  ok("FAQ 국외이전 고지", _sup.indexOf("외부로 전송되지 않습니다") >= 0, "최대 강점 명시");
  ok("FAQ 태그 미노출", _sup.indexOf("<b>") < 0, "esc() 렌더 — 태그 금지");
  ok("용어사전 = 화면 용어", _sup.indexOf("선물 프리미엄") < 0 && _sup.indexOf("보수적 상한") >= 0,
     "화면에 없는 용어 설명 제거");
  showView("copilot"); fillPreset(PRESETS[3]);

  /* ── 폴백 요약(가드레일 포함) ── */
  fillPreset(PRESETS[3]);
  var plain = localBrief(_advisorPayload).replace(/<[^>]*>/g, " ");
  ok("행내 요약 내용",
     plain.indexOf("초과 가능성") >= 0 && plain.indexOf("부담액") >= 0 && plain.indexOf("KB 영업점") >= 0,
     "FACTS 포함");
  ok("행내 요약 가드레일", !_BAD_PHRASE.test(plain), "수익보장·투자권유 표현 없음");

  /* ── ★ 고객 거래정보 외부 전송 차단(신용정보 국외이전) ──
     기업명·거래금액·거래국·회사 기준환율(=손익분기점, 영업비밀)은 외부 추론 인프라로 나가면 안 된다.
     '문구'가 아니라 '실제 네트워크 요청'으로 검증한다. */
  ok("외부 호출 차단 플래그", AI_EXTERNAL_CALL_ENABLED === false, "AI_EXTERNAL_CALL_ENABLED=false");
  var _sent = [], _rf = window.fetch;
  window.fetch = function(u, o){ _sent.push({u: String(u), b: (o && o.body) ? String(o.body) : ""}); return _rf.apply(window, arguments); };
  fillPreset(PRESETS[3]);
  generateAdvisorBrief();
  window.fetch = _rf;
  ok("/advisor 미호출", _sent.filter(function(s){ return s.u.indexOf("/advisor") >= 0; }).length === 0,
     "요청 " + _sent.length + "건");
  ok("고객정보 미송신",
     _sent.filter(function(s){ return /나래상사|400000|1500|budget/.test(s.b); }).length === 0, "송신 0건");
  ok("외부 미전송 표기", $("ai-out").textContent.indexOf("외부 미전송") >= 0, "행내 생성 명시");

  /* ── 부장 지적: 숫자의 성격·근거를 고객 화면에서 말하는가 ── */
  fillPreset(PRESETS[3]);   // 나래상사 = 수입
  // 렌더 텍스트만 본다 — 주입된 프로브 스크립트 소스에도 검사 문구가 들어 있어 자기참조로 오염됨
  function _visText(){
    var c = document.body.cloneNode(true);
    [].slice.call(c.querySelectorAll("script")).forEach(function(s){ s.remove(); });
    return c.textContent;
  }
  var _vt = _visText();
  ok("부담액은 '보수적 상한' 표기", _vt.indexOf("보수적 상한") >= 0 && _vt.indexOf("예상 부담액") < 0,
     "'예상' 단정 표현 제거");
  ok("ES 보수성 고객 고지", $("bbp-honesty").textContent.indexOf(ES_BIAS.ratio.toFixed(1) + "배") >= 0,
     "실제 평균의 1.9배 명시");
  /* 문구가 아니라 '사실이 고지됐는가'를 본다(수입 실제 초과빈도 + 방향별 오차 배수).
     'ECE 0.139'는 고객이 읽을 수 없어 평문으로 바꿨다 — 검사 대상은 용어가 아니라 사실이다. */
  ok("수입 방향 오차 고지", $("bbp-honesty").textContent.indexOf("74.5%") >= 0
     && $("bbp-honesty").textContent.indexOf("평균의 4배") >= 0, "실제 이탈빈도·오차 배수 명시");
  fillPreset(PRESETS[0]);   // 한빛정밀 = 수출 → 문구가 바뀌어야 함
  ok("수출 방향 고지 분기", $("bbp-honesty").textContent.indexOf("25.5%") >= 0
     && $("bbp-honesty").textContent.indexOf("74.5%") < 0, "수출 수치로 분기");
  /* 스탬프는 '언제 기준 숫자냐'만 남긴다 — 소관 부서는 데이터 운영 상태표가 관리(화면에 조직도 금지) */
  ok("산출 근거 스탬프", $("bbp-stamp").textContent.indexOf("FX-EWI v0.1") >= 0
     && $("bbp-stamp").textContent.indexOf(MARKET.date) >= 0, "모델·기준일 표기");
  /* 선물환율은 KB 고시가 아닌 CIP 이론가 → 고객 화면 노출 금지 */
  var fwdBox = document.getElementById("h-fwd").closest(".box");
  ok("선물환율 고객 미노출", fwdBox && fwdBox.classList.contains("internal-only"), "RM 전용 + 이론가 라벨");
  /* ── 다통화(2026-07-17 개방) ──
     규율은 "USD 만 연다"가 아니라 "σ·금리를 실측한 통화만 연다"로 바뀌었다. */
  ok("엔진 통화 = 실측 3종", Object.keys(CUR).length === 3
     && !!CUR.USD && !!CUR.EUR && !!CUR.JPY, "CUR=" + Object.keys(CUR).join(","));
  ok("미실측 통화는 선택 불가",
     [].slice.call(document.querySelectorAll("#in-cur option")).filter(function(o){
       return !CUR[o.value] && !o.disabled; }).length === 0, "CNY·VND disabled");
  ok("실측 통화는 선택 가능",
     [].slice.call(document.querySelectorAll("#in-cur option")).filter(function(o){
       return !!CUR[o.value] && o.disabled; }).length === 0, "USD·EUR·JPY 활성");
  /* 핵심: σ 는 통화별 실측이지 USD 배수가 아니다. 배수였다면 국면별 비율이 통화 간 같다. */
  ok("국면 σ = 통화별 실측(배수 아님)",
     Math.abs((REG.EUR[3].sigAnn / REG.EUR[0].sigAnn) - (REG.USD[3].sigAnn / REG.USD[0].sigAnn)) > 0.05,
     "달러 강세/현재 비율 USD=" + (REG.USD[3].sigAnn / REG.USD[0].sigAnn).toFixed(2)
     + " vs EUR=" + (REG.EUR[3].sigAnn / REG.EUR[0].sigAnn).toFixed(2));
  ok("USD 스냅샷 불변", CUR.USD.sig === 0.098 && CUR.USD.spot === 1528.8,
     "문서·BBP 64.3% 가 이 상수에 걸려 있다");
  /* 통화마다 캘리브레이션을 따로 잰다 — EUR 을 열면서 USD 의 ECE 를 들이대면 거짓말이다 */
  ok("통화별 캘리브레이션 보유", !!CUR.USD.calib && !!CUR.EUR.calib && !!CUR.JPY.calib
     && CUR.EUR.calib.ece !== CUR.USD.calib.ece,
     "USD ECE " + CUR.USD.calib.ece + " · EUR " + CUR.EUR.calib.ece + " · JPY " + CUR.JPY.calib.ece);
  setMode("internal");
  $("in-cur").value = "EUR"; $("in-cur").dispatchEvent(new Event("change"));
  ok("통화별 ECE 화면 노출(RM)", ($("cur-note").textContent || "").indexOf(String(CUR.EUR.calib.ece)) >= 0,
     "고르는 자리에서 그 통화의 정합도를 말한다");
  ok("열위 통화 경고 표기", ($("cur-note").textContent || "").indexOf("USD") >= 0
     && ($("cur-note").textContent || "").indexOf("drift") >= 0,
     "USD보다 나쁘면 이유(drift=0)까지 밝힌다");
  /* 같은 사실을 고객에게는 쉬운 말로 — ECE·Brier·σ·drift 는 사장님이 읽을 말이 아니다(체크리스트 P0 §3).
     경고 자체는 남아야 한다: 숨기면 정직성이 아니라 은폐다. */
  setMode("customer");
  /* 보이는 글자만 모은다 — innerText 폴백(|| textContent)을 쓰면 숨긴 글자가 딸려와 검사가 무의미해진다 */
  var _visText = function(node){
    var t = "";
    [].slice.call(node.childNodes).forEach(function(x){
      if (x.nodeType === 3) { t += x.nodeValue; return; }
      if (x.nodeType !== 1) return;
      if (getComputedStyle(x).display === "none") return;
      t += _visText(x);
    });
    return t;
  };
  var _cnVis = function(){ return _visText($("cur-note")); };
  ok("고객에겐 ECE·σ 미노출", !/ECE|Brier|drift|σ/.test(_cnVis()), "보이는 글자: " + _cnVis().slice(0, 60));
  ok("고객에게도 경고는 남김", _cnVis().indexOf("정확도가 낮") >= 0,
     "숨기는 게 아니라 쉬운 말로 바꾼다");
  setMode("internal");
  /* 통화를 바꾸면 BBP 가 실제로 달라져야 한다(σ 가 다르므로) */
  $("in-cur").value = "USD"; $("in-cur").dispatchEvent(new Event("change"));
  var _bU = parseFloat($("bbp").textContent);
  $("in-cur").value = "JPY"; $("in-cur").dispatchEvent(new Event("change"));
  var _bJ = parseFloat($("bbp").textContent);
  ok("통화 전환 → 재계산", Math.abs(_bU - _bJ) > 0.05, "USD=" + _bU + "% → JPY=" + _bJ + "%");
  /* 대시보드는 USD 프리셋이다 → 폼 통화(JPY)에 오염되면 안 된다 */
  var _dJ = _deal(PRESETS[3]).bbp;
  $("in-cur").value = "USD"; $("in-cur").dispatchEvent(new Event("change"));
  var _dU = _deal(PRESETS[3]).bbp;
  ok("대시보드는 폼 통화에 오염 안 됨", Math.abs(_dJ - _dU) < 1e-9,
     "USD 프리셋은 REG.USD 를 직접 읽는다 (" + _dU.toFixed(2) + "%)");
  fillPreset(PRESETS[3]);

  /* ── 국면 노트(2026-07-17) ──
     σ 와 EWI 는 다른 축이다(수준 vs 자기 과거 대비 상대경보) → 둘이 어긋나 보이는 칩이 생긴다.
     EUR '평온' 은 σ 가 5개 국면 중 최저인데 EWI 79(경계)다. 이름을 고치는 건 거짓말이고
     (σ 최저는 세 통화 모두 그 날짜다), 화면이 그 차이를 스스로 설명해야 한다.
     REG[].note 는 통화별로 써 뒀는데 렌더되지 않아 죽은 데이터였다. */
  $("in-cur").value = "USD"; $("in-cur").dispatchEvent(new Event("change"));
  applyRegime(1);
  var _nU = $("regime-note").textContent;
  ok("국면 노트 렌더", _nU.indexOf("평온") >= 0 && _nU.indexOf("2019-07-25") >= 0
     && _nU.indexOf("5.8%") >= 0, "선택 국면의 날짜·실측 σ·설명");
  $("in-cur").value = "EUR"; $("in-cur").dispatchEvent(new Event("change"));
  applyRegime(1);
  var _nE = $("regime-note").textContent;
  ok("국면 노트는 통화별", _nE !== _nU && _nE.indexOf("EUR") >= 0 && _nE.indexOf("5.0%") >= 0,
     "같은 국면·다른 통화 = 다른 실측");
  ok("σ↔EWI 어긋남을 설명함", _nE.indexOf("자기 과거 대비") >= 0 && _nE.indexOf("경계") >= 0,
     "EUR 평온: σ 최저인데 EWI 79 인 이유를 화면이 말한다");
  /* σ 최저 국면은 세 통화 모두 '평온'(2019-07-25) — 이름은 정확하다. 날짜로 바꿀 이유가 없다. */
  ["USD","EUR","JPY"].forEach(function(c){
    var rs = REG[c], lo = rs.reduce(function(a,b){ return b.sigAnn < a.sigAnn ? b : a; });
    ok("σ 최저 = 평온 (" + c + ")", lo.key === "평온",
       "σ " + (lo.sigAnn*100).toFixed(1) + "% · EWI " + lo.ewi);
  });
  $("in-cur").value = "USD"; $("in-cur").dispatchEvent(new Event("change"));
  applyRegime(0);

  /* ── 반복거래 선제 알림(2026-07-17) ──
     기존 알림은 전부 '이미 잡힌 거래'에 대한 사후 반응이었다(만기 임박·BBP 초과).
     선제성 = 아직 계약도 안 한 다음 회차를 미리 경보하는 것. */
  ok("반복 패턴 = 데모 이력 보유", Object.keys(RECUR).length >= 3
     && !RECUR["대성무역"], "가결제 단계인 대성무역엔 없는 패턴을 만들지 않는다");
  var _r = recurOf(PRESETS[3]);   // 나래상사
  ok("다음 회차 = 이번 만기 + 주기", _r && _r.dueBd === PRESETS[3].horizon + RECUR["나래상사"].everyBd,
     "D+" + (_r ? _r.dueBd : "?"));
  /* 만기가 멀다고 BBP 가 늘 커지는 게 아니다 — 이미 예산환율을 넘어선 나래상사는
     분포가 넓어지면 되돌아올 여지가 생겨 오히려 낮아진다. 화면 문구도 이걸 그렇게 설명해야 한다. */
  ok("선제 BBP 는 방향 고정 아님", _r && _r.delta < 0,
     "나래상사(이미 초과)는 다음 회차가 " + (_r ? _r.delta.toFixed(1) : "?") + "%p — 멀수록 나빠진다는 서술은 거짓");
  var _rh = recurOf(PRESETS[0]);  // 한빛정밀 — 아직 유리한 쪽
  ok("유리한 쪽은 멀수록 커짐", _rh && _rh.delta > 0,
     "한빛정밀 +" + (_rh ? _rh.delta.toFixed(1) : "?") + "%p — 방향이 포지션에 따라 갈린다");
  showView("dashboard");
  var _rc = document.querySelector("#recur-table");   // 반복결제 카드(대시보드엔 네팅 spectable 도 있어 id 로 특정)
  ok("반복 결제 카드 렌더", !!_rc && _rc.querySelectorAll("tbody tr").length === Object.keys(RECUR).length,
     "행 " + (_rc ? _rc.querySelectorAll("tbody tr").length : 0) + "개");
  var _dv = document.getElementById("view-dashboard");
  /* ── 자연헤지(네팅) 집계 (S8) ── */
  var _dvt = _dv.textContent;
  ok("네팅 집계 카드 렌더", _dvt.indexOf("자연헤지(네팅) 집계") >= 0 && _dvt.indexOf("버킷 네팅 후 순노출") >= 0,
     "만기버킷 상계 패널");
  (function(){
    var _deals = PRESETS.map(_deal), _net = _portfolioNetting(_deals);
    var _exp = (_net.gross ? (_net.off/_net.gross*100) : 0).toFixed(0);
    /* 버킷 네팅은 만기 무시 상계(과대상계)보다 순노출이 작을 수 없다 = 정직한 상계 */
    ok("네팅 절감률 계산 정확", _dvt.indexOf(_exp + "%") >= 0 && _net.netInBucket >= _net.naiveNet,
       "절감률 " + _exp + "% · 버킷순노출 " + _net.netInBucket + " ≥ 만기무시순노출 " + _net.naiveNet);
  })();
  ok("간이 네팅 카드 일원화", _dvt.indexOf("순노출만 관리") < 0,
     "만기 무시 상계 카드는 버킷 패널로 일원화(과대상계 제거)");
  /* ── 신규: 헤지수단 비교 패널 + 범위선물환 밴드 개념도 (S8) ── */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[3]); showDeep("dp-a");   // 나래상사(수입·확정·여신)
  var _hc = document.getElementById("hedge-compare");
  ok("헤지수단 비교 패널 렌더", !!_hc && _hc.textContent.indexOf("헤지수단 비교") >= 0 && !!_hc.querySelector("svg"),
     "비교표 + 밴드 개념도");
  ok("비교표에 범위·기간형 노출", !!_hc && _hc.textContent.indexOf("범위선물환") >= 0 && _hc.textContent.indexOf("기간형 선물환") >= 0,
     "신상품이 비교에 포함");
  ok("밴드 개념도 숫자 미표기(정직)", !!_hc && !/[0-9]{3,}/.test((_hc.querySelector("svg") || {}).textContent || ""),
     "구조도에 요율/행사가 숫자 없음");
  ok("공정가 TCA-lite 표기", !!_hc && _hc.textContent.indexOf("공정가 안내") >= 0 && _hc.textContent.indexOf("KB 고시 스프레드") >= 0,
     "중간환율만 표시 · 스프레드는 RM 고시");
  ok("위험회피회계 지정문서 초안", !!_hc && _hc.textContent.indexOf("위험회피회계") >= 0 && _hc.textContent.indexOf("현금흐름위험회피") >= 0,
     "K-IFRS 초안 · 회계처리는 감사인 협의");
  $("in-settle").value = "window"; compute();
  ok("기간형 결제일 window 자격", eligible("기간형선물환", readForm()) === true, "확정·여신·기간 → 기간형 가능");
  $("in-settle").value = "fixed"; compute();
  ok("기간형 결제일 fixed 비자격", eligible("기간형선물환", readForm()) === false, "결제일 확정이면 고정 선물환이 저렴");
  showView("copilot");

  /* ── 체결 후 라이프사이클(2026-07-17) ──
     흐름이 RM 티켓에서 끝나면 계약 이후의 사고(실수요 초과·만기 불일치)를 아무도 안 본다. */
  setMode("internal"); showView("copilot"); showDeep("dp-c"); fillPreset(PRESETS[3]);
  var _lc = function(){ return $("lc-body").textContent.replace(/\s+/g, " "); };
  ok("체결 내역 렌더", _lc().indexOf("KB 선물환") >= 0 && _lc().indexOf("300,000") >= 0,
     "나래상사 데모 약정");
  /* 실수요 초과는 폼에서 파생돼야 한다 — 하드코딩이면 금액을 바꿔도 안 잡힌다 */
  $("in-amount").value = "200000"; compute();
  ok("실수요 초과 감지", _lc().indexOf("실수요 초과") >= 0 && _lc().indexOf("부분해지") >= 0,
     "결제금액을 체결 헤지보다 낮추면 외국환거래법 실수요 원칙 위반 → 부분해지");
  $("in-amount").value = "400000"; compute();
  ok("미헤지 잔량 감지", _lc().indexOf("미헤지 잔량") >= 0, "실수요 > 체결헤지면 잔량 노출");
  $("in-horizon").value = "90"; compute();
  ok("만기 불일치 → 롤오버", /롤오버 검토 27영업일/.test(_lc()),
     "결제(90) - 헤지만기(63) = 27영업일 헤지 공백");
  $("in-horizon").value = "63"; compute();
  /* 해지 정산금·스왑포인트를 화면이 만들어내면 안 된다 — 고시 조건이 필요하다 */
  /* "RM 확인"은 초과·롤오버 브랜치에만 나온다 → 항상 렌더되는 guard 문장으로 검사한다 */
  ok("정산금은 만들지 않음", _lc().indexOf("RM·영업점이 확정") >= 0
     && !/정산금\s*[0-9,]+\s*원/.test(_lc()) && !/스왑포인트\s*[0-9.]+원/.test(_lc()),
     "무엇을 해야 하는지까지만 말하고 금액은 RM 이 확정");
  fillPreset(PRESETS[0]);
  ok("체결 없으면 그렇게 말함", _lc().indexOf("체결된 헤지가 없습니다") >= 0,
     "없는 약정을 만들지 않는다");
  fillPreset(PRESETS[3]); showDeep("dp-a");

  /* ── 2026-07-23 기능 증강 회귀 ──
     벤치마킹 반영분(RM 큐·실수요 원장·L/C 문서심사·마진콜 확률·내규 초안·정책지원·이벤트 캘린더·IZ 토글).
     전부 폼/국면에서 파생돼야 한다 — 하드코딩이면 입력을 바꿔도 화면이 안 바뀐다. */
  setMode("internal"); showView("copilot"); showDeep("dp-a"); fillPreset(PRESETS[3]);
  var _rmq = $("rmq-table");
  ok("RM 큐 전 건 렌더", !!_rmq && _rmq.querySelectorAll(".rmq-r").length === PRESETS.length,
     "프리셋 " + PRESETS.length + "건이 큐에");
  ok("RM 큐 현재 건 표시", !!_rmq && _rmq.textContent.indexOf("보는 중") >= 0, "현재 회사 하이라이트");
  var _row = _rmq && _rmq.querySelector('.rmq-r[data-i="0"]'); if (_row) _row.click();
  ok("RM 큐 클릭 전환", $("in-name").value === "한빛정밀", "행 클릭 → 프리셋 로드");
  /* L/C 문서심사: 결제방식에서 파생 — 한빛(L/C)은 보이고 나래(T/T)는 숨어야 한다 */
  showDeep("dp-c");
  ok("L/C 문서심사 표시(L/C 건)", $("lcdoc-card").style.display !== "none"
     && $("lcdoc-body").textContent.indexOf("선하증권") >= 0, "한빛정밀 · 서류 심사 상태");
  fillPreset(PRESETS[3]);
  ok("L/C 문서심사 숨김(T/T 건)", $("lcdoc-card").style.display === "none", "나래상사 T/T → 비표시");
  /* 실수요 원장(F4): 기체결 300k → 신규 가능 100k, 결제를 200k 로 줄이면 중복헤지 경고 */
  var _lg = function(){ return $("h-ledger").textContent.replace(/\s+/g, " "); };
  ok("실수요 원장 잔여 계산", _lg().indexOf("신규 가능") >= 0 && _lg().indexOf("100,000") >= 0,
     "결제 400k − 기체결 300k = 100k");
  $("in-amount").value = "200000"; compute();
  ok("중복헤지 차단 경고", _lg().indexOf("전액 헤지") >= 0 || _lg().indexOf("중복헤지") >= 0,
     "결제 200k < 기체결 300k → 신규 0");
  $("in-amount").value = "400000"; compute();
  ok("원장 없으면 원장 숨김", (fillPreset(PRESETS[1]), $("h-ledger").style.display === "none"),
     "대성무역 — 체결 헤지 없음");
  fillPreset(PRESETS[3]);
  /* 마진콜 확률 — 시나리오 격자(얼마)에 확률(가능성)을 더한다. 만기 시점 근사 고지 필수 */
  ok("마진콜 확률 표시", $("lc-body").textContent.indexOf("추가담보 요구 가능성") >= 0
     && $("lc-body").textContent.indexOf("만기 시점 기준 근사") >= 0, "확률 + 근사 한계 고지");
  /* 정책 밴드 준수(내규 연동) — 나래 수입 75% ∈ [50,100] */
  ok("정책 밴드 준수 판정", $("lc-body").textContent.indexOf("정책 밴드 준수") >= 0
     && $("lc-body").textContent.indexOf("준수") >= 0, "체결 75% vs 수입 밴드 50~100%");
  /* 이벤트 캘린더 오버레이 — 만기 63영업일 안에 공표 일정이 든다 */
  ok("타임라인 이벤트 마커", $("timeline").textContent.indexOf("FOMC") >= 0
     && $("tl-sub").textContent.indexOf("예정 이벤트") >= 0, "금리·물가 일정이 만기 타임라인에");
  /* 내규 초안·정책지원 — 헤지 비교 카드에서 파생(정책지원은 수출·법인만) */
  var _hc2 = $("hedge-compare").textContent;
  ok("내규 초안 생성", _hc2.indexOf("환위험관리 내규") >= 0 && _hc2.indexOf("논의 출발점") >= 0,
     "프로필 기반 사규 초안 + 확정은 사내 승인");
  ok("정책지원 수입건 비표시", _hc2.indexOf("정책지원 매칭") < 0, "나래상사(수입)는 보험료 지원 대상 아님");
  fillPreset(PRESETS[0]);
  ok("정책지원 수출건 표시", $("hedge-compare").textContent.indexOf("정책지원 매칭") >= 0
     && $("hedge-compare").textContent.indexOf("지어내") < 0, "한빛정밀(수출 중소) — 지원제도 판정");
  /* 이중용도 매트릭스(F8): HS 대분류 × 목적국 조합 판정 */
  showDeep("dp-e");
  ok("이중용도 저위험국 = 확인 권장", $("gov-aml").textContent.indexOf("확인 권장") >= 0,
     "한빛 8542(전자·반도체) × 미국");
  fillPreset(PRESETS[2]); // 소망전자 8542 × 중국
  ok("이중용도 고위험국 = 판정 필요", $("gov-aml").textContent.indexOf("전략물자 판정 필요") >= 0,
     "소망 8542 × 중국 조합");
  /* IZ 개입 감쇠 토글 — 켜면 BBP 가 내려가야 한다(×0.7) */
  fillPreset(PRESETS[3]); showDeep("dp-a");
  var _b0 = parseFloat($("hd-bbp").textContent);
  $("iz-toggle").checked = true; $("iz-toggle").dispatchEvent(new Event("change"));
  var _b1 = parseFloat($("hd-bbp").textContent);
  ok("IZ 토글 = BBP 감쇠", _b1 < _b0 && _b1 > 0, _b0.toFixed(1) + "% → " + _b1.toFixed(1) + "% (×0.7)");
  $("iz-toggle").checked = false; $("iz-toggle").dispatchEvent(new Event("change"));
  ok("IZ 해제 = 원복", Math.abs(parseFloat($("hd-bbp").textContent) - _b0) < 0.2, "재현성 유지");
  /* 위 추가 테스트들이 감사로그를 늘려 200건 캡에 닿으면, 아래 "재조회 = +1건" 검증이
     캡 때문에 깨진다(길이 불변). 캡 아래로 비워 원래 검증 의미를 유지한다. */
  _audit.length = Math.min(_audit.length, 150);

  /* ── 캘리브레이션 수치 = CALIB 상수 단일출처 ── */
  ok("CALIB 배선", $("ts-ece").textContent.indexOf("0.034") >= 0
     && $("calib-s").textContent.indexOf("3,800") >= 0
     && $("calib-kv").textContent.indexOf("0.175") >= 0
     && $("qa-verify").textContent.indexOf("0.034") >= 0, "4곳 상수 렌더");

  /* ── 회귀: 죽은 코드 정리 후에도 안전 ── */
  ok("view-soon 제거", document.getElementById("view-soon") === null, "-");
  var crashed = false;
  try { showView("존재하지않는뷰"); } catch (e) { crashed = true; }
  ok("알 수 없는 뷰 폴백", !crashed && document.getElementById("view-copilot").classList.contains("active"),
     "→ 코파일럿");
  ok("iz 유지(bbp.py 미러)", typeof MARKET.iz !== "undefined" && bbpProb.length === 6,
     "정책개입 파라미터 보존");

  /* ── RM 내부 화면 구조: 실무 3탭에 발표물이 새지 않는가 ──
     이 데모는 RM(실무자)과 심사위원(평가자) 두 독자를 함께 담는다.
     발표·심사물은 ④ 심사 자료 탭에만 있어야 하며, 실무 탭에 섞이면 RM 화면이 피치덱이 된다. */
  setMode("internal");
  var dtabs = [].slice.call(document.querySelectorAll(".deeptab"));
  ok("심화 탭 4개", dtabs.length === 4, dtabs.map(function(t){ return t.textContent.trim(); }).join(" / "));
  var PITCH = ["왜 KB국민은행인가", "심사위원용", "발표용", "파일럿 KPI", "데모 → 파일럿", "운영 게이트",
               "권한 매트릭스", "연동 시퀀스", "payload", "정직 검증", "금융 AI 가이드라인", "금소법",
               "Before → After", "기대효과"];
  ["dp-a", "dp-c", "dp-e"].forEach(function(id){
    var el = document.getElementById(id);
    var hit = PITCH.filter(function(w){ return el && el.textContent.indexOf(w) >= 0; });
    ok("실무탭 " + id + " 발표물 없음", el && hit.length === 0, hit.length ? "누출: " + hit.join(",") : "없음");
  });
  var jd = document.getElementById("dp-judge");
  ok("심사 자료 탭 존재 + 경고", jd && jd.textContent.indexOf("실서비스 RM 화면이 아닙니다") >= 0, "명시");
  ok("심사물은 심사탭에 집결",
     jd && PITCH.filter(function(w){ return jd.textContent.indexOf(w) < 0; }).length === 0, "전부 포함");
  /* RM 핵심 동선: 결정요약 CTA → 상담·진행 탭(티켓) */
  showDeep("dp-a");
  document.getElementById("hd-cta").click();
  ok("CTA → 상담·진행 탭", document.getElementById("dp-c").classList.contains("active"), "-");
  document.getElementById("book").click();
  ok("RM 티켓 생성", document.getElementById("ticket-card").style.display !== "none"
     && $("tk-grid").children.length > 0, $("tk-grid").children.length + "필드");
  ok("① BBP 신뢰도 1줄", ($("calib-lite").textContent || "").indexOf("ECE 0.034") >= 0, "요약 렌더");

  /* ── 감사로그 역할 분리(RBAC 정합) ──
     화면의 권한 매트릭스가 '감사로그 = 준법·AML 권한, 영업 목적 사용 제한'이라고 선언하므로,
     RM 실무 화면에 전체 로그·CSV 추출이 있으면 자기모순이다. RM은 담당 건 처리 이력만 읽기 전용. */
  var e3 = document.getElementById("dp-e");
  ok("③ RM 감사 추출 차단",
     e3.querySelector("#audit-export") === null && e3.querySelector("#audit-log") === null,
     "RM 화면에 전체 로그·CSV 없음");
  ok("③ RM 처리 이력 읽기전용",
     $("audit-log-rm") !== null && $("audit-log-rm").children.length > 0,
     $("audit-log-rm") ? $("audit-log-rm").children.length + "건" : "없음");
  ok("④ 준법 전체 로그 + CSV",
     jd.querySelector("#audit-log") !== null && jd.querySelector("#audit-export") !== null, "-");
  ok("세션 이력 ⊆ 영구 로그", _sessionAudit.length <= _audit.length,
     "세션 " + _sessionAudit.length + " / 영구 " + _audit.length);

  /* ── 제재 스크리닝: 모르는 상대를 통과시키지 않는가 ──
     조회 대상(거래상대방)이 실수요 증빙으로 확정되기 전에 '정상 · 일치 없음'이 뜨면
     하지도 않은 조회를 통과로 보고하는 셈이다. 준법상 가장 위험한 거짓 신호. */
  fillPreset(PRESETS[3]);                       // gate=false 상태로 시작
  ok("게이트 전 상대방 미확인",
     $("gov-aml").textContent.indexOf("미확인 · 조회 대상 미확정") >= 0, "통과 표시 안 함");
  gate = true; gateInfo = { party: "Narae Materials Import", unipass: "41055-26-7012036" };
  compute();
  ok("게이트 후 조회 가능", $("gov-aml").textContent.indexOf("미확인 · 조회 대상 미확정") < 0, "미확인 해소");
  var sm = $("sanc-meta").textContent;
  ok("조회 메타(감사 방어)",
     /조회 시각\s*20\d\d/.test(sm) && sm.indexOf("리스트 기준일 " + SANC_LIST_ASOF) >= 0
     && sm.indexOf("RM 인증 세션") >= 0, "시각·리스트버전·조회자 기록");
  var n0 = _audit.length;
  $("sanc-recheck").click();
  ok("재조회 = 감사 기록 행위",
     _audit.length === n0 + 1 && _audit[0].ev.indexOf("제재 스크리닝 재조회") >= 0, "-");
  setMode("customer");

  /* ── 명도대비(KWCAG 2.2 / WCAG AA 본문 4.5:1) ──
     은행은 접근성 의무 대상이라 이건 취향이 아니라 규정이다. 브라우저가 실제 해석한 색으로 검증한다. */
  function _lin(c){ c=c/255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4); }
  function _lum(t){ return 0.2126*_lin(t[0])+0.7152*_lin(t[1])+0.0722*_lin(t[2]); }
  function _rgb(s){ var m=s.match(/\d+/g); return [ +m[0], +m[1], +m[2] ]; }
  function _cr(a,b){ var l1=_lum(a), l2=_lum(b); if(l1<l2){ var t=l1; l1=l2; l2=t; }
    return (l1+0.05)/(l2+0.05); }
  function _resolved(host, val){
    var s=document.createElement("span"); s.style.color=val; host.appendChild(s);
    var c=getComputedStyle(s).color; s.remove(); return _rgb(c);
  }
  document.documentElement.setAttribute("data-theme","light");
  var card = document.querySelector(".card");
  var cardBg = _rgb(getComputedStyle(card).backgroundColor);
  ["--ink", "--ink-mut", "--ink-faint", "--good", "--warn", "--elev", "--crit", "--gold-ink"].forEach(function(tok){
    var v = _cr(_resolved(card, "var(" + tok + ")"), cardBg);
    ok("대비 " + tok, v >= 4.5, v.toFixed(2) + ":1");
  });
  /* 사이드바는 네이비 배경 — 같은 토큰이 그 문맥에서도 읽혀야 한다 */
  var sr = document.querySelector(".side-rate");
  var srBg = _rgb(getComputedStyle(sr).backgroundColor);
  ["--good", "--warn", "--elev", "--crit"].forEach(function(tok){
    var v = _cr(_resolved(sr, "var(" + tok + ")"), srBg);
    ok("대비 사이드바 " + tok, v >= 4.5, v.toFixed(2) + ":1");
  });

  /* 렌더 텍스트만 검사(스크립트 소스 제외) */
  var vis = document.body.cloneNode(true);
  [].slice.call(vis.querySelectorAll("script")).forEach(function(s){ s.remove(); });
  ok("'고시' 오표기 없음", vis.textContent.indexOf("당일 고시") < 0, "화면 문구 정확");

  function finish(){
    // 마커는 런타임 조립 — 스크립트 소스가 DOM 덤프에 그대로 실리므로,
    // 소스에 완성된 마커가 있으면 정규식이 결과 대신 소스를 잡는다(자기참조 오염).
    var M = "@" + "@FXS" + "@@";
    var out = document.createElement("div");
    out.id = "__fxs";
    out.textContent = M + L.join(" ;; ") + M;
    document.body.appendChild(out);
  }

  /* ── 서류 업로드가 '자동 입력'이라 말한 대로 실제로 채우는가 (비동기) ──
     예전 구현은 아무것도 채우지 않고 기존 값에 하이라이트만 준 뒤 "자동 입력되었습니다"라고 했다.
     사람이 친 값을 넣고 업로드해 '값이 실제로 바뀌는지'를 본다 — 하이라이트 유무로는 못 잡는다.
     인보이스에 없는 회사 정보(기업명·기준환율)를 덮어쓰지 않는 것도 함께 고정한다. */
  setMode("customer"); showView("copilot"); fillPreset(PRESETS[0]);
  $("in-name").value = "테스트상사"; $("in-amount").value = "123456";
  $("in-horizon").value = "99"; $("in-country").value = "일본"; $("in-budget").value = "1234";
  runOCR();
  /* 진행 '중'이라는 사실이 보조기술에 전달되는가.
     완료 시점엔 toast(role=status)가 읽어주지만, 그 전까지는 멈춘 건지 도는 건지 알 수 없었다. */
  ok("OCR 진행 중 aria-busy", $("scan").getAttribute("aria-busy") === "true",
     "aria-busy=" + $("scan").getAttribute("aria-busy"));
  setTimeout(function(){
    ok("OCR 완료 후 aria-busy 해제", $("scan").getAttribute("aria-busy") === "false",
       "aria-busy=" + $("scan").getAttribute("aria-busy"));
    ok("서류가 폼을 실제로 채움",
       $("in-amount").value === String(PRESETS[0].amount)
       && $("in-horizon").value === String(PRESETS[0].horizon)
       && $("in-country").value === PRESETS[0].country,
       "금액 123456→" + $("in-amount").value + " 만기 99→" + $("in-horizon").value);
    ok("서류가 회사 정보는 안 건드림",
       $("in-name").value === "테스트상사" && $("in-budget").value === "1234",
       "기업명·기준환율은 인보이스에 없음 → 보존");
    ok("서류 확인 → 헤지 잠금 해제", gate === true, $("gate-h").textContent.trim());
    ok("읽은 서류명 표시", ($("doc-name").textContent || "").indexOf(PRESETS[0].name) >= 0,
       $("doc-name").textContent.trim());
    /* 가결제는 업로드해도 잠긴다 — 그 사유가 화면에 있어야 한다(예전엔 접힌 서랍 안에만 있었다) */
    fillPreset(PRESETS[1]); $("in-cert").value = "provisional";
    runOCR();
    setTimeout(function(){
      ok("가결제 잠금 사유 표시",
         gate === false && $("gate-note").textContent.indexOf("확정 거래에만") >= 0,
         $("gate-h").textContent.trim());
      ok("'자동 입력' 거짓 주장 없음",
         document.body.innerText.indexOf("자동 입력되었습니다") < 0, "실제로 채운 항목만 고지");
      /* 폰트 로드는 비동기 — 파싱 직후엔 status=loading 이라 여기(≈6s 후)서 본다.
         @font-face 선언만 있고 base64 가 깨졌으면 조용히 폴백하므로 실제 로드를 확인한다. */
      ok("임베드 폰트 로드 완료",
         document.fonts.status === "loaded" && document.fonts.check("700 14px KBFXSans", "가"),
         "status=" + document.fonts.status + " · 한글 글리프 확인");
      finish();
    }, 3000);
  }, 3000);
})();
</script>
"""


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    sys.exit("Chrome을 찾을 수 없습니다. CHROME_CANDIDATES에 경로를 추가하세요.")


def render(html_text, tmpdir, name):
    """주입된 HTML을 헤드리스 Chrome으로 렌더하고 DOM 덤프를 반환."""
    path = os.path.join(tmpdir, name)
    io.open(path, "w", encoding="utf-8").write(html_text)
    proc = subprocess.run(
        [find_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
         "--force-prefers-reduced-motion",          # animNum 트윈 확정 → 게이지 값 읽기 가능
         # 창 크기를 안 주면 헤드리스 기본이 800x600 이라 .app3(브레이크포인트 1160px)가
         # 1열로 무너진 상태를 재게 된다 — 3컬럼 레이아웃 검사가 통째로 무의미해진다.
         "--window-size=1440,900",
         "--virtual-time-budget=20000", "--dump-dom",   # 서류 업로드 비동기 검증(≈6s)까지 포함
         "file:///" + path.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    return proc.stdout or ""


def main():
    src = io.open(SRC, encoding="utf-8").read()
    results = []

    with tempfile.TemporaryDirectory() as tmp:
        # ── 시나리오 A: 기본(스냅샷 고정) ──────────────────────────────
        dom = render(src.replace("</body>", PROBE + "\n</body>"), tmp, "a.html")
        found = re.findall(re.escape(MARK) + "(.*?)" + re.escape(MARK), dom, re.S)
        if not found:
            print("FAIL | 프로브 미실행 | 스크립트 오류 가능 — DOM에 결과 div 없음")
            return 1
        import html as _html
        results += _html.unescape(found[0]).split(" ;; ")

        # 최종 DOM(자동조회 없음 확인) — 스크립트 소스·테스트 출력 제외
        vis = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S)
        vis = re.sub(r'<div id="__fxs">.*?</div>', "", vis, flags=re.S)
        snap = ("○ 기준일 시세" in vis) and ("당일 고시" not in vis) and ("1,488" not in vis)
        results.append(("PASS" if snap else "FAIL") + " | 최종DOM 기준일 시세 고정 | 자동 시세조회 없음")

        # ── 시나리오 B: 외부 경로가 살아 있어도 고객정보가 나가지 않는가 ──
        # 워커 주소가 설정돼 있어도 AI_EXTERNAL_CALL_ENABLED=false 면 호출 자체가 없어야 한다.
        # (예전엔 '워커 다운 시 폴백'을 봤지만, 이제는 애초에 부르지 않는 것이 정상 동작이다.)
        dom_b = render(src.replace("</body>", "\n</body>"), tmp, "b.html")
        m = re.search(r'<div class="ai-out" id="ai-out">(.*?)</div>\s*<div class="guard">', dom_b, re.S)
        seg = m.group(1) if m else ""
        good = ('class="ai-reply"' in seg) and ("외부 미전송" in seg) \
            and ("Failed to fetch" not in seg) and ("⚠" not in seg)
        results.append(("PASS" if good else "FAIL")
                       + " | 첫 화면 AI 요약 = 행내 생성 | 외부 호출 없이 요약 렌더 + 미전송 표기")

    fails = [r for r in results if r.startswith("FAIL")]
    for r in results:
        print(r)
    print("\n%d개 통과 / %d개 실패" % (len(results) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    # 한글 콘솔(cp949)은 결과 문구의 '−'(U+2212) 같은 문자를 못 찍고 죽는다.
    # 검증이 다 끝난 뒤 요약을 출력하다 터지면 통과/실패 수를 못 본다.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    sys.exit(main())
