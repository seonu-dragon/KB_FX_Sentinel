# -*- coding: utf-8 -*-
"""Deep audit of FX_Sentinel_demo_ui.html"""
from pathlib import Path
import re
from collections import Counter

ROOT = Path(__file__).resolve().parent
html_path = ROOT / "FX_Sentinel_demo_ui.html"
text = html_path.read_text(encoding="utf-8")
lines = text.splitlines()
out = []

def w(s=""):
    out.append(s)

w(f"FILE: {html_path.name}")
w(f"lines={len(lines)} chars={len(text)} bytes={html_path.stat().st_size}")
w(f"title={re.search(r'<title>(.*?)</title>', text).group(1)}")
desc = re.search(r'name="description" content="(.*?)"', text)
w(f"description={desc.group(1) if desc else None}")

# encoding corruption markers
corrupt = sum(1 for l in lines if "\ufffd" in l or "??" in l[:80])
w(f"lines_with_replacement_or_qq={corrupt}")

# external deps
w(f"external_css={bool(re.search(r'<link[^>]+stylesheet', text))}")
w("external_script_src=" + str(re.findall(r'src=["\']([^"\']+)["\']', text)))
w(f"inline_style_blocks={len(re.findall(r'<style', text, re.I))}")
w(f"inline_script_blocks={len(re.findall(r'<script', text, re.I))}")

# ids
ids = re.findall(r'\bid="([^"]+)"', text)
w(f"unique_ids={len(set(ids))} total_id_attrs={len(ids)}")
dups = [(k, v) for k, v in Counter(ids).items() if v > 1]
w(f"duplicate_ids={dups}")

# views / nav
views = sorted(set(re.findall(r'data-view="([^"]+)"', text)))
w("data-views=" + str(views))
mode_hits = re.findall(r'data-mode="([^"]+)"', text) + re.findall(r'setMode\("([^"]+)"', text)
w("mode_hits=" + str(sorted(set(mode_hits))))

# key string inventory
keys = [
    "BBP", "예산환율", "상담 요청", "RM", "OCR", "금소법", "감사로그",
    "Student-t", "캘리브레이션", "PBO", "ECE", "Brier", "KB Payment",
    "Usance", "선물환", "K-SURE", "passcode", "1234", "PDF",
    "focus-visible", "prefers-reduced-motion", "aria-", "role=",
    "FX_ADVISOR_PROXY", "FX_RATES_PROXY", "localStorage", "fetch(",
    "internal-only", "customer-only", "judge", "고객", "심사",
    "적합성", "판정표", "권한", "payload", "SSO", "파일럿",
    "walk-forward", "부분헤지", "HS", "신용장", "L/C", "LC",
    "정책자금", "보증", "보험", "수출바우처", "UNIPASS", "관세",
    "제재", "AML", "sanctions", "reduced-motion", "touch-action",
    "meta name=\"robots\"", "canonical", "og:", "manifest", "serviceWorker",
    "https://", "http://", "file://", "eval(", "innerHTML", "document.write",
]
w("--- KEY STRING COUNTS ---")
for k in keys:
    w(f"  {k!r}: {text.count(k)}")

# functions
fns = re.findall(r"function\s+(\w+)\s*\(", text)
w(f"--- FUNCTIONS ({len(fns)}) ---")
w(", ".join(fns))

# proxy constants
for m in re.finditer(r"(const|let|var)\s+(FX_[A-Z_]+|RM_PASS|MARKET|REGIMES|CUR|COUNTRY|PRESETS|KB_PRODUCTS)\s*=\s*(.{0,120})", text):
    w(f"CONST {m.group(2)} = {m.group(3)[:100]}")

# body outline before script
si = next(i for i, l in enumerate(lines) if re.search(r"<script\b", l) and "src=" not in l)
w(f"--- HTML OUTLINE before script (line 1-{si}) ---")
for i, l in enumerate(lines[:si], 1):
    if re.search(
        r"<h[1-3]|class=\"sec-h|class=\"nav|data-view|class=\"modebtn|id=\"view-|stepper|footer|disclaimer|금소|거버넌|감사|로드맵|KPI|OCR|프리셋|shell|sidebar|class=\"view",
        l,
    ):
        clean = re.sub(r"\s+", " ", l).strip()
        if len(clean) > 10:
            # strip tags partially for readability
            plain = re.sub(r"<[^>]+>", " ", clean)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                w(f"{i}: {plain[:130]}")

# form fields
w("--- FORM INPUTS ---")
for m in re.finditer(r"<(input|select|textarea)([^>]*)>", text):
    attrs = m.group(2)
    iid = re.search(r'id="([^"]+)"', attrs)
    itype = re.search(r'type="([^"]+)"', attrs)
    lab = re.search(r'placeholder="([^"]*)"', attrs)
    w(f"  {m.group(1)} id={iid.group(1) if iid else '-'} type={itype.group(1) if itype else '-'} ph={lab.group(1) if lab else '-'}")

