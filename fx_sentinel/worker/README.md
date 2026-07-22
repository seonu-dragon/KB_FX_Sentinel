# FX Sentinel — Worker (LLM Advisor + 실시간 환율)

이 Worker는 두 가지를 제공합니다.

1. **`POST /advisor`** — 결정론 룰엔진(BBP·상품 라우팅) 결과를 **Workers AI(무료 바인딩)** 로 넘겨 **자연어 브리핑**을 생성. 예측·투자권유가 아니라 이미 계산된 리스크·상품 후보를 담당자에게 쉽게 **설명·정리**.
2. **`GET /rates`** — 무료·무키 환율 API(`open.er-api.com`, 실패 시 `exchangerate.host`)를 **프록시**해 KRW 페어로 변환. 로컬 `file://` 데모의 **CORS 문제를 워커가 대신 해결**하므로, 데모가 실시간 환율을 받아 화면을 갱신할 수 있습니다.

> Mir_US_Stocks/worker 와 **동일한 무료 Workers AI 바인딩(`AI`)** 을 그대로 씁니다. 추가 비용·API 키 없음. `/rates` 는 AI 바인딩 없이도 동작합니다.

## 배포 (1회, 무료)

1. https://dash.cloudflare.com → **Workers & Pages** → **Create** → **Worker**
   - 이름 예: `fx-sentinel` → 배포 주소 `https://fx-sentinel.<본인계정>.workers.dev`
2. 템플릿 코드를 지우고 [`fx-advisor.js`](./fx-advisor.js) 내용을 붙여넣은 뒤 **Deploy**
3. **Workers AI 바인딩 추가**:
   워커 → **Settings** → **Bindings** → **Add** → **Workers AI** → 변수 이름 **`AI`** → **Deploy**
   - 바인딩이 없으면 `{ "error": "no_ai_binding" }` 이 돌아옵니다.
4. 배포 주소를 데모에 연결 — `FX_Sentinel_demo_ui.html` 상단 스크립트의
   ```js
   const FX_ADVISOR_PROXY = ""; // ← 여기에 배포 주소를 넣으세요
   ```
   를 본인 주소로 변경:
   ```js
   const FX_ADVISOR_PROXY = "https://fx-sentinel.planbesides.workers.dev";
   ```
   (Mir와 같은 계정이면 서브도메인은 `planbesides.workers.dev`)
   - **실시간 환율**도 같은 워커의 `/rates` 로 동작합니다. 별도 설정 불필요 —
     데모의 `FX_RATES_PROXY` 는 비워두면 자동으로 `FX_ADVISOR_PROXY` 를 씁니다.
     (advisor 없이 환율만 쓰려면 `FX_RATES_PROXY` 에만 워커 주소를 넣어도 됩니다.)

## 동작

### 실시간 환율 — `GET https://<worker>/rates`
```json
{ "ok": true, "base": "USD", "source": "open.er-api.com", "asof": "Tue, 14 Jul 2026",
  "spots": { "USD": 1372.5, "EUR": 1495.3, "JPY": 905.4, "CNY": 190.2, "VND": 5.41 } }
```
- `spots` 는 데모 `CUR.spot` 과 동일 의미(원/단위통화, **JPY·VND 는 100단위**).
- 데모는 로드 시 이 값으로 `CUR`·`MARKET.spot` 을 갱신하고 화면을 재계산합니다.
- 워커 미설정·조회 실패 시 데모는 **데모 스냅샷으로 자동 폴백**(사이드바에 `○ 스냅샷` 표시).
- 소스는 무료 API라 **일별 고시** 기준입니다(인트라데이 아님). 캐시 5분.

### AI 브리핑 — `POST https://<worker>/advisor`
  ```json
  {
    "profile": { "name":"나래상사","pos":"import","biz":"corp","country":"베트남",
                 "currency":"USD","pay":"TT","amount":400000,"horizon":63,"budget":1500 },
    "risk":    { "bbp":64.3,"expectedLossKRW":18330000,"ewi":38,"macroGrade":"보통","alert":true },
    "products":[ { "t":"KB Payment Usance","cat":"수입금융","st":"추천","src":"KB 공식상품",
                   "why":["수입 확정 건 · T/T · USD","6개월 이내"] } ],
    "hedge":   { "inst":"선물환","ratio":0.5 }
  }
  ```
- 응답: `{ "reply": "…자연어 브리핑…", "model": "@cf/…" }`
- 모델: `llama-3.3-70b-fp8-fast → mistral-24b → llama-3.1-8b` 순으로 폴백.
- 헬스체크: `GET https://<worker>/` → `{ ok:true, ai:true|false }`

## 가드레일 (시스템 프롬프트에 내장)

- 제공된 FACTS만 근거 사용 — 금리·수수료·한도 수치 지어내기 금지
- 파생상품은 "상담 후보"로만, 투자권유·수익보장 표현 금지
- 최종 상품 조건·계약은 "KB 영업점/RM 확인 후 확정" 명시
- 3~5문장, 마크다운/이모지 없음

## 보안(선택)

CORS 허용 출처는 **코드가 아니라 Worker 환경변수**로 제한합니다. `ALLOW_ORIGINS` 에 쉼표로 나열하면,
요청 `Origin` 이 목록에 있을 때만 그 값을 되돌려주고(에코) 나머지는 `Access-Control-Allow-Origin`
헤더 자체를 안 붙여 브라우저가 차단합니다.

```bash
wrangler secret put ALLOW_ORIGINS
# https://<your-site>,https://<user>.github.io
```

미설정 시 기본값은 `*` 입니다. 데모 HTML 을 `file://` 로 열면 `Origin` 이 `null` 이라
allowlist 를 켜면 시세 프록시가 막히기 때문에, **데모 이식성을 위해 기본은 열어두고 운영 배포에서만 잠급니다.**
운영 전환 시에는 위 secret 설정이 필수입니다.
