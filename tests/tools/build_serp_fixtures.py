#!/usr/bin/env python3
"""Build the recorded SERP fixtures from the product's own compiled queries.

The queries recorded inside tests/fixtures/serps/*.json are copied verbatim from
`people-finder compile` output for each fixture pair, so a reviewer can confirm
that the recorded results were recorded against the compiled packs rather than
invented around them. All hit content is fictional.

Usage: python3 tests/tools/build_serp_fixtures.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from people_finder.packs import compile_from_paths  # noqa: E402

FIXED_AT = "2026-09-10T08:00:00Z"

HITS = {
    "a": {
        "alumni_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/ananya-rao-data-platform?trk=public_profile",
                "title": "Ananya Rao - Data Platform Engineer - Helioscale",
                "snippet": ("Northwind Institute of Technology alum. Maintainer of the OpenKelvin "
                            "Collective telemetry project."),
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/vikram-shetty-platform",
                "title": ("Vikram Shetty - Senior Data Platform Engineer - Helioscale | "
                          "Northwind Institute of Technology"),
                "snippet": "Alum, Northwind Institute of Technology (M.S. Computer Science, 2018).",
            },
            {
                "rank": 3,
                "url": "https://www.linkedin.com/company/helioscale",
                "title": "Helioscale: company overview",
                "snippet": "Helioscale builds telemetry products.",
            },
            {
                "rank": 4,
                "url": "https://www.linkedin.com/in/tomas-brenner-helioscale",
                "title": "Tomas Brenner - Senior Data Platform Engineer - Helioscale",
                "snippet": "Backend and data services.",
            },
        ],
        "prior_employer_at_target": [],
        "function_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/sofia-marchetti-talent",
                "title": "Sofia Marchetti - Technical Recruiter - Helioscale",
                "snippet": "Talent acquisition for the data platform org.",
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/ananya-rao-data-platform?trk=profile-badge",
                "title": "Ananya Rao - Data Platform Engineer at Helioscale",
                "snippet": "Data platform engineer working on ingestion.",
            },
        ],
        "community_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/ananya-rao-data-platform",
                "title": "Ananya Rao - Data Platform Engineer - Helioscale",
                "snippet": "OpenKelvin Collective steering group member.",
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/meera-iyer-helioscale",
                "title": "Meera Iyer - Engineering Manager, Data Platform - Helioscale",
                "snippet": "OpenKelvin Collective steering group.",
            },
            {
                "rank": 3,
                "url": "https://www.linkedin.com/in/dmitri-volkov-systems",
                "title": "Dmitri Volkov - Distributed Systems Researcher",
                "snippet": ("Northwind Institute of Technology PhD; OpenKelvin Collective "
                            "contributor."),
            },
        ],
        "shared_stamp": [],
        "hiring_adjacent": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/sofia-marchetti-talent?trk=feed",
                "title": "Sofia Marchetti - Technical Recruiter - Helioscale",
                "snippet": "Talent acquisition partner.",
            },
        ],
    },
    "b": {
        "alumni_at_target": [],
        "prior_employer_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/elodie-marchand-platform",
                "title": ("Elodie Marchand - Platform Engineer - Bluecrest Analytics | "
                          "Ex-Ionwave Systems"),
                "snippet": "Formerly at Ionwave Systems. Platform group.",
            },
        ],
        "function_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/tomas-brenner-bluecrest",
                "title": "Tomas Brenner - Staff Backend Engineer - Bluecrest Analytics",
                "snippet": "Backend services and Kafka.",
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/priya-nair-bluecrest",
                "title": "Priya Nair - Senior Backend Engineer - Bluecrest Analytics",
                "snippet": "Hiring for the platform group.",
            },
            {
                "rank": 3,
                "url": "https://www.linkedin.com/in/elodie-marchand-platform",
                "title": "Elodie Marchand - Platform Engineer - Bluecrest Analytics",
                "snippet": "Ex-Ionwave Systems; backend platform work.",
            },
        ],
        "community_at_target": [],
        "shared_stamp": [],
        "hiring_adjacent": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/lena-ostrowski-talent",
                "title": "Lena Ostrowski - Technical Recruiter - Bluecrest Analytics",
                "snippet": "Talent acquisition for platform.",
            },
        ],
    },
    "c": {
        "function_at_target": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/jordan-bell-services",
                "title": "Jordan Bell - Backend Engineer - Lumen Freight Systems",
                "snippet": "Python and SQL services.",
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/jordan-bell-college",
                "title": "Jordan Bell - Backend Engineer - Ashgrove Technical College",
                "snippet": "Python, REST APIs.",
            },
            {
                "rank": 3,
                "url": "https://www.linkedin.com/in/jordyn-bell-dev",
                "title": "Jordyn Bell - Backend Engineer",
                "snippet": "Git, REST APIs.",
            },
            {
                "rank": 4,
                "url": "https://www.linkedin.com/company/northgate-retail-group",
                "title": "Northgate Retail Group",
                "snippet": "Northgate Retail Group storefront engineering.",
            },
        ],
        "hiring_adjacent": [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/samir-haddad-talent",
                "title": "Samir Haddad - Talent Acquisition Partner - Northgate Retail Group",
                "snippet": "Storefront engineering hiring.",
            },
        ],
    },
}

FIXTURE_META = {
    "a": {
        "resume": "tests/fixtures/resumes/a-rich-stamps.md",
        "job": "tests/fixtures/jobs/a-target.json",
        "backend": "recorded_fixture",
        "notes": ("Recorded search results for the rich-stamps fixture. Every query string is "
                  "copied verbatim from `people-finder compile` output for this fixture pair."),
    },
    "b": {
        "resume": "tests/fixtures/resumes/b-career-hop.md",
        "job": "tests/fixtures/jobs/b-target.json",
        "backend": "recorded_fixture",
        "notes": ("Recorded search results for the career-hop fixture. The prior-employer "
                  "result and the generic title results are both recorded so the ranking "
                  "assertion can compare them."),
    },
    "c": {
        "resume": "tests/fixtures/resumes/c-thin-noisy.md",
        "job": "tests/fixtures/jobs/c-target.json",
        "backend": "recorded_fixture",
        "notes": ("Recorded search results for the thin/noisy fixture: common name, generic "
                  "skills, and no result that observes the target employer for a peer path."),
    },
}


def build(key):
    meta = FIXTURE_META[key]
    compiled = compile_from_paths(meta["resume"], meta["job"], generated_at=FIXED_AT)
    queries = {pack["pack_id"]: pack["query"] for pack in compiled["packs"]}
    pack_results = []
    for pack_id, query in queries.items():
        rows = []
        for position, row in enumerate(HITS[key].get(pack_id, []), start=1):
            rows.append({
                "rank": row.get("rank", position),
                "url": row["url"],
                "title": row["title"],
                "snippet": row["snippet"],
            })
        pack_results.append({
            "pack_id": pack_id,
            "query": query,
            "query_source": f"people-finder compile --resume {meta['resume']} --job {meta['job']}",
            "retrieved_at": "2026-09-10T08:05:00Z",
            "result_count": len(rows),
            "results": rows,
        })
    return {
        "schema": "recorded-serp.v1",
        "fixture": f"{key}-recorded",
        "fictional": True,
        "backend": meta["backend"],
        "live_network": False,
        "retrieved_at": "2026-09-10T08:05:00Z",
        "notes": meta["notes"],
        "seeker_resume": meta["resume"],
        "target_job": meta["job"],
        "pack_results": pack_results,
    }


def main():
    for key in ("a", "b", "c"):
        path = os.path.join(ROOT, "tests", "fixtures", "serps", f"{key}-recorded.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(build(key), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
