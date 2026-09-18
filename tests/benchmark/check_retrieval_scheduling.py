#!/usr/bin/env python3
"""Deterministic regression for bounded fallback scheduling and evidence scope."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin" / "people-finder"
RESUME = ROOT / "tests" / "fixtures" / "resumes" / "a-rich-stamps.md"
AT = "2026-09-18T00:00:00Z"


def normalise(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


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

    with tempfile.TemporaryDirectory(prefix="people-finder-retrieval-scheduling-") as raw_workdir:
        workdir = Path(raw_workdir)
        job_path = workdir / "job.json"
        queries_path = workdir / "queries.json"
        results_path = workdir / "results.json"
        candidates_path = workdir / "candidates.json"

        write_json(job_path, {
            "schema": "job-card.v1",
            "fictional": True,
            "job_id": "synthetic-retrieval-scheduling",
            "title": "Senior UX Researcher, Qualitative",
            "company": "Aurora Labs",
            "department": "Data and research",
            "location": "Remote",
            "posting_text": "Synthetic sparse-role retrieval regression.",
        })
        compiled_run = run([
            BIN, "compile", "--resume", RESUME, "--job", job_path,
            "--at", AT, "--out", queries_path, "--quiet",
        ])
        compiled = json.loads(queries_path.read_text(encoding="utf-8")) if compiled_run.returncode == 0 else {}
        packs = compiled.get("packs", [])
        function_pack = next(
            (pack for pack in packs if pack.get("pack_id") == "function_at_target"), {}
        )
        execution = compiled.get("execution", {})
        compiled_before = sum(
            int(pack.get("queries_compiled_before_cap", 0)) for pack in packs
        )
        selected_rows = [
            row for pack in packs for row in pack.get("queries", [])
        ]
        skipped_rows = [
            row for pack in packs for row in pack.get("queries_skipped", [])
        ]
        selected_count = len(selected_rows)
        skipped_count = len(skipped_rows)

        check("Y1-compile-and-function-lane-first", (
            compiled_run.returncode == 0
            and bool(function_pack)
            and packs
            and packs[0].get("pack_id") == "function_at_target"
        ), {
            "compile_returncode": compiled_run.returncode,
            "compile_stderr": compiled_run.stderr[-500:],
            "pack_order": [pack.get("pack_id") for pack in packs],
            "function_query_count": len(function_pack.get("queries", [])),
        })

        check("Y2-global-budget-ledger-is-conserved", (
            execution.get("query_budget") == 12
            and selected_count <= execution.get("query_budget", 0)
            and execution.get("queries_compiled_before_cap") == compiled_before
            and execution.get("queries_compiled") == selected_count
            and execution.get("queries_skipped") == skipped_count
            and selected_count + skipped_count == compiled_before
            and all(row.get("skip_reason") for row in skipped_rows)
        ), {
            "execution": execution,
            "compiled_before": compiled_before,
            "selected_count": selected_count,
            "skipped_count": skipped_count,
            "skipped_queries": [row.get("query") for row in skipped_rows],
        })

        function_queries = function_pack.get("queries", [])
        function_query_text = [normalise(row.get("query")) for row in function_queries]
        function_kinds = [row.get("query_kind") for row in function_queries]
        useful_family_variant = any(
            "ux researcher" in query or "user experience researcher" in query
            for query in function_query_text
        )
        check("Y3-role-family-fallbacks-are-useful-and-bounded", (
            len(function_queries) >= 4
            and useful_family_variant
            and "role_family_variant" in function_kinds
            and "role_family_canonical" in function_kinds
            and all(isinstance(row.get("variant_rank"), int) for row in function_queries)
        ), {
            "function_queries": [row.get("query") for row in function_queries],
            "function_kinds": function_kinds,
            "function_query_count": len(function_queries),
            "query_budget": execution.get("query_budget"),
        })

        # This is a deterministic host-route miniature: the first role-family
        # query raises on the primary route and then genuinely yields nothing
        # on the secondary route. A later compiled fallback returns one mixed
        # profile card, whose neighboring profile must not become evidence.
        fallback_index = min(2, len(function_queries) - 1)
        route_trace = []
        pack_results = []
        for index, query_row in enumerate(function_queries):
            if index == 0:
                events = [
                    {"backend": "bing", "attempt": 1, "outcome": "exception"},
                    {"backend": "bing", "attempt": 2, "outcome": "exception"},
                    {"backend": "bing", "attempt": 3, "outcome": "exception"},
                    {"backend": "duckduckgo", "attempt": 1, "outcome": "zero_result"},
                ]
                rows = []
            elif index == 1:
                events = [{"backend": "bing", "attempt": 1, "outcome": "zero_result"}]
                rows = [{
                    "rank": 1,
                    "url": "https://www.linkedin.com/in/bad-cross-profile",
                    "title": (
                        "Bad Cross - Aurora Labs | LinkedIn"
                        "Other Peer - UX Researcher at Aurora Labs | LinkedIn"
                    ),
                    "snippet": (
                        "Bad Cross - Aurora Labs. "
                        "Other Peer is a UX Researcher at Aurora Labs."
                    ),
                }]
            elif index == fallback_index:
                events = [{"backend": "bing", "attempt": 1, "outcome": "success"}]
                rows = [{
                    "rank": 1,
                    "url": "https://www.linkedin.com/in/jamie-valid",
                    "title": "Jamie Valid - UX Researcher at Aurora Labs | LinkedIn",
                    "snippet": "Jamie Valid is a UX Researcher at Aurora Labs.",
                }]
            else:
                events = [{"backend": "bing", "attempt": 1, "outcome": "zero_result"}]
                rows = []
            route_trace.append({"query_index": index, "events": events})
            pack_results.append({
                "pack_id": "function_at_target",
                "query": query_row.get("query", ""),
                "retrieved_at": AT,
                "route_events": events,
                "results": rows,
            })

        attempts_by_query = [
            len(item["events"]) for item in route_trace
        ]
        check("Y4-route-exception-and-zero-yield-are-journaled", (
            len(route_trace) == len(function_queries)
            and route_trace
            and route_trace[0]["events"][0]["outcome"] == "exception"
            and route_trace[0]["events"][-1]["outcome"] == "zero_result"
            and all(
                event.get("attempt", 0) <= 3
                for item in route_trace for event in item["events"]
            )
            and all(count <= 4 for count in attempts_by_query)
        ), {
            "route_trace": route_trace,
            "attempts_by_query": attempts_by_query,
            "fallback_index": fallback_index,
        })

        write_json(results_path, {
            "schema": "recorded-serp.v1",
            "fixture": "bounded-retrieval-scheduling-adversarial",
            "backend": "synthetic_public_index",
            "live_network": False,
            "retrieved_at": AT,
            "pack_results": pack_results,
        })
        ranked_run = run([
            BIN, "rank", "--queries", queries_path, "--results", results_path,
            "--at", AT, "--out", candidates_path, "--quiet",
        ])
        ranked = json.loads(candidates_path.read_text(encoding="utf-8")) if ranked_run.returncode == 0 else {}
        candidates = ranked.get("candidates", [])
        candidate_urls = {item.get("public_url") for item in candidates}
        candidate_names = [item.get("name") for item in candidates]
        suppressed = {
            item.get("url"): item.get("reason") for item in ranked.get("suppressed", [])
        }
        jamie = next((item for item in candidates if item.get("name") == "Jamie Valid"), {})
        jamie_text = json.dumps(jamie, ensure_ascii=False)

        check("Y5-later-fallback-recovers-a-valid-peer", (
            ranked_run.returncode == 0
            and "Jamie Valid" in candidate_names
            and "https://www.linkedin.com/in/jamie-valid" in candidate_urls
            and "Bad Cross" not in candidate_names
            and "https://www.linkedin.com/in/bad-cross-profile" not in candidate_urls
        ), {
            "rank_returncode": ranked_run.returncode,
            "rank_stderr": ranked_run.stderr[-500:],
            "candidate_names": candidate_names,
            "candidate_urls": sorted(candidate_urls),
            "bad_suppressed_reason": suppressed.get("https://www.linkedin.com/in/bad-cross-profile"),
        })

        check("Y6-positional-evidence-stays-bound-to-first-profile", (
            "Other Peer" not in jamie_text
            and "UX Researcher at Aurora Labs" in jamie.get("headline_observed", "")
            and suppressed.get("https://www.linkedin.com/in/bad-cross-profile") == "wrong_function_or_level"
        ), {
            "jamie_headline": jamie.get("headline_observed"),
            "jamie_function_evidence": (jamie.get("eligibility") or {}).get("function_evidence"),
            "bad_suppressed_reason": suppressed.get("https://www.linkedin.com/in/bad-cross-profile"),
            "selected_urls": sorted(candidate_urls),
        })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "Y",
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
        sys.stderr.write(f"check_retrieval_scheduling harness error: {exc!r}\n")
