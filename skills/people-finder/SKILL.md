---
name: people-finder
description: Discover candidate leads with people-finder, the offline typed-anchor discovery plugin — compile query packs from one supplied profile and one target job, rank supplied public professional-profile leads, and merge one recorded provider envelope into people-candidates.v1. Discovery evidence only; never identity, approval, contactability or an external action.
---

# people-finder — offline candidate-lead discovery

people-finder is a portable Agent Plugin. The bundled runtime is
`./bin/people-finder` (Python standard library only, no dependencies, no
installation step beyond the plugin package itself) and the MCP surface is
declared in `mcp.json` as the stdio server `people-finder` launched with
`./bin/people-finder mcp`.

It answers exactly one question: **which supplied public leads overlap the
seeker's typed anchors for one target job, and which paths fired?**

It never answers: who this person is, whether they are reachable, whether an
address exists, and whether anyone approved anything.

## Invocation

| Surface | How |
| --- | --- |
| MCP (host) | Tool calls on `compile_people_queries`, `rank_people_candidates`, `start_people_research` |
| CLI | `./bin/people-finder compile\|rank\|import-exa\|validate\|mcp` |
| Files | Every operation reads and writes JSON/Markdown files at paths the caller names |

Every operation is offline and credential-free: supplied local content only, no
API key, no search backend, no network call. Exit codes: `0` success, `2`
malformed or missing input (no output artifact is written), `1` unexpected
internal failure.

## MCP tool names and argument shapes

`compile_people_queries` — compile typed-anchor query packs from supplied resume
content and one target job card.

```
resume_text   string   supplied resume content (preferred for small content)
resume_path   string   local path to supplied resume content
job           object   supplied target job card
job_path      string   local path to the supplied job card
resume_source string   label recorded as the resume provenance
job_source    string   label recorded as the job provenance
generated_at  string   fixed retrieval timestamp for reproducible output
```

`rank_people_candidates` — rank supplied search results against compiled packs
and emit `people-candidates.v1`.

```
queries       object   compiled people-queries.v1 document
queries_path  string   local path to the compiled document
results       object   supplied results document (host-supplied or recorded)
results_path  string   local path to the supplied results document
generated_at  string   fixed retrieval timestamp
```

`start_people_research` — describe the cross-plugin discovery intent for one
target job and, when both inputs are present, preview the packs it would use.

```
resume_text   string   supplied resume content
resume_path   string   local path to supplied resume content
job           object   supplied target job card
job_path      string   local path to the supplied job card
resume_source string   label for the supplied resume
job_source    string   label for the supplied job card
generated_at  string   fixed retrieval timestamp
subject       string   optional human-selected subject name
notes         string   optional free-text note
```

No tool has a required argument, and no tool accepts a search query, backend,
provider, URL, engine, key or maximum-results option. The host owns the search;
people-finder only receives what the host already obtained.

CLI equivalents of the same operations:

```sh
./bin/people-finder compile    --resume <resume.md> --job <job-card.json> [--out <file>] [--at <ts>] [--quiet]
./bin/people-finder rank       --queries <people-queries.v1.json> --results <supplied-results.json> [--out <file>] [--at <ts>] [--quiet]
./bin/people-finder import-exa --candidates <people-candidates.v1.json> --result <recorded-envelope.json> [--out <file>] [--quiet]
./bin/people-finder validate   <document.json> [--expect-schema <schema>]
./bin/people-finder mcp        # stdio JSON-RPC: compile_people_queries, rank_people_candidates, start_people_research
```

Emitting schemas: `people-queries.v1`, `people-candidates.v1`,
`recorded-serp.v1`.

## Fixed inputs and limits

- One supplied seeker profile plus exactly **one** target job card per compile.
  There is no batch mode, no queue and no scheduler inside the plugin.
- The supplied results document is the only lead input. The plugin never
  fetches, never paginates and never retries a provider.
- `import-exa` merges **one** recorded provider envelope per call. Repeat calls
  are deduplicated; the envelope is recall evidence, never liveness.
- Ordering, counts and `paths[]` are deterministic for identical input, so the
  same supplied files always reproduce the same ranked document.

## Provenance and miss reasons

Every emitted document carries the paths that fired, the observed evidence
(`url_observed_in`, `headline_observed`, `score_breakdown`), the supplied source
labels, and an explicit `unknowns[]`. When nothing qualifies, the honest output
is few or zero candidates with the miss stated in `unknowns[]` and `counts` —
never an invented person, headline, employer or relationship.

Second-degree warmth is only ever the `shared_stamp` public-stamp proxy and is
labelled `public_stamp_proxy`; it never claims a member-graph edge.

## Boundary — what this plugin must never gain

- No generic people search, browsing, scraping or provider client: A public
  `/in/` URL is a lead, not a contact, not a verified identity and not a
  delivery route.
- No identity confirmation from a URL, title, snippet, score or shared stamp;
  identity is a later, separate step on one human-selected name.
- No approval, messaging, sending, applying, publishing, scheduling or any
  other external action, and no code path that writes another tool's state.
- No social-network session, cookie, browser automation or third-party actor.
- No credentials, API keys or network calls anywhere in the runtime.
- No host-state coupling: the plugin persists nothing. It is therefore
  registered **without** a data directory — the MCP entry passes only `mcp`,
  and no `${PLUGIN_DATA}` argument is used, because fabricating a store would
  fabricate a capability. It also never reads a sibling tool's `PLUGIN_DATA`.

## Host-owned email discovery (handoff, not capability)

Work-email discovery stays outside this plugin. For an approved fixed list, the
host may run one bounded Fiber/Exa Agent lookup per already-identified person
and hand each normalized result to `contact-brief`; the host owns concurrency,
spend, resumable journaling and raw-provider retention. people-finder adds no
Fiber credentials, provider calls, mailbox checks or automatic contact writes,
and a candidate output is never proof of contactability. Provider-reported
addresses remain `provider_reported`/`not_checked`, never a verified mailbox.

## Evidence labels

When reporting results, label each record honestly:

- `live` — retrieved during the current run by a named host route;
- `replay` — a recorded prior response re-presented (for example an
  `import-exa` envelope);
- `fixture` — synthetic deterministic data used for offline checks.

Fixtures used by the packaging and benchmark checks are fictional and are never
reported as live discovery.

## Packaging and install

The portable package is the repository root: `plugin.json`, `mcp.json`,
`skills/people-finder/SKILL.md`, `bin/people-finder`, `src/people_finder/`.
Clients that implement Agent Plugins read those fixed locations directly. For a
client that needs a rendered pointer instead, `compat/hermes/config.yaml.template`
is the thin Hermes adapter.

Reference material at the repository root: `README.md` (operations, gate),
`ARCHITECTURE.md` (design freeze), `BENCHMARK.md` (reviewer-owned offline
acceptance contract), `E2E_BENCHMARK.md` (reviewer-owned live gate), `SCORE.md`
(scoring).

Offline verification of this package (manifest, registration, skill, install,
uninstall, keep-outs):

```sh
python3 tests/plugin/check_plugin_packaging.py       # deterministic, offline
python3 tests/plugin/check_hermes_host_probe.py      # real isolated Hermes host probe
```
