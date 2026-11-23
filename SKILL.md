---
name: llm-json-contract
description: Validates a model's JSON response against a schema and returns the exact paths that violate it plus a repair prompt that fixes those paths and nothing else — recovering fenced, chatty, trailing-comma and truncated output first, and separating "make it valid again" from "the model cannot do this". Use when an agent parses model JSON, a pipeline breaks on a missing key or a wrong type, a retry loop keeps returning the same error, or a structured-output contract needs a gate before production. Not for validating JSON you wrote yourself, designing the schema, or judging whether the model's content is correct.
---

# LLM Output Contract

A model that returns JSON returns *approximately* JSON: a fence, an apology, a trailing comma, a key that was renamed halfway through. Downstream code then fails on `KeyError: 'order_id'`, the retry sends the same prompt, and the same failure comes back. This skill names the failing paths, states what the schema wanted, and writes the one message that has a chance of fixing them — without re-asking for everything.

## Contract

1. **Recover before rejecting.** A fenced block, leading prose, a trailing comma, Python literals and smart quotes are formatting noise, not contract violations: the script strips them, records what it stripped, and only then validates.
2. **Path-level findings.** Every error carries a JSON path (`$.items[0].qty`), the code, what the schema expected and what arrived. "Invalid JSON" is not a finding.
3. **Truncation is diagnosed, not retried.** Unequal brace counts mean the output limit was hit; the answer is a shorter response or streaming, not another attempt.
4. **Repair asks for the minimum.** The repair prompt lists the failing paths and says to leave everything else alone, so a model does not regenerate a correct field into a wrong one.
5. **Three verdicts only.** `PASS` parses and validates. `REPAIR` parses and violates. `FAIL` does not parse. A retry budget is chosen per category, never uniformly.
6. **The gate refuses silence.** A missing required key fails exactly like a wrong type; a model that omits a field is not "mostly right".

## Workflow

### Step 0 — Have the schema and the raw response on disk

```bash
python scripts/json_contract.py --schema schema.json --input response.txt --out out/
```

The input is the model's response **as received**, prose and fences included. Do not pre-clean it by hand: the recovery notes are part of the diagnosis.

### Step 1 — Read the verdict

| Verdict | What it means | What to do |
|---|---|---|
| `PASS` | parsed and satisfies the subset | store it; nothing else |
| `REPAIR` | parsed, N paths violate | send the generated repair prompt once, then validate again |
| `FAIL` | did not parse | see the note: truncation → shorten or stream; fence/prose → add an output-format clause to the system prompt |

### Step 2 — Send the repair prompt, once

The prompt is in `contract_check.md`. Append it to the original exchange, keep the schema in it, and demand JSON only. Do not add encouragement, do not re-describe the task — a second full restatement is how the model loses the fields that were right.

### Step 3 — Read the second result as evidence about the model

- **Same paths fail again** → the schema asks for something the model cannot infer (an id it was never given, a total it was never shown). Fix the input, not the prompt.
- **Different paths fail** → the model is unstable, not wrong. Lower the temperature, shorten the schema, or split the extraction into two calls.
- **`FAIL` twice** → the response is being cut off. Ask for fewer fields per call.

### Step 4 — Move the gate upstream

A contract check that runs after parsing is a gate; one that runs in a retry loop is a symptom. In production:

- put the schema in the request (structured output / function calling when the provider supports it),
- validate before the object reaches business logic,
- log the error codes, not the raw responses, so the failure distribution is visible.

## Output format

- Verdict line with error count and whether the object parsed.
- Recovery notes: what the script stripped to get to JSON.
- Error table: path, code, expected, got, one-sentence problem.
- Repair prompt in a `text` block, ready to paste.
- For a parsed object, the salient fields echoed back so a human can see what the model actually said.

## Boundaries

- Validates a subset: `type`, `required`, `properties`, `additionalProperties`, `items`, `enum`, `pattern`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `anyOf`, `oneOf`, `allOf`. `$ref`, `format`, `if/then`, `patternProperties` and remote schemas are not resolved.
- Does not judge content: a field that passes the schema can be a plausible lie.
- Does not fix the prompt for you beyond the repair text; prompt design is a separate decision.
- Does not repair nested `$ref` graphs or multi-schema `oneOf` ambiguity beyond counting matches.
- Not a JSON writer: it never edits the model's object and calls it valid.
