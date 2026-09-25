# Treg + Jev contact selection pilot

This is a **host experiment** on the `experiment/jev-contact-pilot` branch.
The code lives under `experiments/` so the People Finder runtime remains offline.
Keep real job inputs, provider records, results and credentials outside this repo.

## Default host path for new jobs

Use `treg_jev.py` for one job at a time. Treg discovers public profiles using
bounded company-domain/title searches; the runner records every response and
call cost, requires the matching company domain and a public LinkedIn profile,
deduplicates, and sends the eligible candidates to Jev. Jev returns a ranked
list and a peer/hiring shortlist. Every shortlist entry requires human review.
An uncertain peer-versus-hiring lane stays marked for review; it does not
discard a relevant person when Jev assigns very little weight to `neither`.
This is the experiment's host workflow, not a network capability added to the
portable people-finder plugin.

Create a private spec outside the repository:

```json
{
  "profile_summary": "The seeker's relevant background",
  "job": {
    "company": "ExampleCo",
    "domain": "example.com",
    "title": "People Strategy",
    "summary": "Role duties copied or summarized from the source posting"
  },
  "search_titles": ["People", "Talent", "CEO"]
}
```

Run in Ubuntu/WSL:

```sh
python3 treg_jev.py "/private/job-spec.json" --out-dir "/private/job-run"
```

The script reads `TREG_TOKEN` from the process environment or prompts for it.
For Jev, it reads `OPENROUTER_API_KEY`, then the locally saved key, then prompts.
To save the OpenRouter key once, run `python3 openrouter_key.py` in Ubuntu and
paste it at the hidden prompt. This writes `~/.config/jobsss/openrouter.key`
outside Git with owner-only permissions (0600). The key is local plaintext;
protect your Ubuntu account and do not put the key in chat or the job spec.
Default Treg search
cap is $0.03 per query; use `--max-cost` to change it. To inspect provider
results before spending on Jev, use `--discover-only`. Resume from its saved
`jev-input.json` without repeating Treg searches using `--rank-only`:

```sh
python3 treg_jev.py "/private/job-spec.json" --out-dir "/private/job-run" --rank-only
```

For a recorded Treg response, use `--recordings "/private/recordings.json"`
with a JSON array containing one response per search title. This is replay,
not a new live discovery. The runner does not find emails or send outreach.

The existing offline people-finder `compile` and `rank` commands remain
available for a separately controlled comparison. This host runner applies its
own strict current-company-domain and public-profile gates before Jev; it does
not invoke people-finder's full typed-anchor ranker.

`jev_rank.py` takes a JSON document with `profile_summary`, `job` (company,
title, summary), and `candidates[]` (candidate_id, name, current_company,
current_title, linkedin_url, evidence). It uses [OpenRouter's Jev Decisions API](https://openrouter.ai/blog/tutorials/how-to-use-jev/).
It prompts for an OpenRouter key in an interactive terminal, or reads
`OPENROUTER_API_KEY` in automation. The model is pinned to
`typesafe/jev-1.13` so the pilot can be compared across runs:

From this experiment directory, run both inputs with one hidden key prompt:

```sh
bash run_pilot.sh "$RUN_DIR" --check
bash run_pilot.sh "$RUN_DIR"
```

`RUN_DIR` is the private directory containing `inputs/vapi.json` and
`inputs/notion.json`. The launcher writes both result files there, outside Git.

Create a key at [OpenRouter API Keys](https://openrouter.ai/settings/keys), then
use the one-time save command above or enter it at the live hidden prompt.
For noninteractive automation, set `OPENROUTER_API_KEY` using your secret
manager. Never paste the key into chat, commit it, or put it in pilot JSON.

Use `--responses private/recorded-jev.json` to replay recorded responses without
network access. The script only evaluates supplied candidates; it cannot
discover identities, find emails, approve outreach, or write plugin state.

The first live Treg/Exa run is documented in the user's private Job Search
research directory. The Garage live Treg discovery and Jev input are in a
separate private run directory dated 2026-09-25.
