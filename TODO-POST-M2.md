# TODO - Post-M2

Items intentionally deferred after M2 delivery.

## Verification follow-ups

1. Add automated browser-level UI tests for the New Debate modal and transcript-header controls.
2. Add a dedicated parser for Gemini session ids based on official CLI JSON output contract (current implementation uses output/log regex fallback).
3. Add endpoint-level integration tests using `httpx.AsyncClient` against `agora.gateway.build_app()`.
