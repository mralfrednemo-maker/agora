# Agora UX / Protocol Design Notes

Status: active design review. Do not implement piecemeal; collect decisions here and implement as one coordinated pass.

## Pending Changes

### 0. Simplify the UX before adding more protocol surface

Current concern:
- The current New Room UX is too dense and exposes internal protocol names directly.
- Users cannot tell what they are selecting, why the style matters, which LLMs are primary vs secondary, or what will happen after Start.
- Adding more protocol options into this UI will make the confusion worse.

Decision direction:
- Do not implement the new Primary Pair Convergence workflow into the current cluttered modal as-is.
- First reduce the New Room UX to the minimum necessary choices.
- Add advanced protocol controls slowly, only when the core room creation path is understandable.

Candidate minimal New Room shape:
- Brief
- Mode, with plain names and short descriptions:
  - `Simple Debate`
  - `Primary Pair`
  - `Code Audit`
- Participants, shown as explicit roles instead of a raw checkbox grid:
  - Primary A
  - Primary B
  - Secondary seed model
- Round cap
- Auto-generate final document
- Start

UX rules:
- Do not show internal names like `Ein MDP` unless the implementation actually matches that protocol.
- Do not expose offline participants in the default picker unless the user expands an advanced section.
- Make the selected workflow visually preview what will happen before Start.
- The War Room should emphasize the conversation view first, not the backend phase vocabulary.
- Keep advanced knobs hidden until the minimal happy path works.

Dynamic fields / tabs:
- The room setup form should be mode-driven. The user picks the deliberation style first; only relevant fields appear afterward.
- Do not use one universal modal with every possible field visible at once.
- Candidate structure:
  - `Brief` tab: shared across most modes; asks for the user question and optional attachments/context.
  - `Participants` tab: mode-specific role assignment, not raw model checkboxes by default.
  - `Workflow` tab: shows only the controls for the selected mode.
  - `Advanced` tab: hidden by default; exposes offline models, raw model IDs, experimental options, and low-level caps.
- Examples:
  - `Simple Debate`: brief, participants, round cap, final document toggle.
  - `Primary Pair`: brief, Primary A, Primary B, Secondary seed, convergence cap, final document toggle.
  - `Code Audit`: target directory, DoD document, fixer/auditor roles, audit cap, apply-changes toggle.
- The visible field set should change when the user changes the mode.
- The modal should explain the selected workflow with a small visual preview, not long prose.

Per-role model and effort selection:
- The user must be able to choose the backend model and reasoning effort for each LLM role.
- Role assignment alone is not enough; each role needs explicit runtime configuration.
- For `Primary Pair`, each role card should expose:
  - Driver / provider
  - Model
  - Effort / thinking level
- Candidate role-card shape:
  - `Primary A`
  - selected driver
  - selected model
  - selected effort
  - session policy: `persistent for room`
- Same for `Primary B` and `Secondary`.
- Keep these controls mode-specific:
  - `Simple Debate` may expose fewer role controls.
  - `Primary Pair` should expose all three role cards.
  - `Code Audit` should expose fixer/auditor model choices and effort levels.
- Hide raw backend IDs behind friendly labels by default, but let the user expand to see exact backend IDs when needed.

### 1. Add an intake clarification gate before deliberation

Current concern:
- The current shared room rule says: if the brief is ambiguous, state a working interpretation and proceed.
- This can waste a deliberation by letting participants debate invented assumptions instead of the requestor's intended context.

Decision direction:
- Material ambiguity should pause the room and return a clarification request to the original requestor.
- The goal is no silent guessing on context that could materially change the answer.

Candidate behavior:
- Add a phase-zero intake check before real deliberation.
- Each participant checks whether the brief has missing or ambiguous context that could materially change the result.
- If a blocking ambiguity exists, the room does not proceed to positions/debate.
- Agora returns a consolidated clarification request to the user.
- Once the user answers, the deliberation starts with the clarified brief.

Candidate prompt rule:

```text
Before answering, assess whether the brief contains missing or ambiguous context that could materially change the answer.

If yes, do not proceed with deliberation. Return:
CLARIFICATION_REQUIRED
- question 1
- question 2
- question 3

Only proceed if the ambiguity is minor and would not materially change the result. In that case, state:
ASSUMPTIONS_USED
- assumption 1
Then continue.
```

Open design questions:
- Should one blocking ambiguity from any participant pause the room, or should multiple participants need to agree it is blocking?
- Should the intake gate be run by all selected participants or by a cheaper/single intake agent?
- Should clarification questions be consolidated deterministically by Agora code or by one selected synthesis participant?
- Should the UI expose a paused state such as `clarification_required` distinct from ordinary `paused`?

Exhaustion Loop-specific direction:
- Be stricter than debate rooms.
- If the DoD or target is ambiguous enough that pass/fail cannot be judged, pause before code changes.
- If ambiguity is minor, record it as an assumption or audit note, but do not invent acceptance criteria.

