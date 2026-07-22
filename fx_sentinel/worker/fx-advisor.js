// FX Sentinel — LLM Advisor (Cloudflare Worker + Workers AI)
// ─────────────────────────────────────────────────────────────
// 데모의 결정론 룰엔진(BBP·상품 라우팅)이 만든 컨텍스트를 받아
// KB Star FX 수출입금융 "AI 어드바이저"가 자연어 브리핑을 생성한다.
// 예측/투자권유가 아니라, 이미 계산된 리스크·상품 후보를 설명·정리한다.
//
// 배포: Cloudflare Workers(무료) + Workers AI 바인딩 변수명 "AI"
//   (Mir_US_Stocks/worker와 동일한 무료 바인딩을 그대로 사용)
// 엔드포인트:  POST /advisor   ·  GET /rates (실시간 환율)  ·  GET / (헬스체크)
//   /rates 는 무료·무키 환율 API를 프록시(CORS 해결)해 KRW 페어로 변환한다.

// CORS 허용 출처. 운영 배포는 Worker 환경변수 ALLOW_ORIGINS 에 쉼표로 나열한다.
//   예) ALLOW_ORIGINS="https://<your-site>,https://<user>.github.io"
// 설정하면 요청 Origin 이 목록에 있을 때만 그 Origin 을 되돌려준다(에코). 없으면 차단.
// 미설정이면 "*" — 데모를 file:// 로 열면 Origin 이 "null" 이라 allowlist 로는 시세
// 프록시가 죽는다. 데모 이식성 우선이라 기본값은 열어두되, 운영은 env 한 줄로 잠근다.
const DEFAULT_ALLOW_ORIGIN = "*";

function allowedOrigin(request, env) {
  const list = ((env && env.ALLOW_ORIGINS) || "").split(",").map(s => s.trim()).filter(Boolean);
  if (!list.length) return DEFAULT_ALLOW_ORIGIN;
  const origin = request.headers.get("Origin");
  return list.indexOf(origin) >= 0 ? origin : null;  // null = 헤더 미부여 → 브라우저가 차단
}
// 큰 모델이 한국어 지시를 훨씬 잘 따름. 앞에서부터 성공하는 첫 모델 사용.
// 무료 플랜에서 뉴런 예산이 빠듯하면 8B 하나만 남겨도 됨.
const MODELS = [
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  "@cf/mistralai/mistral-small-3.1-24b-instruct",
  "@cf/meta/llama-3.1-8b-instruct",
];

export default {
  async fetch(request, env) {
    const origin = allowedOrigin(request, env);
    if (request.method === "OPTIONS") return cors(new Response(null, { status: 204 }), origin);
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/advisor") {
      return cors(await handleAdvisor(request, env), origin);
    }
    if (request.method === "GET" && url.pathname === "/rates") {
      return cors(await handleRates(), origin);
    }
    // 헬스체크 (version 으로 재배포 여부 확인)
    return cors(json({ ok: true, service: "fx-sentinel-advisor", version: "1.2-cors-allowlist", ai: !!(env && env.AI), features: ["rates", "advisor", "input-sanitize", "output-guardrail", "cors-allowlist"], endpoints: ["POST /advisor", "GET /rates"] }), origin);
  },
};

