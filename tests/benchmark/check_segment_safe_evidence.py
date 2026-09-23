#!/usr/bin/env python3
"""Deterministic regression for segment-safe positional evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import product_command
RESUME = ROOT / "tests" / "fixtures" / "resumes" / "a-rich-stamps.md"
AT = "2026-09-18T00:00:00Z"


def env_without_credentials():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(key, None)
        if upper in {"PLUGIN_DATA", "DATABASE_URL"}:
            env.pop(key, None)
    return env


def run(argv):
    return subprocess.run(
        [str(item) for item in argv],
        cwd=ROOT,
        env=env_without_credentials(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def main():
    assertions = []

    def check(identifier, passed, evidence):
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    with tempfile.TemporaryDirectory(prefix="people-finder-segment-safe-") as raw_workdir:
        workdir = Path(raw_workdir)
        job_path = workdir / "job.json"
        queries_path = workdir / "queries.json"
        results_path = workdir / "results.json"
        candidates_path = workdir / "candidates.json"

        write_json(job_path, {
            "schema": "job-card.v1",
            "fictional": True,
            "job_id": "segment-safe-synthetic",
            "title": "Senior Data Platform Engineer",
            "company": "Helioscale",
            "department": "Data Platform",
            "location": "Remote",
            "posting_text": "Synthetic segment-safety role.",
        })
        compiled = run(product_command(
            "compile", "--resume", RESUME, "--job", job_path,
            "--at", AT, "--out", queries_path, "--quiet",
        ))
        queries = json.loads(queries_path.read_text(encoding="utf-8")) if compiled.returncode == 0 else {}

        rows = [
            {
                "rank": 1,
                "url": "https://www.linkedin.com/in/alice-cross-title",
                "title": (
                    "Alice Cross - Helioscale | LinkedIn"
                    "Bob Builder - Senior Data Platform Engineer at Helioscale | LinkedIn"
                ),
                "snippet": (
                    "Alice Cross - Helioscale. "
                    "Bob Builder is a Senior Data Platform Engineer at Helioscale."
                ),
            },
            {
                "rank": 2,
                "url": "https://www.linkedin.com/in/carol-cross-snippet",
                "title": "Carol Cross - Helioscale | LinkedIn",
                "snippet": (
                    "Experience: Helioscale · Education: Northwind Institute of Technology · "
                    "View Carol Cross’s profile on LinkedIn. "
                    "Bob Builder is a Senior Data Platform Engineer at Helioscale."
                ),
            },
            {
                "rank": 3,
                "url": "https://www.linkedin.com/in/drew-cross-body",
                "title": "Drew Cross - Helioscale | LinkedIn",
                "snippet": (
                    "Drew Cross profile. Experience: Helioscale · Education: Northwind "
                    "Institute of Technology · Senior Data Platform Engineer at Helioscale. "
                    "Experience: OtherCo · Education: Other University."
                ),
            },
            {
                "rank": 4,
                "url": "https://www.linkedin.com/in/erin-valid-title",
                "title": "Erin Valid - Senior Data Platform Engineer at Helioscale | LinkedIn",
                "snippet": "Current Helioscale engineer. Experience: Helioscale · Education: University.",
            },
            {
                "rank": 5,
                "url": "https://www.linkedin.com/in/frank-valid-body",
                "title": "Frank Valid - Helioscale | LinkedIn",
                "snippet": (
                    "Frank Valid is a Senior Data Platform Engineer at Helioscale. "
                    "He works on current platform systems."
                ),
            },
            {
                "rank": 6,
                "url": "https://www.linkedin.com/in/gina-valid-segment",
                "title": (
                    "Gina Valid - Senior Data Platform Engineer at Helioscale | LinkedIn"
                    "Hank Other - Product Manager at Helioscale | LinkedIn"
                ),
                "snippet": (
                    "Gina Valid is a Senior Data Platform Engineer at Helioscale. "
                    "Hank Other is a Product Manager at Helioscale."
                ),
            },
            {
                "rank": 7,
                "url": "https://www.linkedin.com/in/hannah-valid-body",
                "title": "Hannah Valid - Helioscale | LinkedIn",
                "snippet": (
                    "Experience: Helioscale · Education: Northwind Institute of Technology · "
                    "Hannah Valid is a Senior Data Platform Engineer at Helioscale."
                ),
            },
        ]
        write_json(results_path, {
            "schema": "recorded-serp.v1",
            "fixture": "segment-safe-adversarial",
            "backend": "synthetic_public_index",
            "live_network": False,
            "retrieved_at": AT,
            "pack_results": [{
                "pack_id": "function_at_target",
                "query": '"Senior Data Platform Engineer" "Helioscale" site:linkedin.com/in',
                "retrieved_at": AT,
                "results": rows,
            }],
        })

        ranked = run(product_command(
            "rank", "--queries", queries_path, "--results", results_path,
            "--at", AT, "--out", candidates_path, "--quiet",
        ))
        document = json.loads(candidates_path.read_text(encoding="utf-8")) if ranked.returncode == 0 else {}
        candidates = document.get("candidates", [])
        names = [item.get("name") for item in candidates]
        urls = {item.get("public_url") for item in candidates}
        suppressed = {item.get("url"): item.get("reason") for item in document.get("suppressed", [])}

        check("S1-compile-and-rank", compiled.returncode == 0 and ranked.returncode == 0, {
            "compile_returncode": compiled.returncode,
            "compile_stderr": compiled.stderr[-500:],
            "rank_returncode": ranked.returncode,
            "rank_stderr": ranked.stderr[-500:],
        })
        check("S2-cross-profile-title-rejected", "Alice Cross" not in names, {
            "selected_names": names,
            "suppressed_reason": suppressed.get("https://www.linkedin.com/in/alice-cross-title"),
        })
        check("S3-cross-profile-view-snippet-rejected", "Carol Cross" not in names, {
            "selected_names": names,
            "suppressed_reason": suppressed.get("https://www.linkedin.com/in/carol-cross-snippet"),
        })
        check("S4-cross-profile-structured-snippet-rejected", "Drew Cross" not in names, {
            "selected_names": names,
            "suppressed_reason": suppressed.get("https://www.linkedin.com/in/drew-cross-body"),
        })
        check(
            "S5-valid-title-and-body-survive",
            {"Erin Valid", "Frank Valid", "Gina Valid", "Hannah Valid"}.issubset(set(names))
            and {
                "https://www.linkedin.com/in/erin-valid-title",
                "https://www.linkedin.com/in/frank-valid-body",
                "https://www.linkedin.com/in/gina-valid-segment",
                "https://www.linkedin.com/in/hannah-valid-body",
            }.issubset(urls),
            {"selected_names": names, "selected_urls": sorted(urls)},
        )

        gina = next((item for item in candidates if item.get("name") == "Gina Valid"), {})
        gina_text = json.dumps(gina, ensure_ascii=False)
        check("S6-output-retains-bound-first-profile-evidence", (
            "Gina Valid - Senior Data Platform Engineer at Helioscale" in gina.get("headline_observed", "")
            and "Hank Other" not in gina.get("headline_observed", "")
            and "Hank Other" not in gina_text
        ), {
            "headline_observed": gina.get("headline_observed"),
            "function_evidence": (gina.get("eligibility") or {}).get("function_evidence"),
        })

        check("S7-bad-positional-text-never-selected", all(
            bad_url not in urls for bad_url in (
                "https://www.linkedin.com/in/alice-cross-title",
                "https://www.linkedin.com/in/carol-cross-snippet",
                "https://www.linkedin.com/in/drew-cross-body",
            )
        ), {
            "bad_urls": [
                "https://www.linkedin.com/in/alice-cross-title",
                "https://www.linkedin.com/in/carol-cross-snippet",
                "https://www.linkedin.com/in/drew-cross-body",
            ],
            "selected_urls": sorted(urls),
            "suppressed_reasons": {
                key: suppressed.get(key) for key in sorted(suppressed)
                if "cross-" in key
            },
        })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "S",
        "offline": True,
        "passed": all(item["passed"] for item in assertions),
        "assertions": assertions,
        "test_count": len(assertions),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("check_segment_safe_evidence harness error: %r\n" % (exc,))
        print(json.dumps({
            "benchmark": "people-finder-v1",
            "criterion": "S",
            "offline": True,
            "passed": False,
            "assertions": [{"id": "HARNESS", "passed": False, "evidence": {"error": repr(exc)}}],
            "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)
