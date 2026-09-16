# People Finder — architecture freeze

Sibling of contact-brief. Jobsss is the later sink, never the search engine. contact-brief is downstream (one named person), never the caller.

## What it does

From a seeker profile + one target job, emit a small ranked set of **candidate leads** (public LinkedIn URLs + observed **typed anchors** + which **path** fired + unknowns). School × employer × function is one path, not the product.

Three states, never collapsed:

1. **discovery** — queries + SERP/Exa hits (`people-candidates.v1`)
2. **identity** — contact-brief on one selected name
3. **approval** — human Jobsss `import_contact` / `record_research`

A `/in/` URL is a lead, not a contact and not a messaging channel.

## Ranking: sparse typed anchors, not embeddings

Do **not** embed the resume and kNN over LinkedIn. We have no member graph, snippets are thin, and opaque cosine scores become fake identity. The honest “vector” is a **sparse bag of typed anchors** extracted from the seeker, matched against tokens observed in a hit’s title/snippet/URL.

**Anchor types** (seeker side, from resume + job):

| Type | Examples | Weight idea |
|---|---|---|
| rare community | lab, OSS org, conference, paper, niche degree program | highest |
| prior employer | companies the seeker actually worked at | high |
| school | university / college; program if present | high if rare, downweight generic |
| function | role family, dept keywords from the *job* | medium |
| target employer | the company being applied to | required filter for most paths, not a score by itself |
| location | city/region | low, optional |
| skill | tools; only rare ones | default off — “Python” drowns the list |

**Query packs** (compile several, tag every hit with `paths[]`):

- `alumni_at_target` — school/program × target company
- `prior_employer_at_target` — seeker’s past companies × target (true “someone like me already there”)
- `function_at_target` — role/dept × target
- `community_at_target` — lab/OSS/conference × target
- `hiring_adjacent` — TA/recruiter/HM at target (**separate lane**, never mixed with peers)
- `shared_stamp` — two public stamps without requiring current employer (second-degree *proxy*)

**Score** = linear combination of typed overlaps, with IDF-style downweight on common tokens, a cap per path so 40 generic engineers don’t bury 2 lab-mates, and explicit `unknowns[]`. Exa may add recall; it does not replace the score.

**Second-degree:** LinkedIn’s real graph is out (no Recruiter/Sales Nav, no scrape). Approximate warmth as **shared public stamps** (same lab, same prior company, same OSS org). That is “you both left this mark on the public web,” not “you’re 2nd degree on LinkedIn.” Label it `shared_stamp`, never `connected`.

## Modality

- **Agent Plugin skill** — workflow, backend choice, evidence rules
- **MCP** — `compile_people_queries`, `rank_people_candidates`, `start_people_research` (accepts **supplied** search results; no required network)
- **CLI** — same operations on JSON files
- **Zero-dep core** — extract anchors, compile packs, filter `linkedin.com/in`, normalize, dedupe, score, validate

Search backends run on the **host**, not in the core. No API key in the plugin. People-finder never opens Jobsss `PLUGIN_DATA`.

## Exa (optional, contact-brief style)

Host Codex/Hermes Exa plugin → normalized envelope → `import-exa`. No `EXA_API_KEY` on that route. Direct Agent API is a journaled fallback. Exa is people/query **recall**, not identity and not email.

## Tests (v0)

Three fictional resume MDs + job cards + **recorded SERPs** (live Exa opt-in, not the default gate):

| Fixture | What must happen |
|---|---|
| A — rich stamps | school + lab/community + target company; alumni and community paths fire |
| B — career hop | prior employer now at target; `prior_employer_at_target` outranks generic title matches |
| C — thin / noisy | common name, generic skills only; ranker returns few/none and does not invent people |

**Jobsss composition (temp profiles, required):** a host harness with isolated temp `PLUGIN_DATA` + temp people-finder out dir. Sequence:

1. `jobsss` `start` → `create_profile` from fixture resume → `import_job`
2. people-finder compile + rank on **fixture SERP** (no live net in the default gate)
3. mapping helper turns selected candidates into `import_contact` / `record_research` **args**
4. harness (not people-finder) calls Jobsss MCP on the temp store
5. `list_contacts` shows unapproved records; `map_reachable_network` sees them
6. assert people-finder never read/wrote `PLUGIN_DATA`

That proves the sibling plugin is callable next to Jobsss without coupling runtimes.

## Later Jobsss loop

Host composition: Jobsss network intent → people-finder → human selects → contact-brief → human approves writes. Frozen `start_people_research` stays a cross-plugin intent.

## Keep-out

LinkedIn scrape/cookies/Sales Nav; HarvestAPI/Apify; required keys; embeddings-as-identity; auto-import; PDL until licensed; finder code inside `./bin/jobsss`; claiming LinkedIn 2nd-degree.