// ── 실시간 환율(무료·무키 API 프록시 → KRW 페어) ──────────────────────
// spots = CUR.spot 의미와 동일(원/단위통화, JPY·VND 는 100단위).
async function handleRates() {
  // 1차: open.er-api.com (무료·키없음·CORS)
  try {
    const r = await fetch("https://open.er-api.com/v6/latest/USD", { cf: { cacheTtl: 600, cacheEverything: true } });
    const d = await r.json();
    if (d && d.result === "success" && d.rates && d.rates.KRW) {
      return ratesJson(d.rates, "open.er-api.com", d.time_last_update_utc);
    }
  } catch (e) {}
  // 2차: exchangerate.host
  try {
    const r = await fetch("https://api.exchangerate.host/latest?base=USD&symbols=KRW,EUR,JPY,CNY,VND", { cf: { cacheTtl: 600, cacheEverything: true } });
    const d = await r.json();
    if (d && d.rates && d.rates.KRW) {
      return ratesJson(d.rates, "exchangerate.host", d.date);
    }
  } catch (e) {}
  return json({ ok: false, error: "rate_source_unavailable" }, 200);
}
function ratesJson(rates, source, asof) {
  const K = rates.KRW;
  const spots = {
    USD: round(K, 2),
    EUR: rates.EUR ? round(K / rates.EUR, 2) : null,
    JPY: rates.JPY ? round((K / rates.JPY) * 100, 2) : null,
    CNY: rates.CNY ? round(K / rates.CNY, 3) : null,
    VND: rates.VND ? round((K / rates.VND) * 100, 4) : null,
  };
  return new Response(JSON.stringify({ ok: true, base: "USD", source, asof: String(asof || "").slice(0, 16), spots }), {
    status: 200,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=300" },
  });
}
const round = (v, n) => Math.round(v * Math.pow(10, n)) / Math.pow(10, n);

const SYSTEM_PROMPT = `당신은 KB국민은행 "KB Star FX" 수출입 금융 AI 어드바이저입니다.
회사의 거래 정보와, 시스템이 이미 계산한 정량 지표·상품 후보를 받아 담당자에게 **자연어 브리핑**을 작성합니다.

[역할]
- 환율을 예측하지 않습니다. 이미 산출된 값(BBP=예산환율 초과 가능성, 기대손실, 상품 후보)을 쉽게 설명·정리합니다.
- 중소기업·개인사업자 담당자가 "지금 상황이 어떤지, 오늘 무엇을 준비/확인하면 되는지"를 이해하도록 돕습니다.

[반드시 지킬 규칙]
- 제공된 사실(FACTS)만 사용하세요. 금리·수수료·한도 등 구체 수치를 지어내지 마세요.
- 파생상품(선물환·옵션)은 "상담 후보"로만 안내하고, 매수/매도·투자·수익보장 표현을 쓰지 마세요.
- 최종 상품 조건·자격·계약은 반드시 "KB 영업점/RM 확인 후 확정"이라고 명시하세요.
- 1순위 KB 상품을 왜 추천하는지 근거를 1~2개로 짧게 설명하세요.
- 존댓말, 담백하게. 마크다운/이모지 없이 3~5문장. 과장·불안 조성 금지.

[출력 형식]
한 단락(3~5문장)으로:
① 지금 리스크 한 줄 요약 → ② 1순위 KB 상품과 추천 이유 → ③ 헤지 등 참고(상담 후보) → ④ 오늘 준비/확인할 것 → ⑤ "최종 확인·계약은 KB 영업점" 한 줄.`;

async function handleAdvisor(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return json({ error: "bad_json" }, 400);
  }
  if (!env || !env.AI) {
    return json({ reply: "", error: "no_ai_binding", hint: "Worker Settings → Bindings → Workers AI (변수명 AI) 추가 후 Deploy" }, 200);
  }

  const facts = buildFacts(body);
  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: `아래 FACTS만 근거로 브리핑을 작성하세요.\n\n[FACTS]\n${facts}` },
  ];

  let lastError = "no_model";
  for (const model of MODELS) {
    try {
      const result = await env.AI.run(model, { messages, max_tokens: 512, temperature: 0.3 });
      const text = String((result && result.response) || "").trim();
      if (text) {
        // 출력 가드레일 — 수익보장·투자권유 표현이 섞이면 차단
        if (BAD_OUTPUT.test(text)) {
          return json({ reply: "요약 결과가 가이드라인(수익보장·투자권유 표현 금지)에 저촉되어 자동 차단되었습니다. 담당 RM 상담을 이용해 주세요.", model, blocked: true });
        }
        return json({ reply: text, model });
      }
      lastError = `empty_response:${model}`;
    } catch (e) {
      lastError = `${model}: ${(e && e.message) || e}`;
    }
  }
  return json({ reply: "", error: lastError }, 200);
}

