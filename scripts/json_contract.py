#!/usr/bin/env python3
"""Validate an LLM response against a JSON schema subset and emit a precise repair prompt."""
import argparse
import json
import os
import re
import sys

TYPES = {"object": dict, "array": list, "string": str, "number": (int, float),
         "integer": int, "boolean": bool, "null": type(None)}


def extract(text):
    """Pull JSON out of a model response. Returns (value, notes) or (None, notes)."""
    notes = []
    raw = text.strip()
    if raw.startswith("```"):
        raw2 = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw2 = re.sub(r"\s*```$", "", raw2)
        if raw2 != raw:
            notes.append("stripped a markdown code fence")
        raw = raw2
    attempts = []
    candidates = [raw]
    for start in ("{", "["):
        i = raw.find(start)
        if i > 0:
            candidates.append(raw[i:])
    for cand in candidates[:1]:
        attempts.append(cand)
    for start in ("{", "["):
        i = raw.find(start)
        if i > 0:
            j = max(raw.rfind("}"), raw.rfind("]"))
            if j > i:
                attempts.append(raw[i:j + 1])
    fixed_variants = []
    for c in attempts:
        fixed_variants.append(c)
        no_trail = re.sub(r",\s*([}\]])", r"\1", c)
        if no_trail != c:
            fixed_variants.append(no_trail)
        py_lits = no_trail.replace("True", "true").replace("False", "false").replace("None", "null")
        if py_lits != no_trail:
            fixed_variants.append(py_lits)
        smart = py_lits.replace("\u201c", '"').replace("\u201d", '"')
        if smart != py_lits:
            fixed_variants.append(smart)
    for c in fixed_variants:
        try:
            return json.loads(c), notes + (["removed trailing commas"] if ", }" in c or ", ]" in c else [])
        except json.JSONDecodeError:
            continue
    opens, closes = raw.count("{") + raw.count("["), raw.count("}") + raw.count("]")
    if opens != closes:
        notes.append("brace count %d open / %d close — the response looks truncated" % (opens, closes))
    return None, notes


