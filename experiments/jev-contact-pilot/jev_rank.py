#!/usr/bin/env python3
"""Host-side Jev experiment: rank supplied, already discovered people.

This deliberately does not search for people, find emails, or write JobSSS state.
Input and output are JSON files; the OpenRouter key stays in the environment.
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from openrouter_key import read_key

API = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"


def question_set():
    return {
        "role_fit": {
            "type": "score",
            "instructions": "How relevant is this person's current work to the target job's work? Judge only supplied evidence.",
            "criteria": ["No relevant current work", "Adjacent work", "Directly relevant work"],
        },
        "shared_context": {
            "type": "score",
            "instructions": "How strong is the seeker-specific, evidence-backed reason for a professional conversation? Do not infer a personal connection.",
            "criteria": ["No specific shared context", "Plausible professional overlap", "Strong, specific overlap"],
        },
        "route": {
            "type": "choice",
            "instructions": "Which contact role is supported by this person's CURRENT title and evidence?",
            "criteria": {
                "peer": "Does similar work at the target employer and could offer role insight",
                "hiring": "Recruiting, talent, founder, or likely hiring decision-maker for this role",
                "neither": "Neither role is supported by the supplied evidence",
            },
        },
        "evidence": {
            "type": "score",
            "instructions": "How sufficient is the supplied evidence to justify selecting this exact person?",
            "criteria": ["Thin or contradictory", "Some direct evidence", "Clear current role and employer evidence"],
        },
    }


def jev_call(state, key):
    payload = json.dumps({"model": MODEL, "state": state, "questions": question_set()}).encode()
    request = Request(API, payload, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with profile_summary, job, candidates[]")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--responses", type=Path, help="offline replay: JSON mapping candidate_id to Jev response")
    args = parser.parse_args()
    doc = json.loads(args.input.read_text(encoding="utf-8"))
    job = doc["job"]
    candidates = doc["candidates"]
    if len(candidates) > 30:
        parser.error("limit is 30 candidates per job")
    replay = json.loads(args.responses.read_text(encoding="utf-8")) if args.responses else None
    key = (os.environ.get("OPENROUTER_API_KEY") or read_key()) if replay is None else None
    if replay is None and not key:
        if not sys.stdin.isatty():
            parser.error("OPENROUTER_API_KEY or a saved ~/.config/jobsss/openrouter.key is required for noninteractive live Jev calls")
        key = getpass.getpass("OpenRouter API key (hidden; not saved): ").strip()
        if not key:
            parser.error("an OpenRouter API key is required for live Jev calls")
    results = []
    for person in candidates:
        if person.get("current_company", "").casefold() != job["company"].casefold():
            continue
        url = person.get("linkedin_url", "")
        if not url.startswith(("https://www.linkedin.com/in/", "https://linkedin.com/in/")):
            continue
        state = {
            "seeker": doc["profile_summary"],
            "job": {"company": job["company"], "title": job["title"], "summary": job["summary"]},
            "person": {"name": person["name"], "current_title": person["current_title"], "evidence": person["evidence"]},
        }
        if len(json.dumps(state)) > 10000:
            parser.error(f"candidate {person['candidate_id']} exceeds the 10,000-character state limit")
        answer = replay[person["candidate_id"]] if replay is not None else jev_call(state, key)
        a = answer["answers"]
        route = a["route"]["choice"]
        route_confidence = a["route"].get("confidence")
        probabilities = a["route"].get("probabilities") or {}
        lane_ambiguous = route_confidence is not None and route_confidence < 0.6
        # Ambiguity between peer and hiring affects the outreach route, but
        # both are useful contacts. Hold a person only when "neither" remains
        # plausible, or when no distribution was returned to inspect.
        route_needs_review = lane_ambiguous and (
            not probabilities or probabilities.get("neither", 0) >= 0.2
        )
        # These are rubric scores, not calibrated probabilities of contact success.
        score = round(0.5 * a["role_fit"]["score"] + 0.3 * a["shared_context"]["score"] + 0.2 * a["evidence"]["score"], 4)
        results.append({"candidate_id": person["candidate_id"], "name": person["name"], "linkedin_url": url,
                        "lane": route, "score": score, "jev": answer,
                        "route_confidence": route_confidence,
                        "lane_ambiguous": lane_ambiguous,
                        "route_needs_review": route_needs_review,
                        "requires_human_review": True})
    results.sort(key=lambda row: (row["lane"] != "peer", row["lane"] != "hiring", -row["score"], row["candidate_id"]))
    # Keep the peer and hiring paths distinct. A person whose "neither"
    # probability remains material waits for review before shortlisting.
    peer = next((row for row in results if row["lane"] == "peer" and not row["route_needs_review"]), None)
    hiring = next((row for row in results if row["lane"] == "hiring" and not row["route_needs_review"]), None)
    shortlist = [row for row in (peer, hiring) if row is not None]
    output = {"job": job, "model": MODEL, "method": "Jev rubric pilot; scores comparable only within lane",
              "ranked": results, "shortlist": [row["candidate_id"] for row in shortlist],
              "selected": results, "email_lookup_authorized_by_this_file": False}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
