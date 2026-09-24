# BENCHMARK.md

## Status

This is the reviewer-owned acceptance contract for `people-finder`.

This contract defines the commands, assertion IDs, expected exit codes, fixtures, and pass meanings for review. Changes to the contract are maintainer-owned; implementation work must not weaken an assertion or skip a required command. Test fixtures and checks may be appended, but no required fixture, assertion, or check may be deleted, renamed, skipped, or relaxed to obtain a passing result.

Implementation is limited by `ARCHITECTURE.md`. Internal module layout is not frozen.

## Product boundary

`people-finder` discovers and ranks public candidate leads. It does not establish identity, claim a LinkedIn relationship, send anything, approve contacts, or write Jobsss state.

The standalone default gate is completely offline and does not require JobSSS:

- fictional resumes and jobs;
- recorded SERPs;
- recorded Exa envelopes;
- supplied CLI and MCP inputs;
- an isolated temporary JobSSS store only when optional Criterion J is run.

The standalone default gate runs D, R, E, I and K. Criterion J is an optional sibling integration check.

Live network or live Exa execution carries zero points and cannot replace any recorded fixture.

## Required implementation-owned files

The implementer must create these exact paths:

```text
bin/people-finder

tests/benchmark/check_discovery.py
tests/benchmark/check_ranking.py
tests/benchmark/check_exa_import.py
tests/benchmark/check_interfaces.py
tests/benchmark/check_jobsss_composition.py
tests/benchmark/check_keepouts.py

tests/fixtures/resumes/a-rich-stamps.md
tests/fixtures/resumes/b-career-hop.md
tests/fixtures/resumes/c-thin-noisy.md

tests/fixtures/jobs/a-target.json
tests/fixtures/jobs/b-target.json
tests/fixtures/jobs/c-target.json

tests/fixtures/serps/a-recorded.json
tests/fixtures/serps/b-recorded.json
tests/fixtures/serps/c-recorded.json

tests/fixtures/exa/no-result.json
tests/fixtures/exa/one-person.json
tests/fixtures/exa/multiple-people-invalid.json
```

All fixtures must be fictional. No real user resume, contact, cookie, token, or API key may appear in them.

## Check protocol

Run every command from the repository root.

Each check is one command and must:

1. exercise the product through public CLI or MCP surfaces;
2. use only Python standard library for the check harness;
3. perform no live network call;
4. print exactly one JSON object to stdout;
5. use stderr only for diagnostics;
6. execute every frozen assertion—no early success;
7. run deterministic product operations twice where the criterion defines a reproducibility assertion.

Required result shape:

```json
{
  "benchmark": "people-finder-v1",
  "criterion": "D",
  "offline": true,
  "passed": true,
  "assertions": [
    {
      "id": "D1",
      "passed": true,
      "evidence": {}
    }
  ]
}
```

`evidence` must contain observed values, not only prose such as `"ok"`.

Exit codes are frozen:

- `0`: every mandatory assertion for that criterion passed.
- `1`: the check ran, but one or more mandatory assertions failed.
- `2`: malformed fixture, unavailable required local executable, invalid check output, or harness/configuration failure.
- Any other exit code: crash; criterion score is zero.

A command passes only when it exits `0`, emits valid JSON matching the protocol, names the expected criterion, reports `"offline": true`, and contains every frozen assertion ID for that criterion with `"passed": true`.

## Public operations exercised by the gate

The implementation may add options, but these operations must remain callable:

```text
./bin/people-finder compile
./bin/people-finder rank
./bin/people-finder import-exa
./bin/people-finder mcp
```

`compile` accepts a resume file and job file and emits compiled query-pack JSON.

`rank` accepts compiled queries plus supplied result JSON and emits `people-candidates.v1`.

`import-exa` accepts an existing candidate envelope plus a recorded normalized Exa envelope and emits a new candidate envelope.

`mcp` is a stdio JSON-RPC server advertising at least:

```text
compile_people_queries
rank_people_candidates
start_people_research
```

The CLI and MCP compile/rank operations must not require network access or credentials.

## Criterion D — typed discovery and query packs

Command:

```sh
python3 tests/benchmark/check_discovery.py
```

Expected passing exit code: `0`.

Mandatory assertions:

