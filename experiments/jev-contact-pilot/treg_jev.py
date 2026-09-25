#!/usr/bin/env python3
"""Host-side Treg discovery followed by Jev ranking for one target job.

The people-finder plugin remains offline. Provider responses and contact research
belong in a private output directory outside the repository.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from treg_key import read_key


TREG_API = "https://treg.to/call/treg.people.search"


def host(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    return (urlparse(value).hostname or "").removeprefix("www.")


def linkedin(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.hostname not in ("linkedin.com", "www.linkedin.com"):
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    return "https://www.linkedin.com/in/" + parts[1] if len(parts) == 2 and parts[0] == "in" else ""


def normalize_people(recording, job, query_index):
    body = recording.get("body", recording)
    routed = body.get("_treg", {})
    rows = body.get("output", {}).get("people", [])
    candidates = []
    allowed_domains = {host(job["domain"]), *(host(value) for value in job.get("domain_aliases", []))}
    for row in rows:
        name = row.get("fullName") or row.get("full_name") or " ".join(
            filter(None, [row.get("firstname") or row.get("firstName") or row.get("first_name"),
                          row.get("lastname") or row.get("lastName") or row.get("last_name")])
        )
        title = row.get("lastJobTitle") or row.get("jobTitle") or row.get("title") or row.get("current_title")
        company = row.get("lastCompanyName") or row.get("company_name") or row.get("current_company")
        company_record = row.get("company") or {}
        if isinstance(company_record, dict):
            company = company or company_record.get("name")
        company_url = row.get("lastCompanyWebsite") or row.get("company_url")
        if isinstance(company_record, dict):
            company_url = company_url or company_record.get("website") or company_record.get("domain")
        profile = linkedin(row.get("profileUrl") or row.get("employee_linkedin") or row.get("linkedin_url"))
        # A generic company name like "Garage" is insufficient: require its domain
        # on the actual person row to exclude auto shops and clothing retailers.
        if not name or not title or not profile or host(company_url) not in allowed_domains:
            continue
        candidates.append({
            "candidate_id": profile.split("/in/")[1].lower(),
            "name": name.strip(),
            "current_company": job["company"],
            "current_title": title.strip(),
            "linkedin_url": profile,
            "evidence": (
                f"Treg people search call {recording.get('call_id', 'recorded')}, "
                f"provider {routed.get('served_by', 'unknown')}, query {query_index + 1}: "
                f"current title {title}; employer {company or job['company']} "
                f"with website {company_url}. Public profile URL supplied by provider."
            ),
        })
    return candidates


def treg_search(params, token, max_cost):
    payload = json.dumps(params).encode("utf-8")
    request = Request(TREG_API, payload, {
        "X-Treg-Token": token,
        "X-Treg-Route-Max-Cost": str(max_cost),
        "Content-Type": "application/json",
    }, method="POST")
    with urlopen(request, timeout=90) as response:
        return {
            "status": response.status,
            "call_id": response.headers.get("X-Treg-Call-Id"),
            "cost_usd": int(response.headers.get("X-Treg-Cost-Micro", "0")) / 1_000_000,
            "body": json.load(response),
        }


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="private JSON: profile_summary, job, search_titles")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--recordings", type=Path, help="replay a JSON array of recorded Treg call results")
    parser.add_argument("--discover-only", action="store_true", help="prepare Jev input without calling Jev")
    parser.add_argument("--rank-only", action="store_true", help="rank an existing jev-input.json without repeating Treg")
    parser.add_argument("--max-cost", type=float, default=0.03, help="Treg cap per search (default: $0.03)")
    args = parser.parse_args()
    if args.rank_only and args.recordings:
        parser.error("--rank-only cannot be combined with --recordings")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    output = args.out_dir
    output.mkdir(parents=True, exist_ok=True)
    jev_input = output / "jev-input.json"

    if not args.rank_only:
        job = spec["job"]
        if not job.get("domain") or not job.get("company"):
            parser.error("job.company and job.domain are required")
        titles = spec["search_titles"]
        if not 1 <= len(titles) <= 5:
            parser.error("supply 1-5 search_titles")
        recordings = json.loads(args.recordings.read_text(encoding="utf-8")) if args.recordings else None
        if recordings is not None and len(recordings) != len(titles):
            parser.error("recordings count must match search_titles count")
        token = os.environ.get("TREG_TOKEN") or read_key()
        if recordings is None and not token:
            if not sys.stdin.isatty():
                parser.error("TREG_TOKEN or a saved ~/.config/jobsss/treg.key is required for noninteractive discovery")
            token = getpass.getpass("Treg token (hidden; not saved): ").strip()
        found = []
        saved_calls = []
        for index, title in enumerate(titles):
            params = {"company_domain": job["domain"], "title": title, "limit": 8}
            recording = recordings[index] if recordings is not None else treg_search(params, token, args.max_cost)
            saved_calls.append(recording)
            found.extend(normalize_people(recording, job, index))
        write_json(output / "treg-recordings.json", saved_calls)
        unique = {}
        for person in found:
            # Duplicate provider rows can disagree on the URL. Keep one person
            # and preserve the first sourced URL for human identity review.
            unique.setdefault(person["name"].casefold(), person)
        if not unique:
            parser.error("Treg returned no eligible people with current-company domain and LinkedIn evidence")
        if len(unique) > 30:
            parser.error("more than 30 eligible candidates; narrow the search")
        write_json(jev_input, {
            "profile_summary": spec["profile_summary"],
            "job": {key: job[key] for key in ("company", "title", "summary")},
            "candidates": list(unique.values()),
        })
        print(f"Prepared {len(unique)} eligible people in {jev_input}")

    if not args.discover_only:
        result = subprocess.run([
            sys.executable, str(Path(__file__).with_name("jev_rank.py")),
            str(jev_input), "--out", str(output / "jev-results.json")
        ], check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
        print(f"Ranked people in {output / 'jev-results.json'}")


if __name__ == "__main__":
    main()