def jtype(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def type_ok(v, want):
    if want == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if want == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    return isinstance(v, TYPES.get(want, object))


def validate(inst, sch, path="$"):
    errs = []
    if not isinstance(sch, dict):
        return errs
    t = sch.get("type")
    if t:
        want = [t] if isinstance(t, str) else list(t)
        if not any(type_ok(inst, w) for w in want):
            errs.append({"path": path, "code": "TYPE", "expected": "|".join(want),
                         "actual": jtype(inst), "detail": "wrong JSON type"})
            return errs
    if "enum" in sch and inst not in sch["enum"]:
        errs.append({"path": path, "code": "ENUM", "expected": json.dumps(sch["enum"])[:120],
                     "actual": json.dumps(inst)[:60], "detail": "value is not an allowed option"})
    if isinstance(inst, str):
        if "pattern" in sch and not re.search(sch["pattern"], inst):
            errs.append({"path": path, "code": "PATTERN", "expected": sch["pattern"],
                         "actual": inst[:60], "detail": "string does not match the required pattern"})
        if "minLength" in sch and len(inst) < sch["minLength"]:
            errs.append({"path": path, "code": "MIN_LENGTH", "expected": str(sch["minLength"]),
                         "actual": str(len(inst)), "detail": "string too short"})
        if "maxLength" in sch and len(inst) > sch["maxLength"]:
            errs.append({"path": path, "code": "MAX_LENGTH", "expected": str(sch["maxLength"]),
                         "actual": str(len(inst)), "detail": "string too long"})
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in sch and inst < sch["minimum"]:
            errs.append({"path": path, "code": "MINIMUM", "expected": str(sch["minimum"]),
                         "actual": str(inst), "detail": "below the minimum"})
        if "maximum" in sch and inst > sch["maximum"]:
            errs.append({"path": path, "code": "MAXIMUM", "expected": str(sch["maximum"]),
                         "actual": str(inst), "detail": "above the maximum"})
    if isinstance(inst, dict):
        for req in sch.get("required", []):
            if req not in inst:
                errs.append({"path": path, "code": "REQUIRED", "expected": req,
                             "actual": "(absent)", "detail": "required key is missing"})
        props = sch.get("properties", {})
        for k, v in inst.items():
            child = "%s.%s" % (path, k)
            if k in props:
                errs.extend(validate(v, props[k], child))
            elif sch.get("additionalProperties") is False:
                errs.append({"path": child, "code": "EXTRA_KEY", "expected": "no extra keys",
                             "actual": k, "detail": "key is not allowed by the schema"})
            elif isinstance(sch.get("additionalProperties"), dict):
                errs.extend(validate(v, sch["additionalProperties"], child))
    if isinstance(inst, list):
        if "minItems" in sch and len(inst) < sch["minItems"]:
            errs.append({"path": path, "code": "MIN_ITEMS", "expected": str(sch["minItems"]),
                         "actual": str(len(inst)), "detail": "too few items"})
        if "maxItems" in sch and len(inst) > sch["maxItems"]:
            errs.append({"path": path, "code": "MAX_ITEMS", "expected": str(sch["maxItems"]),
                         "actual": str(len(inst)), "detail": "too many items"})
        item_sch = sch.get("items")
        if isinstance(item_sch, dict):
            for i, v in enumerate(inst):
                errs.extend(validate(v, item_sch, "%s[%d]" % (path, i)))
    for key in ("allOf",):
        for sub in sch.get(key, []) or []:
            errs.extend(validate(inst, sub, path))
    if "oneOf" in sch or "anyOf" in sch:
        key = "oneOf" if "oneOf" in sch else "anyOf"
        branches = sch[key]
        results = [validate(inst, b, path) for b in branches]
        ok = [r for r in results if not r]
        if key == "oneOf" and len(ok) != 1:
            if not ok:
                errs.append({"path": path, "code": "ONE_OF", "expected": "%d branch(es)" % len(branches),
                             "actual": "matches none", "detail": "value matches no allowed shape"})
            elif len(ok) > 1:
                errs.append({"path": path, "code": "ONE_OF", "expected": "exactly one branch",
                             "actual": "matches %d" % len(ok), "detail": "value matches several shapes"})
        if key == "anyOf" and not ok:
            errs.append({"path": path, "code": "ANY_OF", "expected": "%d branch(es)" % len(branches),
                         "actual": "matches none", "detail": "value matches no allowed shape"})
    return errs


def repair_prompt(schema, errs, original, notes):
    lines = ["You returned JSON that does not satisfy the schema. Fix only the paths listed; keep every other value unchanged.",
             ""]
    for e in errs[:20]:
        lines.append("- `%s` %s: expected %s, got %s — %s" % (e["path"], e["code"], e["expected"], e["actual"], e["detail"]))
    if notes:
        lines.append("")
        lines.append("Formatting problems found in your response: " + "; ".join(notes) + ".")
    lines += ["", "Schema:", "```json", json.dumps(schema, indent=2)[:1600], "```", "",
              "Return only the corrected JSON object. No prose, no code fence, no explanation."]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Validate an LLM response against a JSON schema subset")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--input", required=True, help="file containing the raw model response")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    for p in (a.schema, a.input):
        if not os.path.exists(p):
            sys.exit("missing file: %s" % p)
    schema = json.load(open(a.schema, encoding="utf-8"))
    text = open(a.input, encoding="utf-8", errors="replace").read()

    value, notes = extract(text)
    errs = []
    if value is None:
        verdict = "FAIL"
    else:
        errs = validate(value, schema)
        verdict = "PASS" if not errs else "REPAIR"
    os.makedirs(a.out, exist_ok=True)

    L = ["# LLM output contract\n"]
    L.append("Verdict: **%s** — %d error(s) across %s.\n" % (
        verdict, len(errs), "the parsed object" if value is not None else "an unparseable response"))
    if notes:
        L.append("Recovery notes: %s.\n" % "; ".join(notes))
    if verdict == "FAIL":
        L.append("The response could not be parsed as JSON. Do not retry blindly: a truncated response usually means the "
                 "output limit was hit, and a fenced or chatty response means the instruction needs an output-format clause.\n")
    if errs:
        L.append("| Path | Code | Expected | Got | Problem |")
        L.append("|---|---|---|---|---|")
        for e in errs:
            L.append("| `%s` | %s | %s | %s | %s |" % (e["path"], e["code"], e["expected"], e["actual"], e["detail"]))
        L.append("")
        L.append("## Repair prompt\n")
        L.append("Send this back with the original response, then validate again:\n")
        L.append("```text")
        L.append(repair_prompt(schema, errs, text, notes))
        L.append("```")
    if value is not None:
        L.append("\n## Salient fields\n")
        L.append("```json")
        L.append(json.dumps(value, indent=2, ensure_ascii=False)[:1200])
        L.append("```")
    with open(os.path.join(a.out, "contract_check.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(os.path.join(a.out, "contract_check.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": verdict, "errors": errs, "notes": notes,
                   "parsed": value is not None}, fh, indent=2)
    print("verdict=%s errors=%d parsed=%s" % (verdict, len(errs), value is not None))
    print("wrote %s" % os.path.join(a.out, "contract_check.md"))
    return 0 if verdict == "PASS" else 1 if verdict == "REPAIR" else 2


if __name__ == "__main__":
    sys.exit(main())
