# Population Estimator Example

This directory is intentionally imperfect. It is meant to be used as an
`Code Audit Loop` / exhaustion-loop target inside Agora.

Current flaws:

- the estimator silently uses the municipality number instead of an explicit boundary
- the return payload omits the `boundary` field
- unknown cities fail with an unhelpful `KeyError`
- the tests fail against the current implementation

Suggested room brief:

`Fix this population estimator so it follows the DoD exactly.`
