# People Finder

Standalone candidate-discovery tool for professional networking. Compiles typed-anchor query
packs from a seeker profile + one target job, ranks supplied public `/in/` leads by sparse
typed-anchor overlap, and emits `people-candidates.v1` JSON. It does not scrape LinkedIn,
does not establish identity, sends nothing, and never writes another tool's state.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the frozen design and
[BENCHMARK.md](BENCHMARK.md) for the reviewer-owned acceptance contract.

## What it is

Three states that are never collapsed:

1. **discovery** (this product) — queries + supplied results → ranked candidate leads
2. **identity** (a later, separate step on one selected name)
3. **approval** (a human, inside the sink tool)

A public `/in/` URL is a lead: not a contact, not a verified identity, and not a delivery route.

Ranking is a **sparse bag of typed anchors** (rare community, prior employer, school/program,
job-derived function, target employer as a required filter, optional location, default-off
skills) with IDF-style downweighting on common tokens and a cap per path. No embeddings, no
vector similarity, no opaque cosine score as identity evidence.

Second-degree warmth is approximated as `shared_stamp`: two of the seeker's public stamps
observed in one supplied result. It is labelled `public_stamp_proxy` and never claims a
member-graph edge.

## Public operations

```sh
./bin/people-finder compile    --resume <resume.md> --job <job-card.json> [--out <file>]
./bin/people-finder rank       --queries <people-queries.v1.json> --results <recorded-serp.v1.json> [--out <file>]
./bin/people-finder import-exa --candidates <people-candidates.v1.json> --result <recorded-envelope.json> [--out <file>]
./bin/people-finder validate   <document.json> [--expect-schema <schema>]
./bin/people-finder mcp        # stdio JSON-RPC: compile_people_queries, rank_people_candidates, start_people_research
```

Every operation is offline and credential-free: supplied local files only, no API key, no
search backend, no network call. Exit codes: `0` success, `2` malformed or missing input
(no output artifact is written in that case), `1` unexpected internal failure.

## Default gate (frozen)

```sh
python3 tests/benchmark/check_discovery.py          # D  typed discovery and query packs
python3 tests/benchmark/check_ranking.py            # R  ranking, provenance, non-invention
python3 tests/benchmark/check_exa_import.py         # E  recorded provider import
python3 tests/benchmark/check_interfaces.py         # I  CLI and MCP parity
python3 tests/benchmark/check_jobsss_composition.py # J  Jobsss host composition
python3 tests/benchmark/check_keepouts.py           # K  hard keep-outs
```

Each command prints exactly one JSON object to stdout, keeps diagnostics on stderr, exits `0`
only when every frozen assertion for its criterion passed, and performs no live network call.
Convenience runner: `python3 tests/tools/run_gate.py <label>` writes raw stdout, stderr, exit
codes and an assertion summary to `.tmp/benchmark-<label>/`.

## Layout

```text
bin/people-finder                 CLI entry point
src/people_finder/                zero-dependency core (stdlib only)
  anchors.py  textutil.py         typed-anchor extraction, normalization
  packs.py                        query-pack compilation
  rank.py                         sparse typed-anchor scoring -> people-candidates.v1
  exa_import.py                   recorded provider recall import
  results.py  schema.py           supplied-input loading and output validation
  mcp_server.py  cli.py           stdio JSON-RPC surface and command dispatch
tests/benchmark/                  the six frozen checks + shared harness
tests/fixtures/                   fictional resumes, job cards, recorded SERPs, recorded envelopes
tests/tools/                      fixture builder and gate runner
```

## Boundary with the sibling tools

`jobsss` is a later sink and is never the search engine; `contact-brief` is downstream and
never the caller. People-finder does not import Jobsss modules, does not open or write a
Jobsss `PLUGIN_DATA` store, and exposes no `send`, `message`, `connect`, `approve` or
`auto-import` command. The composition check proves the host harness can drive the sibling
`jobsss` executable over its own MCP surface in an isolated temporary store while
people-finder runs with no `PLUGIN_DATA` in its environment and no access to that store.

All fixtures are fictional. No real resume, contact, cookie, token or API key appears in them.
