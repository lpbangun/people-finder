# SCORE.md

## Scoring from command evidence

Score each named criterion independently:

- `D`: typed discovery and query packs
- `R`: ranking and non-invention
- `E`: recorded Exa import
- `I`: CLI and MCP parity
- `J`: Jobsss host composition
- `K`: hard keep-outs

For `D`, `R`, `E`, `I`, and `J`:

- `0`: command is missing; exits `2` or unexpectedly; emits invalid/non-machine-readable evidence; uses live network as evidence; or does not exercise the named product surfaces.
- `1–8`: command exits `1`, or valid evidence shows only a strict subset of mandatory assertions passing. Assign proportionally from the observed mandatory assertion results, capped at `8.0`; unsupported assertions count as failed.
- `9.0`: command exits `0`; every frozen mandatory assertion is present and passes with concrete observed evidence.
- `10.0`: qualifies for `9.0`, and the repeated deterministic operations are semantically identical, evidence identifies exact invoked commands and fixture paths, and no unexplained warning, skipped assertion, or uninspected fallback remains.

Do not award points for live Exa, live search, manual demonstrations, screenshots, prose claims, or tests outside the named command.

For `K`:

- `10.0`: command exits `0` and every `K1`–`K10` assertion passes.
- `0`: any keep-out fires, any K assertion is missing or unsupported, or the command does not complete validly.
- No intermediate K score is allowed.

## Converge rule

Converged only when:

```text
D ≥ 9.0
R ≥ 9.0
E ≥ 9.0
I ≥ 9.0
J ≥ 9.0
K ≥ 9.0
```

This is not an average. A high score in one criterion cannot compensate for another criterion below `9.0`.

If any keep-out fires, set `K = 0` and the result is non-converged regardless of every other score.