### 2. Use prompt-carried deliberation state and produce a full final synthesis

Current concern:
- Agora currently persists `room.json`, `transcript.jsonl`, `events.log`, and `verdict.md`.
- The transcript is complete, but the current prompts do not strongly require the models to keep agreed points alive.
- The current verdict prompt asks for shared conclusions and residual disagreements, but it relies on one selected participant to infer them from context after the fact.
- Because deliberation naturally spends more time on disagreement, stable agreements can be under-represented in the final output unless they are explicitly tracked.

Decision direction:
- Do not over-engineer a separate external ledger system for the first implementation pass.
- Use prompting and the existing LLM session context to carry deliberation state forward.
- The final EIN-MDP output should be a full synthesis document, not just a short verdict.
- Later prompts should require each participant to preserve agreed points, identify live disagreements, and state what changed.
- The final synthesizer should receive the participant final positions in-session and produce the final synthesis from that context.

Candidate prompt-carried state block:

```text
At the end of your answer, include:

AGREEMENTS TO PRESERVE
- ...

LIVE DISAGREEMENTS
- ...

POSITION SHIFT
- Changed: ...
- Unchanged: ...
```

Candidate behavior:
- Keep the state inside the model conversations rather than adding a new persisted ledger subsystem.
- Strengthen the prompts so agreements are carried forward deliberately, not reconstructed only at the end.
- Start the agreement list as soon as the first multi-perspective comparison happens.
- In every later deliberation cycle, require each participant to update the agreement list rather than merely answer the latest objection.
- Treat the agreement list as cumulative: preserve prior agreements unless the participant explicitly says a prior agreement is invalid and explains why.
- Require each participant to separate confirmed agreements from candidate agreements that still need pressure testing.
- By Phase 4, each participant must output the full cumulative agreement/disagreement register, not merely deltas from the latest round.
- Phase 4 must be self-contained because Phase 5 receives the other participants' Phase 4 final positions inline, not their full private thread history.
- In final positions, each model should state its verdict, what changed, what did not change, strongest new point, and top conclusions.
- In final positions, each model should include:
  - `CONFIRMED AGREEMENTS`
  - `REMAINING DISAGREEMENTS`
  - `MY FINAL VERDICT`
- In the final synthesis prompt, the synthesizer should first merge the participants' confirmed agreement lists, then list remaining disagreements, apply the 2/3 majority rule where needed, and only then write the final synthesis.
- Preserve minority views when the majority resolves a disagreement.

Current laptop EIN-MDP prompt gap:
- Phase 2 asks for strongest/weakest arguments, contradictions, position, missed factor, and hallucination check.
- Phase 3 explicitly asks where the participant agrees and disagrees.
- Phase 4 asks for final verdict, changed/unchanged position, strongest new point, and top 3 conclusions.
- Phase 5 asks the synthesizer to list remaining disagreements and produce the final synthesis.
- Missing piece: no phase currently requires a cumulative agreement register that grows round by round.

Recommended prompt correction:
- Leave Phase 1 and Phase 1.5 focused on perspective generation and adversarial pressure; do not ask for agreements there.
- Start the cumulative agreement register in Phase 2, after each model has seen the six views plus the original question and source context.
- Add a cumulative `AGREEMENTS TO PRESERVE` section to Phase 2, Phase 3, and Phase 4.
- Add a `REMAINING DISAGREEMENTS` section to Phase 3 and Phase 4.
- In Phase 3, every participant should compile/update the agreement and disagreement register from its own Phase 2 output plus the other two participants' Phase 2 outputs. Do not allocate compilation to a single model yet; having all three compile independently catches omissions and false consensus.
- In Phase 4, require each participant to restate the complete final register:
  - all confirmed agreements accumulated so far
  - all remaining disagreements
  - any disagreements resolved by its own judgment
  - its final verdict
- Change Phase 5 so it explicitly says:

```text
Before listing disagreements, first consolidate the CONFIRMED AGREEMENTS from all three Phase 4 final positions.
These agreements are part of the final answer and must not be dropped merely because they were no longer disputed.

Then list each remaining disagreement, identify which position had 2/3 support, and resolve it.

Then produce the FINAL SYNTHESIS...
```

Candidate final document shape:
- Title, room ID, date, selected style, participants, and model IDs.
- Original brief, plus clarification history and assumptions used.
- One- or two-sentence final answer.
- Full synthesis: the practical answer or decision, written as a coherent document.
- Agreements to preserve: stable claims all participants accept or did not contest.
- Resolved disagreements: issue, competing positions, majority or consensus resolution, and rationale.
- Residual disagreements: what remains unsettled and what evidence would settle it.
- Evidence and provenance: referenced participant arguments, uploaded DoD files, or source documents.
- Per-participant closing verdicts.
- Audit note: confirmation that the synthesis introduced no new arguments outside the deliberation.

