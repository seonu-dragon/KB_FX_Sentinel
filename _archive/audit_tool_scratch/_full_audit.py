# -*- coding: utf-8 -*-
"""Deep audit inventory for FX_Sentinel_demo_ui.html + backend."""
import re
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "FX_Sentinel_demo_ui.html"
text = HTML.read_text(encoding="utf-8")
print("=== FILE ===")
print("chars", len(text), "lines", text.count("\n") + 1, "bytes", HTML.stat().st_size)

scripts = list(re.finditer(r"<script[^>]*>(.*?)</script>", text, re.S | re.I))
print("script_blocks", len(scripts), [len(s.group(1)) for s in scripts])
styles = list(re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
print("style_blocks", len(styles), [len(s.group(1)) for s in styles])

ids = re.findall(r'\bid="([^"]+)"', text)
print("unique_ids", len(set(ids)), "total", len(ids))
dups = [(k, v) for k, v in Counter(ids).items() if v > 1]
print("dups", dups)

funcs = re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", text)
print("functions", len(funcs))
print("func_list", ",".join(funcs))

consts = re.findall(r"const\s+([A-Z_][A-Z0-9_]*)\s*=", text)
print("CONSTS", consts)

print("data-views", sorted(set(re.findall(r'data-view="([^"]+)"', text))))
print("modes", sorted(set(re.findall(r'data-mode="([^"]+)"', text))))

print("label_for", len(re.findall(r"<label[^>]*\bfor=", text, re.I)))
print("inputs", len(re.findall(r"<(input|select|textarea)\b", text, re.I)))
print("buttons", len(re.findall(r"<button\b", text, re.I)))
print("button_no_type", len(re.findall(r"<button(?![^>]*\btype=)", text, re.I)))
print("aria_count", len(re.findall(r"aria-", text)))
print("role_count", len(re.findall(r"\brole=", text)))
print("innerHTML", text.count("innerHTML"))
print("textContent_assign", len(re.findall(r"\.textContent\s*=", text)))
print("fetch", text.count("fetch("))
print("localStorage", text.count("localStorage"))
print("media_queries", len(re.findall(r"@media", text)))
print("viewport", "viewport" in text)
print("lang", re.search(r"<html[^>]*>", text).group(0) if re.search(r"<html", text) else None)

# HTML outline: tags around body main sections
body = text
# find view divs
views = re.findall(r'id="(view-[^"]+)"', text)
print("views", views)

# feature presence
features = [
    "decision-hero", "hero-decision", "trust-strip", "calib-lite",
    "in-pay", "in-hs", "in-biz", "in-margin", "in-split",
    "recommendPackage", "renderTicket", "maskPII", "AI_EXTERNAL",
    "walk-forward", "prefers-reduced-motion", "focus-visible",
    "color-scheme", "data-theme", "themebtn", "dark",
    "serviceWorker", "manifest", "og:", "canonical",
    "Content-Security", "nonce", "strict-transport",
    "postMessage", "WebSocket", "IndexedDB",
    "print(", "window.print", "@media print",
    "keyboard", "keydown", "Escape",
    "skip", "skip-link", "sr-only",
    "error boundary", "try{", "catch",
    "ELIG", "fitCount", "primaryProduct",
    "OCR", "UNIPASS", "sanctions", "제재",
    "SSO", "RBAC", "payload",
    "global seller", "글로벌셀러",
    "CNY", "VND", "EUR", "JPY",
    "settled", "체결 후", "post-hedge", "롤오버",
    "recurring", "반복 결제",
    "onboarding", "튜토리얼", "empty-state",
    "toast", "loading", "skeleton",
    "i18n", "lang=", "en",
    "PWA", "offline",
    "unit test", "jsdom",
    "chart.js", "d3", "canvas",
    "pdf", "PDF", "blob:",
    "clipboard", "navigator.clipboard",
    "history.pushState", "hashchange",
    "IntersectionObserver",
    "ResizeObserver",
    "debounce", "throttle",
    "zod", "schema",
    "XSS", "DOMPurify",
    "esc(", "function esc",
]
print("\n=== FEATURE COUNTS ===")
for f in features:
    print(f"{f}\t{text.count(f)}")

# AI external flag
m = re.search(r"AI_EXTERNAL_CALL_ENABLED\s*=\s*([^;]+)", text)
print("\nAI_EXTERNAL", m.group(0) if m else "NOT FOUND")
m = re.search(r"FX_ADVISOR_PROXY\s*=\s*([^;]+)", text)
print("FX_ADVISOR_PROXY", m.group(0) if m else "NOT FOUND")
m = re.search(r"FX_RATES_PROXY\s*=\s*([^;]+)", text)
print("FX_RATES_PROXY", m.group(0) if m else "NOT FOUND")
m = re.search(r"RM_PASS\s*=\s*([^;]+)", text)
print("RM_PASS", m.group(0) if m else "NOT FOUND")

# PRESETS count
presets = re.search(r"const PRESETS\s*=\s*(\[[\s\S]*?\]);", text)
if presets:
    # rough count of name fields
    names = re.findall(r'name:\s*"([^"]+)"', presets.group(1))
    print("PRESET names", names)

# KB products
prods = re.search(r"const KB_PRODUCTS\s*=\s*(\[[\s\S]*?\n\]);", text)
if prods:
    titles = re.findall(r't:\s*"([^"]+)"', prods.group(1))
    print("KB_PRODUCTS count", len(titles))
    for t in titles[:30]:
        print("  -", t)

# COUNTRY keys
country = re.search(r"const COUNTRY\s*=\s*(\{[\s\S]*?\n\});", text)
if country:
    keys = re.findall(r'"([^"]+)":\s*\{', country.group(1))
    print("COUNTRY keys", keys)

# TRADE_STATS
print("TRADE_STATS", "TRADE_STATS" in text or "tradestat" in text)
ts = re.findall(r'["\'](\d{4})["\']', text)
print("HS-like codes sample", sorted(set(ts))[:20])

# Error handling patterns
print("\n=== ERROR / VALIDATION ===")
print("aria-invalid", text.count("aria-invalid"))
print("err-", len(re.findall(r'id="err-', text)) + text.count("err-"))
print("입력을 확인해", text.count("입력을 확인해"))
print("NaN", text.count("NaN"))

# Mobile / a11y
print("\n=== A11Y / MOBILE ===")
print("touch-action", text.count("touch-action"))
print("min-height:44", text.count("44px") + text.count("min-height:44"))
print("prefers-contrast", text.count("prefers-contrast"))
print("prefers-reduced-motion", text.count("prefers-reduced-motion"))
print("forced-colors", text.count("forced-colors"))
print("outline", text.count("outline"))
print("tabindex", text.count("tabindex"))
print("aria-live", text.count("aria-live"))
print("aria-expanded", text.count("aria-expanded"))
print("aria-controls", text.count("aria-controls"))
print("aria-selected", text.count("aria-selected"))
print("aria-current", text.count("aria-current"))
print("aria-busy", text.count("aria-busy"))
print("aria-hidden", text.count("aria-hidden"))

# Security
print("\n=== SECURITY ===")
print("eval", bool(re.search(r"\beval\s*\(", text)))
print("document.write", "document.write" in text)
print("innerHTML with user", "innerHTML" in text)
print("https://", len(re.findall(r"https://", text)))
print("http://", len(re.findall(r"http://", text)))
print("ALLOW_ORIGIN", "ALLOW_ORIGIN" in text)

# Extract JS section size ratio
js_chars = sum(len(s.group(1)) for s in scripts)
css_chars = sum(len(s.group(1)) for s in styles)
# font data urls
fonts = len(re.findall(r"data:font|data:application/font|base64,", text))
print("\n=== SIZE ===")
print("js_chars", js_chars, "css_chars", css_chars)
print("base64_hits", fonts)
print("approx_html_markup", len(text) - js_chars - css_chars)

# List deep tabs
print("\n=== DEEP TABS ===")
for m in re.finditer(r'data-deep="([^"]+)"[^>]*>([^<]+)', text):
    print(m.group(1), m.group(2).strip())

# Search for known product gaps from design docs
gaps = [
    "외화예금", "외화대출", "수입유산스", "수출환어음", "네고", "Nego",
    "Payment Usance", "특별출연", "글로벌셀러", "환변동보험",
    "수출바우처", "신용장", "D/A", "D/P", "O/A",
    "롤오버", "부분해지", "실수요", "외국환거래법",
    "감사로그", "모델버전", "ECE", "Brier", "PBO", "DSR",
    "before/after", "파일럿 KPI", "권한 매트릭스",
    "SSO", "원장", "CRM", "UNIPASS",
]
print("\n=== DOMAIN TERMS ===")
for g in gaps:
    print(f"{g}\t{text.count(g)}")

# Backend python inventory
print("\n=== PYTHON MODULES ===")
fx = ROOT / "fx_sentinel"
for p in sorted(fx.glob("*.py")):
    t = p.read_text(encoding="utf-8", errors="replace")
    print(f"{p.name}\tlines={t.count(chr(10))+1}\tbytes={p.stat().st_size}")

# Tests
print("\n=== TESTS ===")
for p in sorted((ROOT / "tests").glob("*.py")):
    t = p.read_text(encoding="utf-8", errors="replace")
    print(f"{p.name}\tlines={t.count(chr(10))+1}")
    # count ok( in smoke
    if "ui_smoke" in p.name:
        print("  ok( calls", t.count("ok("))

# Compare bbp formulas
print("\n=== BBP PARITY CHECK ===")
# extract JS bbpProb body snippet
m = re.search(r"function bbpProb\((.*?)\{([\s\S]*?)\n\}", text)
if m:
    body = m.group(2)
    print("js_bbp_has_iz", "iz" in body)
    print("js_bbp_has_tcdf", "tcdf" in body)
    print("js_bbp_snippet", body[:400].replace("\n", " | "))

# Check if Python agents wired to HTML
print("\n=== AGENT WIRING ===")
for term in ["Sentinel", "Analyst", "Advisor", "Hedge", "4-agent", "에이전트"]:
    print(term, text.count(term))

# Customer first screen elements
print("\n=== CUSTOMER FIRST SCREEN ===")
for term in ["verdict", "easy-terms", "쉬운 용어", "glossary", "onboard", "help tip", "툴팁", "tooltip"]:
    print(term, text.count(term))

print("\nDONE")
