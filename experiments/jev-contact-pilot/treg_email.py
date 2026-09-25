#!/usr/bin/env python3
"""Find work emails only for Jev-selected people; keep provider evidence private."""

import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from treg_key import read_key
import re

API = "https://treg.to/call/treg.people.email.find"


def find_email(person, domain, token, max_cost):
    payload = {"full_name": person["name"], "domain": domain,
               "linkedin_url": person["linkedin_url"]}
    request = Request(API, json.dumps(payload).encode(), {
        "X-Treg-Token": token, "X-Treg-Route-Max-Cost": str(max_cost),
        "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=90) as response:
        return {"request": payload, "call_id": response.headers.get("X-Treg-Call-Id"),
                "cost_usd": int(response.headers.get("X-Treg-Cost-Micro", "0")) / 1_000_000,
                "body": json.load(response)}


def select_people(result, candidate_ids):
    allowed = set(result["shortlist"])
    if candidate_ids:
        allowed = set(candidate_ids)
    if not allowed or len(allowed) > 2:
        raise ValueError("Select one or two Jev-ranked candidate IDs")
    indexed = {row["candidate_id"]: row for row in result["ranked"]}
    if allowed - indexed.keys():
        raise ValueError("Candidate ID is absent from Jev ranking")
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", id) or ".." in id for id in allowed):
        raise ValueError("Unsafe candidate ID")
    return [indexed[id] for id in sorted(allowed)]


def run(spec, result, out_dir, token, max_cost, candidate_ids=(), transport=find_email):
    if not token:
        raise ValueError("TREG_TOKEN is required")
    domain = spec["job"]["domain"]
    if result["job"]["company"] != spec["job"]["company"]:
        raise ValueError("Jev result belongs to another company")
    people = select_people(result, candidate_ids)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for person in people:
        path = out_dir / (person["candidate_id"] + "-treg-email.json")
        if path.exists():
            recording = json.loads(path.read_text(encoding="utf-8"))
            if recording.get("request") != {"full_name": person["name"], "domain": domain,
                                           "linkedin_url": person["linkedin_url"]}:
                raise ValueError(f"Existing recording belongs to another person: {path}")
        else:
            # An uncertain dispatch is deliberately not repeated after a crash.
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"dispatch": "uncertain", "request": {
                    "full_name": person["name"], "domain": domain,
                    "linkedin_url": person["linkedin_url"]}}, handle, indent=2)
            recording = transport(person, domain, token, max_cost)
            path.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8")
        if recording.get("dispatch") == "uncertain":
            raise RuntimeError(f"Uncertain prior dispatch; inspect {path} before retrying")
        output = recording.get("body", {}).get("output", {})
        rows.append({"candidate_id": person["candidate_id"], "name": person["name"],
                     "linkedin_url": person["linkedin_url"], "email": output.get("email"),
                     "provider_verified": output.get("verified"), "call_id": recording.get("call_id"),
                     "cost_usd": recording.get("cost_usd"), "recording": str(path)})
    summary = out_dir / "treg-email-results.json"
    summary.write_text(json.dumps({"job": spec["job"], "people": rows}, indent=2) + "\n", encoding="utf-8")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("jev_results", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--candidate-id", action="append", default=[], help="Choose one or two Jev-ranked IDs; default is Jev shortlist")
    parser.add_argument("--max-cost", type=float, default=0.05)
    args = parser.parse_args()
    token = os.environ.get("TREG_TOKEN", "").strip() or read_key()
    if not token and os.isatty(0):
        token = getpass.getpass("Treg token (hidden): ").strip()
    if not 0 < args.max_cost <= 1:
        parser.error("--max-cost must be in (0, 1] USD")
    rows = run(json.loads(args.spec.read_text(encoding="utf-8")),
               json.loads(args.jev_results.read_text(encoding="utf-8")),
               args.out_dir, token, args.max_cost, args.candidate_id)
    for row in rows:
        print(f"{row['name']} | {row['linkedin_url']} | {row['email'] or 'no email found'}")


if __name__ == "__main__":
    main()