Prompt parity concern:
- Agora's current `Ein MDP` room style is not identical to the laptop EIN-MDP loop pipeline.
- Agora currently has a compact phase set: `positions`, `contrarian`, `debate`, `verdict`.
- The laptop pipeline uses a six-phase flow: `phase1`, `phase1_5`, `phase2`, `phase3`, `phase4`, `phase5`, with a ledger file and a dedicated final synthesis phase.
- We need to decide whether Agora's menu item should mean exact laptop parity, or whether the current compact flow should be renamed as a lighter variant.

Recommended direction:
- Rename the current compact Agora `Ein MDP` room style because it is not laptop EIN-MDP and the name is causing confusion.
- Reserve `Ein MDP` for an Agora implementation that actually matches the laptop six-phase protocol semantics.
- If we keep the current compact flow, give it a distinct name such as `Compact Adversarial`, `Mini MDP`, or `Objection Loop`.
- Add a dedicated final synthesis phase that consumes the participant final verdicts in the synthesizer's conversation context.
- The UI should expose the final synthesis as the main deliverable and let the user inspect the transcript as the supporting artifact.

### 3. Candidate replacement: Primary Pair Convergence

Purpose:
- Replace or rename the current misleading Agora `Ein MDP` room style with a protocol that uses two primary LLMs and one secondary seed LLM.
- This is not laptop EIN-MDP. It is a pairwise convergence loop seeded by three initial views.

Candidate name:
- Preferred working name: `Primary Pair Convergence`.
- Alternatives: `Two-Primary Convergence`, `Triad Seed, Pair Debate`, `Pairwise Synthesis Loop`.

Workflow:
- Optional intake gate: if the brief is materially ambiguous, pause and ask the user for clarification before launching the room.
- Round 1: all three LLMs respond in parallel.
  - Primary A gives an independent view.
  - Primary B gives an independent view.
  - Secondary gives an independent seed view.
- Round 2: only the two primary LLMs continue.
  - Both primaries receive all three Round 1 views plus the original brief/context.
  - Each primary produces a self-contained final-draft document.
  - Each final draft must include agreements, rejected/weak ideas, remaining disagreements, and proposed final text.
- Alternating convergence loop:
  - Send one primary's latest document to the other primary.
  - The receiving primary accepts useful ideas, rejects ideas it disagrees with, and outputs a complete revised document.
  - Send that revised document back to the other primary with the same instruction.
  - Continue for a round cap or until both primary LLMs accept the same substantive text.
- Output:
  - If converged, use the latest accepted document as the final output.
  - If not converged by the cap, output the latest document plus the remaining disagreement list.

Prompt invariant:
- From Round 2 onward, every primary output must be a complete document, not only a critique or delta.
- Each revision must carry forward:
  - full agreements
  - rejected/weak ideas
  - remaining disagreements
  - final proposed text
- The two primary LLMs have persistent thread context. Do not reattach or restate information already present in that same primary LLM's session unless needed for clarity or recovery.
- In alternating revision rounds, pass only the other primary LLM's latest document plus the revision instruction; the receiving LLM should rely on its own thread history for its prior views and context.
- Each revision should end with:

```text
CONVERGED
```

or

```text
NOT_CONVERGED: <one-line reason>
```

Diagram artifact:
- `C:\Users\chris\PROJECTS\agora\docs\primary-pair-convergence-flow.html`
- The diagram must include stable round labels and explicit input/outcome columns so the protocol can be discussed by ID:
  - `R0`: intake
  - `R1`: parallel seed views -> `A0`, `B0`, `S0`
  - `R2A`: Primary A full draft -> `A1`
  - `R2B`: Primary B full draft -> `B1`
  - `R3B`: B revises using `B1` + `A1` -> `B2`
  - `R4A`: A revises using `A1` + `B2` -> `A2`
  - `R5B...`: continuing alternating revisions -> `B3`, `A3`, etc.
  - `Ck`: convergence check
  - `F`: final output
- Latest screenshot: `C:\Users\chris\PROJECTS\agora\logs\primary-pair-convergence-flow-io-map.png`

War Room monitoring UX:
- The operator needs to see the LLMs talking to each other, including what document was passed and how the receiving model replied.
- Use a Greek Pi-shaped layout for this protocol:
  - top wide, shorter panel: secondary LLM seed view `S0`
  - bottom left tall panel: Primary A thread `A0 -> A1 -> A2 -> A3...`
  - bottom right tall panel: Primary B thread `B0 -> B1 -> B2 -> B3...`
- The secondary panel can remain short because the secondary LLM only contributes the seed view.
- The two primary panels should be long and scrollable because the real deliberation continues between them.
- The UI should make each turn label visible (`A1`, `B2`, etc.) and show both the input artifact received and the output reply/document produced.
- Pi-layout diagram screenshot: `C:\Users\chris\PROJECTS\agora\logs\primary-pair-convergence-flow-pi-monitor.png`