- `D1`: Fixture A extraction emits typed anchors for school or program, rare community or lab, target employer, and job-derived function. Every asserted anchor includes source evidence traceable to supplied resume or job text.
- `D2`: Fixture A compiles at least `alumni_at_target`, `community_at_target`, and `function_at_target`; it is not reducible to school × employer.
- `D3`: Fixture B extracts the seeker’s actual prior employer as `prior_employer` and compiles `prior_employer_at_target`.
- `D4`: Compiled output uses multiple query packs and represents `hiring_adjacent` as a distinct lane from peer discovery.
- `D5`: `shared_stamp` is represented only as a public-stamp proxy; neither its query nor metadata claims a LinkedIn graph edge, connection, or second-degree relationship.
- `D6`: Fixture C does not promote generic skills or a common name into rare-community, school, or prior-employer anchors. Missing facts remain absent or explicitly unknown.
- `D7`: The same compile invocation run twice with identical inputs produces semantically identical JSON after removal of explicitly documented retrieval timestamps.

Pass means typed anchors are evidence-backed and several frozen query paths compile without pretending that school × employer is the whole product.

## Criterion R — ranking, provenance, and non-invention

Command:

```sh
python3 tests/benchmark/check_ranking.py
```

Expected passing exit code: `0`.

Mandatory assertions:

- `R1`: Every emitted item conforms to `people-candidates.v1`, has a public `linkedin.com/in/` URL observed in the supplied fixture results, and contains array-valued `paths` and `unknowns`.
- `R2`: Fixture A emits candidates carrying the paths that actually fired, including alumni and community evidence where supplied by the recorded SERP.
- `R3`: In fixture B, the recorded candidate matching the seeker’s prior employer and target employer ranks above every candidate supported only by generic title or function terms.
- `R4`: Hiring-adjacent candidates remain in a separate lane and cannot displace peer candidates through an undifferentiated shared ordering.
- `R5`: Fixture C returns at most one candidate and invents no person, URL, employer, school, community, title, relationship, email, or channel absent from its recorded inputs.
- `R6`: Unknown or unobserved facts appear in `unknowns`; no output field or value labels a candidate `connected`, `connection`, `second_degree`, `2nd-degree`, or equivalent.
- `R7`: Duplicate URLs normalize to one candidate while retaining all supported `paths`; unsupported path tags are not added.
- `R8`: Ranking the same supplied inputs twice produces semantically identical order, scores, paths, and unknowns.

Pass means ranking is sparse, deterministic, path-tagged, honest about unknowns, and incapable of manufacturing people or relationships.

## Criterion E — recorded Exa import

Command:

```sh
python3 tests/benchmark/check_exa_import.py
```

Expected passing exit code: `0`.

Mandatory assertions:

- `E1`: `tests/fixtures/exa/no-result.json` uses a contact-brief-style normalized envelope with a completed no-result or unavailable status and a null result; import succeeds without adding a candidate.
- `E2`: `tests/fixtures/exa/one-person.json` records route, actual tool or alias, retrieval time, status, subject, and cited source URL. Import adds or merges no more than one public people-search lead.
- `E3`: An imported Exa lead remains discovery evidence. It is not marked identity-confirmed, connected, approved, reachable, or contactable.
- `E4`: `tests/fixtures/exa/multiple-people-invalid.json` is rejected by the product subprocess with exit code `2`, and no output artifact is committed by that invocation.
- `E5`: Import rejects contradictory status/result data and rejects a non-null lead without source attribution.
- `E6`: The importer neither requires nor reads `EXA_API_KEY` or another provider key; the check runs with credential-like environment variables removed.
- `E7`: Repeating the valid one-person import produces semantically identical output and does not duplicate the lead.

Pass means `import-exa` consumes zero-or-one recorded provider results for recall only, following the normalized handoff pattern without turning provider output into identity.

## Criterion I — CLI and MCP parity on supplied results

Command:

```sh
python3 tests/benchmark/check_interfaces.py
```

Expected passing exit code: `0`.

Mandatory assertions:

- `I1`: CLI `compile` succeeds from fixture resume and job files with all credential-like environment variables removed.
- `I2`: CLI `rank` succeeds using only compiled query JSON and recorded SERP JSON and emits valid `people-candidates.v1`.
- `I3`: MCP initialization and `tools/list` succeed over stdio JSON-RPC, and the server advertises `compile_people_queries`, `rank_people_candidates`, and `start_people_research`.
- `I4`: MCP `compile_people_queries` accepts supplied resume and job content without required API-key fields.
- `I5`: MCP `rank_people_candidates` accepts supplied result content without required API-key fields or a required network backend.
- `I6`: CLI and MCP compile outputs are semantically equivalent for the same fixture, excluding transport metadata.
- `I7`: CLI and MCP rank outputs have the same normalized candidates, order, `paths`, and `unknowns` for the same supplied results.
- `I8`: The check denies network access; no CLI or MCP operation attempts DNS, HTTP, browser automation, or provider dispatch.
- `I9`: MCP output remains protocol-clean: JSON-RPC only on stdout, with diagnostics confined to stderr.

