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

For a target job, the host companion in `experiments/jev-contact-pilot/` can
discover people with Treg, rank them with Jev, then find emails with Treg for
one or two selected candidates. Each provider call is recorded privately with
its cost. People Finder's bundled runtime remains offline and credential-free:
it does not inspect mailboxes, infer addresses or write contact records, and a
candidate output is never proof of contactability.

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

## Standalone default gate (offline)

The standalone gate runs typed discovery, ranking, recorded Exa import, CLI/MCP parity,
and hard keep-outs. It uses the active Python interpreter and does not require a JobSSS
checkout or provider credentials.

~~~
python3 tests/tools/run_gate.py <label>
# Windows: py -3 tests/tools/run_gate.py <label>
~~~

The summary records Criterion J as skipped because sibling integration is optional. To run
it, pass --with-jobsss. The runner uses the same Python interpreter for every check.
JOBSSS_BIN may point to an executable elsewhere. If it is unset, the harness looks for
../jobsss/bin/jobsss relative to this repository.

~~~
JOBSSS_BIN=/path/to/jobsss/bin/jobsss python3 tests/tools/run_gate.py <label> --with-jobsss
~~~

Each check prints one JSON object to stdout, keeps diagnostics on stderr, and reports its
exit status and assertion results in .tmp/benchmark-<label>/. Criterion J exercises a
host-owned composition boundary; it does not change the stateless People Finder core.

## Plugin packaging (additive checks)

The repository root is a portable Agent Plugins 1.0.0 package: `plugin.json` (manifest),
`mcp.json` (one stdio server `people-finder` -> `./bin/people-finder mcp`),
`skills/people-finder/SKILL.md` (installed skill), `bin/people-finder` and
`src/people_finder/` (bundled runtime). `compat/hermes/config.yaml.template` is the only
generated adapter, and it is a pointer with no policy.

The package declares **no data directory**: people-finder stores no state, so
`${PLUGIN_DATA}` is deliberately not used (nor is `${PLUGIN_ROOT}`) and no store is fabricated
on its behalf. Install is a directory copy; uninstall is a directory removal.

```sh
python3 tests/plugin/check_plugin_packaging.py   # P  manifest, registration, skill, install, keep-outs
python3 tests/plugin/check_hermes_host_probe.py  # H  real isolated Hermes host probe
```

`check_plugin_packaging.py` is fully offline and fixture-based: it validates the Manifest and
MCP configuration against the Agent Plugins 1.0.0 rules, spawns the declared command + args and
asserts the advertised tool names and argument schemas, drives real offline tool calls over that
declared server, checks the miss/shortfall semantics, proves the host-provided `PLUGIN_DATA`
canary is untouched, and proves a relocated install produces identical results with a clean
uninstall.

`check_hermes_host_probe.py` renders the adapter into a temporary `HERMES_HOME` and runs the real
host surface there (`hermes mcp test people-finder`, `hermes skills list`). It exits `2` with an
honest `status: unavailable` when no Hermes binary is present. It proves local host integration
only: no discovery, no send, no paid call, and no credential is copied between profile homes.

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
plugin.json  mcp.json             Agent Plugins 1.0.0 manifest and MCP registration
skills/people-finder/SKILL.md     installed skill: workflow, tool shapes, boundaries
compat/                           generated client adapters (pointers only)
tests/benchmark/                  the six frozen checks + shared harness
tests/plugin/                     additive packaging and host-probe checks
tests/fixtures/                   fictional resumes, job cards, recorded SERPs, recorded envelopes
tests/tools/                      fixture builder and gate runner
```

## Boundary with the sibling tools

JobSSS is a later sink and Contact Brief is downstream. People Finder does not import
JobSSS modules, open or write a JobSSS PLUGIN_DATA store, or expose a send, message,
connect, approve, or auto-import command. Optional Criterion J proves the host harness
can drive a configured sibling JobSSS executable over its own MCP surface in an isolated
temporary store while People Finder runs without PLUGIN_DATA and has no access to that
store. Set JOBSSS_BIN when the executable is outside ../jobsss/bin/jobsss.

All fixtures are fictional. No real resume, contact, cookie, token or API key appears in them.

Reference material at the repository root: README.md (operations and gate), ARCHITECTURE.md
(design), BENCHMARK.md (offline acceptance contract), and SCORE.md (scoring). The
separately configured live journey is tests/e2e/check_profile_jobs_people.py. It is not
part of the offline gate; consult that script for live search and JobSSS requirements.
This package does not ship a separate E2E_BENCHMARK.md contract.

Offline verification:

~~~
python3 tests/plugin/check_plugin_packaging.py        # deterministic package smoke
python3 tests/plugin/check_hermes_host_probe.py       # isolated Hermes host probe
python3 tests/tools/run_gate.py <label>               # standalone D/R/E/I/K gate
python3 tests/tools/run_gate.py <label> --with-jobsss # add optional Criterion J
~~~