// 출력 금지어(가드레일) — 수익보장·투자권유·확정예측 표현
const BAD_OUTPUT = /수익\s*을?\s*보장|원금\s*보장|손실\s*없|반드시\s*(오르|올라|내리)|무조건|매수\s*하세요|매도\s*하세요|투자를?\s*(추천|권유)|확실(한|히)\s*수익|보장된?\s*수익/;

// 프론트가 보낸 계산 결과를 LLM이 오해 없이 읽도록 평문 FACTS로 직렬화
function buildFacts(b) {
  const p = (b && b.profile) || {};
  const r = (b && b.risk) || {};
  const h = (b && b.hedge) || {};
  const products = Array.isArray(b && b.products) ? b.products.slice(0, 6) : [];
  const posK = p.pos === "import" ? "수입" : "수출";
  const bizK = p.biz === "sole" ? "개인사업자" : "법인(중소기업)";
  const L = [];
  L.push(`- 회사: ${str(p.name)} (${bizK})`);
  L.push(`- 거래: ${posK} · 거래국 ${str(p.country)} · 결제방식 ${str(p.pay)} · ${num(p.amount)} ${str(p.currency)} · 결제만기 ${num(p.horizon)}영업일`);
  L.push(`- 회사 기준환율: ${num(p.budget)}원`);
  L.push(`- BBP(예산환율 초과 가능성): ${pct(r.bbp)} · 이탈 시 기대손실: ${won(r.expectedLossKRW)}`);
  L.push(`- 시장 위험게이지(FX-EWI): ${num(r.ewi)}/100 · 거래국 구조 리스크: ${str(r.macroGrade)} · 능동 헤지 구간 여부: ${r.alert ? "예" : "아니오"}`);
  if (h && h.inst) L.push(`- 참고 헤지(상담 후보): ${str(h.inst)} ${Math.round((h.ratio || 0) * 100)}%`);
  if (products.length) {
    L.push(`- KB 상품 후보(룰엔진 판정, 위쪽이 상위):`);
    products.forEach((x, i) => {
      const why = Array.isArray(x.why) ? x.why.join(" / ") : "";
      L.push(`  ${i + 1}) [${str(x.st)}] ${str(x.t)} (${str(x.cat)} · ${str(x.src)})${why ? " — " + why : ""}`);
    });
  }
  return L.join("\n").slice(0, 3500);
}

// 입력 sanitize — 프롬프트 인젝션 방어(개행·지시문 제거, 길이 제한)
const str = (v) => {
  if (v == null || v === "") return "미상";
  return String(v)
    .replace(/[\r\n\t]+/g, " ")
    .replace(/ignore\s+(all\s+)?previous|disregard\s+above|system\s*prompt|assistant\s*:|당신은\s*이제|이전\s*지시\s*무시/gi, "")
    .slice(0, 120);
};
const num = (v) => (isFinite(+v) ? (+v).toLocaleString() : "미상");
const pct = (v) => (isFinite(+v) ? (+v).toFixed(1) + "%" : "미상");
const won = (v) => {
  const n = +v;
  if (!isFinite(n)) return "미상";
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + "억원";
  return Math.round(n / 1e4).toLocaleString() + "만원";
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}
function cors(resp, origin) {
  const r = new Response(resp.body, resp);
  // origin 이 null 이면 ACAO 를 아예 안 붙인다 — 브라우저가 응답을 버린다.
  if (origin) r.headers.set("Access-Control-Allow-Origin", origin);
  r.headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  r.headers.set("Access-Control-Allow-Headers", "Content-Type");
  // allowlist 운영 시 Origin 별로 응답이 달라지므로 캐시 오염을 막는다.
  r.headers.set("Vary", "Origin");
  return r;
}