Pass means both public interfaces expose the same offline core and supplied-result workflow without keys.

## Criterion J — JobSSS host composition (optional sibling integration)

Command:

~~~
python3 tests/benchmark/check_jobsss_composition.py
~~~

Run this criterion separately, or add it to the runner with
python3 tests/tools/run_gate.py <label> --with-jobsss.

JOBSSS_BIN may name an absolute executable path or a path relative to the People Finder
repository root. Without it, the harness uses ../jobsss/bin/jobsss relative to the
People Finder repository. If that optional executable is unavailable, the standalone
default gate still runs; Criterion J reports a setup failure only when explicitly run.

Expected passing exit code when configured: 0.

Mandatory assertions:

- `J1`: The harness creates a fresh temporary PLUGIN_DATA, invokes the configured sibling JobSSS executable, and does not import JobSSS source modules.
- `J2`: Through actual JobSSS MCP calls, the harness invokes start, create_profile from a fictional fixture resume, and import_job from a fictional fixture job.
- `J3`: While People Finder compile and rank run, its environment does not contain PLUGIN_DATA; the temporary JobSSS directory is inaccessible to the People Finder subprocess. The directory tree and file hashes are unchanged across that phase.
- `J4`: People Finder ranks the recorded SERP offline and emits candidate leads; it performs no JobSSS mutation.
- `J5`: Harness-owned mapping converts a selected candidate into explicit import_contact and record_research argument objects. The mapper is test/host composition code, not People Finder product code.
- `J6`: The harness, not People Finder, sends those arguments through actual JobSSS MCP calls to the sibling executable.
- `J7`: list_contacts readback finds the exact returned contact ID and shows humanApproved: false.
- `J8`: list_research readback finds the exact returned research ID and preserves candidate provenance, paths, and unknowns in the mapped research record.
- `J9`: map_reachable_network for the imported fixture job sees the imported contact or associated research record without claiming a relationship, referral, permission, or message delivery.
- `J10`: No JobSSS send, draft-send, approval, or human-decision tool is called.
- `J11`: The temporary store is removed after the check, including on assertion failure.

Pass means JobSSS remains an independently executed sink owned by the host harness, while
People Finder never opens or writes its store.

## Criterion K — hard keep-outs

Command:

```sh
python3 tests/benchmark/check_keepouts.py
```

Expected passing exit code: `0`.

This criterion is binary. Any failed assertion is a keep-out fire and forces `K = 0`.

Mandatory assertions:

- `K1`: No product dependency, executable path, option, or runtime behavior scrapes LinkedIn, consumes LinkedIn cookies/session state, drives a logged-in browser, or integrates Sales Navigator, HarvestAPI, or Apify.
- `K2`: No product operation sends or schedules email, messages, connection requests, outreach, applications, or other external actions.
- `K3`: Embeddings, vector similarity, or opaque semantic similarity are not used as identity evidence or as a substitute for typed-anchor ranking.
- `K4`: People-finder never auto-imports or auto-approves a candidate in Jobsss.
- `K5`: People-finder product code neither imports Jobsss modules nor opens, reads, creates, or writes a Jobsss `PLUGIN_DATA` store.
- `K6`: No output or user-facing claim describes a candidate as connected, reachable through the seeker, LinkedIn second-degree, `2nd-degree`, or equivalent. `shared_stamp` is allowed only when explicitly described as a public-stamp proxy.
- `K7`: No API key is required by the zero-dependency core, CLI compile/rank path, MCP compile/rank path, or default test gate.
- `K8`: All four other standalone default-gate checks remain offline; optional Criterion J is not run by the keep-out check.
- `K9`: Repository inspection finds no product command named or described as `send`, `message`, `connect`, `auto-import`, or another authority-bearing equivalent.
- `K10`: The benchmark inventory is intact: all frozen check paths, fixture paths, criterion IDs, and assertion IDs remain present.

Pass means every keep-out is absent. A single keep-out fire overrides every positive score.

## Frozen execution set

The standalone default gate is exactly:

~~~
python3 tests/benchmark/check_discovery.py
python3 tests/benchmark/check_ranking.py
python3 tests/benchmark/check_exa_import.py
python3 tests/benchmark/check_interfaces.py
python3 tests/benchmark/check_keepouts.py
~~~

Optional sibling integration:

~~~
JOBSSS_BIN=/path/to/jobsss/bin/jobsss python3 tests/benchmark/check_jobsss_composition.py
~~~

Additional tests may be appended. They cannot replace, skip, delete, rename, or weaken
these standalone commands or their assertions. Criterion J remains a separate scored
criterion and is required for full convergence when the sibling integration is configured.
