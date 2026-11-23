# The supported schema subset, and the recovery ladder

## Supported keywords

| Keyword | Applies to | Behaviour |
|---|---|---|
| `type` | any | object, array, string, number, integer, boolean, null; a list means "one of these" |
| `required` | object | every listed key must be present; absence is an error |
| `properties` | object | each present key validated against its subschema |
| `additionalProperties` | object | `false` rejects unknown keys; a schema validates them |
| `items` | array | per-element validation, path gets an index |
| `enum` | any | exact membership; the expected list is printed so the repair prompt is actionable |
| `pattern` | string | `re.search`, not full match — anchor with `^…$` when the whole string matters |
| `minimum` / `maximum` | number, integer | inclusive bounds |
| `minLength` / `maxLength` | string | character counts |
| `minItems` / `maxItems` | array | element counts |
| `allOf` | any | every branch must pass |
| `oneOf` | any | exactly one branch must pass; both zero and several are errors |
| `anyOf` | any | at least one branch must pass |

## Not resolved

`$ref` (local or remote), `format` (email, date-time, uuid — treated as plain strings), `if/then/else`, `patternProperties`, `dependencies`, `propertyNames`, `uniqueItems`, `multipleOf`, `exclusiveMinimum/Maximum`.

If a schema depends on any of these, the check will under-report. That is a known gap, not a false PASS claim: a `PASS` here means "valid against the subset".

## The recovery ladder

Applied in order, cheapest first. Every strip is recorded in the output as a recovery note.

1. **Markdown fence** — a response that opens with ` ```json ` and closes with ` ``` `. Very common; stripped silently.
2. **Leading or trailing prose** — "Here is the structured order: …" and "Let me know if you need more." The script slices from the first `{` or `[` to the last `}` or `]`.
3. **Trailing commas** — `{"a": 1,}` / `[1, 2,]`. Invalid JSON, valid JavaScript habit.
4. **Python literals** — `True`, `False`, `None` where the schema wants `true`, `false`, `null`.
5. **Smart quotes** — `“` `”` around strings.
6. **Single-quoted keys** — not repaired; `json.loads` rejects it and the ladder stops. A model doing this repeatedly needs an output-format instruction, not a parser hack.

If the ladder reaches the end without parsing, the brace counts decide the message: unequal counts mean truncation, equal counts mean a syntax error the script will not guess at.

## Retry policy

| Situation | Budget | Why |
|---|---|---|
| `REPAIR` with 1–2 paths | 1 retry | the repair prompt usually lands |
| `REPAIR` with > 5 paths | 1 retry, then change the call | many errors mean the schema or the input is wrong |
| `FAIL`, truncated | 0 retries | shortening the response is the fix |
| `FAIL`, fenced or chatty | 1 retry with an output-format clause | cheap to try |
| Same paths fail twice | 0 further retries | the model lacks the information, not the discipline |

An unbounded retry loop on the same prompt is the single most common misuse of a validator. The second failure is information; the fifth is a waste.

## Choosing schema strictness

- `additionalProperties: false` is correct for extraction tasks where the object is fed into a fixed pipeline, and wrong for summarisation where the model legitimately adds a field.
- `required` should contain only fields the consumer reads. A required field that nothing reads is a guaranteed repair loop for no benefit.
- `enum` belongs on closed vocabularies (status, category). Do not put it on free text.
- Keep the schema under about 30 properties. Every added field is another chance to fail, and models lose accuracy as the schema grows.

## What to log

Log `{"verdict", "error_codes", "paths", "model", "attempt"}`. Do not log the raw response for every failure: it is large, it contains user data, and the shape of the failure is already captured by the code and path.
