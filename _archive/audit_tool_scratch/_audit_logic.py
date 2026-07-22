# -*- coding: utf-8 -*-
"""Extract and critique JS business logic snippets from demo UI."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
text = (ROOT / "FX_Sentinel_demo_ui.html").read_text(encoding="utf-8")
lines = text.splitlines()
out = []

def extract_fn(name, max_lines=80):
    pat = re.compile(rf"function\s+{name}\s*\(")
    for i, l in enumerate(lines):
        if pat.search(l):
            block = []
            depth = 0
            started = False
            for j in range(i, min(i + max_lines * 3, len(lines))):
                block.append(f"{j+1}: {lines[j]}")
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
            return "\n".join(block[:max_lines])
    return f"(not found: {name})"

for fn in [
    "bbpProb", "expShortfall", "hedgeRatio", "triage", "compute",
    "recommendPackage", "primaryProduct", "generateAdvisorBrief",
    "localBrief", "runOCR", "refreshRates", "setMode", "requestMode",
    "renderAdvisor", "renderNextSteps", "applyGate", "maskPII",
]:
    out.append("=" * 60)
    out.append(fn)
    out.append("=" * 60)
    out.append(extract_fn(fn, 100 if fn in ("compute", "recommendPackage", "triage") else 50))
    out.append("")

# PRESETS / MARKET / COUNTRY / REGIMES
for name in ["MARKET", "REGIMES", "CUR", "COUNTRY", "PRESETS", "KB_PRODUCTS"]:
    m = re.search(rf"(?:const|var|let)\s+{name}\s*=\s*", text)
    if not m:
        out.append(f"{name}: NOT FOUND")
        continue
    start = m.end()
    # take next 40 lines from that point
    pos = text[:start].count("\n")
    out.append("=" * 60)
    out.append(name)
    out.append("=" * 60)
    for j in range(pos, min(pos + 45, len(lines))):
        out.append(f"{j+1}: {lines[j]}")
        if j > pos and lines[j].strip() in (");", "};", "],") and j - pos > 3:
            # weak end detect
            if name != "KB_PRODUCTS":
                break
    out.append("")

(ROOT / "_audit_logic.txt").write_text("\n".join(out), encoding="utf-8")
print("wrote logic extract", len(out))
