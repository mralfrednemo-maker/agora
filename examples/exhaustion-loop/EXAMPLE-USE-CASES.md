# Exhaustion Loop Example Use Cases

## 1. Population Estimator Repair

Use this when you want the loop to repair a small codebase against an explicit DoD.

- **Mode**: `Code Audit Loop`
- **Brief**: `Fix this population estimator so it satisfies the DoD exactly.`
- **Target Directory**:
  `C:\Users\chris\PROJECTS\agora\examples\exhaustion-loop\population-estimator-broken`
- **DoD Document**:
  `C:\Users\chris\PROJECTS\agora\examples\exhaustion-loop\dod-population-estimator.md`

Suggested roles:

- **Fixer**: `claude-code-new-1`
- **Auditor**: `gemini-cli-1`
- **Validator**: `codex-1`

What makes it useful:

- the code is intentionally wrong
- the tests intentionally fail before repair
- the DoD is explicit enough for an audit loop to check concrete claims
- the repaired sibling directory `population-estimator` can be used as a post-fix reference

## 2. Boundary-Specific Population API Audit

Use this if you want to pressure the loop on reasoning discipline rather than only code edits.

- **Mode**: `Code Audit Loop`
- **Brief**: `Repair the estimator so it never conflates municipality, urban, and metro populations.`
- **Target Directory**:
  `C:\Users\chris\PROJECTS\agora\examples\exhaustion-loop\population-estimator-broken`
- **DoD Document**:
  `C:\Users\chris\PROJECTS\agora\examples\exhaustion-loop\dod-population-estimator.md`

This is good for checking whether the fixer actually grounded its claims in the target files instead of just asserting that everything already passes.
