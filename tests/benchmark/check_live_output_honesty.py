"""Additive live-output honesty check.

This check exercises the public rank/validate CLI boundary.  Search-result
prose is allowed to contain ordinary words that resemble relationship or
contact claims, while generated claims and forbidden truthy fields remain
rejected.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.path.join(ROOT, "bin", "people-finder")
RESUME = os.path.join(ROOT, "tests", "fixtures", "resumes", "a-rich-stamps.md")
JOB = os.path.join(ROOT, "tests", "fixtures", "jobs", "a-target.json")


def _env():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(key, None)
        if upper in {"PLUGIN_DATA", "DATABASE_URL"}:
            env.pop(key, None)
    return env


def _run(argv):
    return subprocess.run(
        argv, cwd=ROOT, env=_env(), capture_output=True, text=True, timeout=60
    )


def _assert(condition, message, evidence, assertions):
    assertions.append({"id": message.split(":", 1)[0], "passed": bool(condition), "evidence": evidence})
    if not condition:
        raise AssertionError(message)


def main():
    assertions = []
    os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="live-honesty-", dir=os.path.join(ROOT, ".tmp")) as scratch:
        queries_path = os.path.join(scratch, "queries.json")
        results_path = os.path.join(scratch, "results.json")
        candidates_path = os.path.join(scratch, "candidates.json")
        bad_path = os.path.join(scratch, "bad-candidates.json")

        compiled = _run([
            BIN, "compile", "--resume", RESUME, "--job", JOB,
            "--at", "2026-09-18T00:00:00Z", "--out", queries_path, "--quiet",
        ])
        if compiled.returncode != 0:
            raise RuntimeError(compiled.stderr)

        supplied = {
            "schema": "recorded-serp.v1",
            "fixture": "live-output-honesty",
            "backend": "synthetic_public_index",
            "live_network": True,
            "retrieved_at": "2026-09-18T00:00:00Z",
            "pack_results": [{
                "pack_id": "function_at_target",
                "query": '"Data Platform Engineer" "Helioscale" site:linkedin.com/in',
                "retrieved_at": "2026-09-18T00:00:00Z",
                "results": [{
                    "rank": 1,
                    "url": "https://www.linkedin.com/in/rowan-vale",
                    "title": "Rowan Vale - Data Platform Engineer at Helioscale - 300 connections",
                    "snippet": (
                        "Current Helioscale engineer; reachable through this public index; "
                        "contactable is only ordinary observed directory wording."
                    ),
                    "source_url": "https://search.example.test/?q=helioscale",
                    "observed_at": "2026-09-18T00:00:00Z",
                }],
            }],
        }
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(supplied, handle)

        ranked = _run([
            BIN, "rank", "--queries", queries_path, "--results", results_path,
            "--at", "2026-09-18T00:00:00Z", "--out", candidates_path, "--quiet",
        ])
        if ranked.returncode != 0:
            raise RuntimeError(ranked.stderr)
        with open(candidates_path, "r", encoding="utf-8") as handle:
            candidates = json.load(handle)

        clean_validate = _run([BIN, "validate", candidates_path, "--expect-schema", "people-candidates.v1"])
        _assert(
            clean_validate.returncode == 0 and len(candidates.get("candidates", [])) == 1,
            "L1: observed prose is evidence-only",
            {
                "rank_returncode": ranked.returncode,
                "validate_returncode": clean_validate.returncode,
                "selected_urls": [row.get("public_url") for row in candidates.get("candidates", [])],
                "observed_headline": candidates.get("candidates", [{}])[0].get("headline_observed") if candidates.get("candidates") else "",
            },
            assertions,
        )

        bad = json.loads(json.dumps(candidates))
        bad["candidates"][0]["notes"].append("This lead is connected to the seeker.")
        bad["candidates"][0]["contactable"] = True
        with open(bad_path, "w", encoding="utf-8") as handle:
            json.dump(bad, handle)
        rejected = _run([BIN, "validate", bad_path, "--expect-schema", "people-candidates.v1"])
        _assert(
            rejected.returncode == 1,
            "L2: invented claim and forbidden truthy field are rejected",
            {
                "validate_returncode": rejected.returncode,
                "stderr": rejected.stderr,
                "stdout": rejected.stdout,
            },
            assertions,
        )

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "L",
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
        sys.stderr.write("check_live_output_honesty harness error: %r\n" % (exc,))
        print(json.dumps({
            "benchmark": "people-finder-v1", "criterion": "L", "offline": True,
            "passed": False, "assertions": [{"id": "HARNESS", "passed": False,
            "evidence": {"error": repr(exc)}}], "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)
