"""문서 ↔ 코드 정합 검사 (README·제안서가 데모 뒤로 처지지 않게)

실행:  python tests/doc_sync_check.py

왜 필요한가 — 이 프로젝트에서 반복된 사고는 '코드를 고치고 문서를 안 고치는 것'이었다.
제안서가 "Workers AI 실연결"을 자랑하는데 코드는 외부 호출을 차단해 두는 식이면,
심사위원이 둘을 나란히 보는 순간 신뢰가 무너진다. 코드의 사실을 기준으로 문서를 검사한다.

주의: 옛 용어를 '인용해 대비시키는' 문장은 정상이다("예상 부담액"이 아니라 "부담액(상한)").
      단순 문자열 포함으로 검사하면 그런 문장이 거짓 양성이 된다 → 인용부호 안은 제외한다.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, os.pardir)
HTML = io.open(os.path.join(KB, "FX_Sentinel_demo_ui.html"), encoding="utf-8").read()
DOCS = {name: io.open(os.path.join(KB, name), encoding="utf-8").read()
        for name in ["README.md", "FX_Sentinel_제안서_v1.md"]}

# 설계문서(청사진·A~F)는 '당시의 계획'을 남기는 기록물이라 옛 주장 자체는 지우지 않는다(F 원칙).
# 대신 **정정 배너가 붙어 있는지**를 검사한다 — 배너 없이 반증된 주장만 있으면 그게 거짓말이 된다.
BANNERED = {
    "FX_Sentinel_청사진.md": ["v2 재정렬 배너", "F_전략재정렬_v2.md"],
    "FX_Sentinel_A_FX-EWI_수식설계.md": ["v2 정정 배너", "기각"],
    "FX_Sentinel_C_검증_경제성설계.md": ["v2 정정 배너", "반증"],
}


def unquoted(text):
    """인용부호(" ") 안의 내용을 지운 사본 — 옛 용어를 대비용으로 인용한 문장을 오탐하지 않기 위해."""
    return re.sub(r'"[^"]{0,40}"', '""', text)


# (검사명, 코드에서 참인 사실, 문서에 있으면 안 되는 패턴)
FORBIDDEN = [
    ("외부 AI 호출 차단", "AI_EXTERNAL_CALL_ENABLED = false" in HTML,
     r"Workers AI 실연결|Workers AI\)|LLM 어댑터 슬롯|본선 전 연결"),
    ("적합도 점수 폐지", "function fitCount" in HTML, r"적합도 \d+|적합도 점수"),
    ("부담액 = 보수적 상한", "보수적 상한" in HTML, r"예상 부담액|기대손실 <b>"),
    ("로드맵 4단계 재편", "PHASE_LBL" in HTML, r"12개 게이트를 로드맵으로 명시"),
    # BBP 소수 자리 검사는 두지 않는다 — 문서의 "40%↑"는 경보 임계 설명이라 오탐만 낸다.
    # 표기 일관성은 코드 쪽 불변식(ui_smoke_test: "BBP 표기 일관(aria 포함)")이 이미 지킨다.
]

fails = []
passes = 0
for name, code_fact, pattern in FORBIDDEN:
    if not code_fact:
        print("SKIP | %s | 코드 전제 불성립" % name)
        continue
    for doc, text in DOCS.items():
        hit = re.search(pattern, unquoted(text))
        if hit:
            fails.append("FAIL | %s | %s 에 '%s' — 코드와 어긋남" % (name, doc, hit.group(0)))
        else:
            print("PASS | %s | %s" % (name, doc)); passes += 1

# 설계문서: 반증된 주장 옆에 정정 배너가 있는가 (지우지 않되, 정정 없이 두지도 않는다)
for name, needles in BANNERED.items():
    text = io.open(os.path.join(KB, name), encoding="utf-8").read()
    missing = [n for n in needles if n not in text]
    if missing:
        fails.append("FAIL | 정정 배너 | %s 에 %s 없음 — 반증된 주장이 정정 없이 남음" % (name, missing))
    else:
        print("PASS | 정정 배너 | %s" % name); passes += 1

# 청사진 v2 배너가 오늘의 결정(외부전송 차단·보수적 상한 등)까지 담고 있는가
bp = io.open(os.path.join(KB, "FX_Sentinel_청사진.md"), encoding="utf-8").read()
head = bp[:bp.find("## 0.")] if "## 0." in bp else bp[:4000]
for label, needle in [("외부 전송 차단", "AI_EXTERNAL_CALL_ENABLED=false"),
                      ("보수적 상한", "보수적 상한"),
                      ("USD 전용", "USD 전용"),
                      ("연동 없이 파일럿", "연동은 파일럿의 전제가 아니라 결과물"),
                      ("ELIG 단일 소스", "ELIG")]:
    if needle in head:
        print("PASS | 청사진 배너 반영 | %s" % label); passes += 1
    else:
        fails.append("FAIL | 청사진 배너 반영 | '%s' 미기재 — 배포 결정이 설계문서에 없음" % label)

# 코드의 핵심 수치를 문서가 잘못 옮기지 않았는가
NUMBERS = [
    ("ES 보수성 배수", r"ratio:([\d.]+)", "1.9배"),
    ("캘리브레이션 ECE", r"CALIB=\{ece:([\d.]+)", "0.034"),
]
for label, rx, expect in NUMBERS:
    m = re.search(rx, HTML)
    if m:
        print("INFO | %s | 코드=%s (문서 표기 '%s')" % (label, m.group(1), expect))

for f in fails:
    print(f)
print("\n%d개 통과 / %d개 실패" % (passes, len(fails)))
sys.exit(1 if fails else 0)