# a11y quick
w("--- A11Y ---")
w(f"aria- attrs total substr={text.count('aria-')}")
w(f"role= count={text.count('role=')}")
w(f"label for= count={len(re.findall(r'<label[^>]*for=', text))}")
w(f"button without type={len(re.findall(r'<button(?![^>]*type=)[^>]*>', text))}")
w(f"focus-visible={text.count('focus-visible')}")
w(f"prefers-reduced-motion={text.count('prefers-reduced-motion')}")
w(f"tabindex={text.count('tabindex')}")

# security
w("--- SECURITY ---")
w(f"RM_PASS hardcoded present={'RM_PASS' in text}")
w(f"passcode 1234 present={'1234' in text}")
w(f"ALLOW not in html; worker separate")
w(f"innerHTML assignments approx={len(re.findall(r'innerHTML\s*=', text))}")
w(f"esc( function present={'function esc' in text or 'esc=' in text}")

# checklist items from 2026-07-12 - presence check
w("--- CHECKLIST FEATURE PRESENCE ---")
checks = {
    "customer/internal mode toggle": "setMode" in text and "internal" in text and "customer" in text or "고객" in text,
    "internal-only class": "internal-only" in text,
    "customer-only class": "customer-only" in text,
    "RM passcode gate": "RM_PASS" in text,
    "BBP gauge": 'id="bbp"' in text,
    "FX-EWI": "ewi" in text.lower() or "FX-EWI" in text,
    "OCR": "OCR" in text or "runOCR" in text,
    "audit log": "logAudit" in text,
    "export audit": "exportAudit" in text,
    "mask PII": "maskPII" in text,
    "AI advisor proxy": "FX_ADVISOR_PROXY" in text,
    "rates proxy": "FX_RATES_PROXY" in text or "refreshRates" in text,
    "multi view shell": "showView" in text,
    "dashboard view": "renderDashboard" in text,
    "products view": "renderProducts" in text,
    "trades view": "renderTrades" in text,
    "policy funds": "renderPolicy" in text or "정책" in text,
    "support/help": "renderSupport" in text,
    "notifications": "renderNotifs" in text,
    "report": "renderReport" in text,
    "tariff": "tariff" in text.lower() or "관세" in text,
    "country risk": "cty-" in text or "COUNTRY" in text,
    "governance": "거버넌" in text or "금소법" in text,
    "before/after KPI": "renderBeforeAfter" in text or "renderKPI" in text,
    "timeline next steps": "renderNextSteps" in text,
    "product package": "recommendPackage" in text,
    "suitability matrix": "적합성" in text or "판정표" in text,
    "permission matrix": "권한 매트릭스" in text or "역할" in text and "준법" in text,
    "KB SSO sequence": "SSO" in text,
    "pilot measurement": "측정식" in text or "파일럿 측정" in text,
    "simple vs advanced form": "간편" in text or "고급 설정" in text,
    "KB 상담 요청하기 CTA": "KB 상담 요청" in text or "상담 요청하기" in text,
}
for k, v in checks.items():
    w(f"  [{'Y' if v else 'N'}] {k}")

# compare market snapshot vs python state
demo_json = ROOT / "fx_sentinel" / "state" / "demo_ui_data.json"
if demo_json.exists():
    import json
    d = json.loads(demo_json.read_text(encoding="utf-8"))
    w(f"demo_ui_data market.spot={d['market']['spot']} date={d['market']['date']} ewi={d['market']['ewi']}")
    w(f"demo companies={len(d.get('companies',[]))}")

# extract MARKET from html
mm = re.search(r"const MARKET\s*=\s*(\{[^;]+\});", text, re.S)
if mm:
    w("HTML MARKET snippet: " + re.sub(r"\s+", " ", mm.group(1))[:200])

# mobile media queries
w(f"@media rules count={len(re.findall(r'@media', text))}")
for m in re.finditer(r"@media[^{]+\{", text):
    w("  " + m.group(0)[:80])

# size of css vs js
style_m = re.search(r"<style>(.*?)</style>", text, re.S)
script_m = re.search(r"<script>(.*?)</script>\s*</body>", text, re.S)
if style_m:
    w(f"CSS chars={len(style_m.group(1))}")
if script_m:
    w(f"JS chars={len(script_m.group(1))}")

# list md docs
w("--- DOCS ---")
for p in sorted(ROOT.glob("*.md")):
    w(f"  {p.name} ({p.stat().st_size} bytes)")

report = ROOT / "_audit_report.txt"
report.write_text("\n".join(out), encoding="utf-8")
print(f"Wrote {report} ({len(out)} lines)")
