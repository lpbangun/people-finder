#!/usr/bin/env python3
"""Offline regression for role-family query recall and eligibility precision."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import product_command
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


def compile_job(workdir, key, title, company, department):
    job_path = workdir / f"{key}-job.json"
    query_path = workdir / f"{key}-queries.json"
    write_json(job_path, {
        "schema": "job-card.v1",
        "fictional": True,
        "job_id": f"synthetic-{key}",
        "title": title,
        "company": company,
        "department": department,
        "location": "Remote",
        "posting_text": f"Synthetic posting for the {title} role.",
    })
    result = run(product_command(
        "compile", "--resume", RESUME, "--job", job_path,
        "--at", AT, "--out", query_path, "--quiet",
    ))
    document = json.loads(query_path.read_text(encoding="utf-8")) if result.returncode == 0 else None
    return result, document, query_path


def rank_job(workdir, key, compiled, company, rows):
    results_path = workdir / f"{key}-results.json"
    candidates_path = workdir / f"{key}-candidates.json"
    result_rows = []
    for index, (url, title, snippet) in enumerate(rows, start=1):
        result_rows.append({
            "rank": index,
            "url": url,
            "title": title,
            "snippet": snippet,
            "source_url": "https://search.example.test/role-family",
            "observed_at": AT,
        })
    write_json(results_path, {
        "schema": "recorded-serp.v1",
        "fixture": f"role-family-{key}",
        "backend": "synthetic_public_index",
        "live_network": False,
        "retrieved_at": AT,
        "pack_results": [{
            "pack_id": "function_at_target",
            "query": "synthetic role-family regression",
            "retrieved_at": AT,
            "results": result_rows,
        }],
    })
    result = run(product_command(
        "rank", "--queries", workdir / f"{key}-queries.json",
        "--results", results_path, "--at", AT,
        "--out", candidates_path, "--quiet",
    ))
    document = json.loads(candidates_path.read_text(encoding="utf-8")) if result.returncode == 0 else None
    return result, document


def main():
    assertions = []

    def check(identifier, passed, evidence):
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    with tempfile.TemporaryDirectory(prefix="people-finder-function-variants-") as raw_workdir:
        workdir = Path(raw_workdir)
        sdr_title = "Sales Development Representative, Early Stage"
        ops_title = "People Ops Manager"
        compiled = {}
        compile_runs = {}
        for key, title, company, department in (
            ("sdr", sdr_title, "Northstar Cloud", "GTM"),
            ("ops", ops_title, "Northstar Workforce", "People operations"),
        ):
            compile_runs[key], compiled[key], _ = compile_job(
                workdir, key, title, company, department
            )

        sdr_anchor = next(
            (item for item in (compiled["sdr"] or {}).get("anchors", [])
             if item.get("type") == "function"), {}
        )
        ops_anchor = next(
            (item for item in (compiled["ops"] or {}).get("anchors", [])
             if item.get("type") == "function"), {}
        )
        sdr_variants = [normalise(item) for item in sdr_anchor.get("attributes", {}).get("query_variants", [])]
        ops_variants = [normalise(item) for item in ops_anchor.get("attributes", {}).get("query_variants", [])]
        check("Q1-grounded-role-family-variants",
              compile_runs["sdr"].returncode == 0
              and compile_runs["ops"].returncode == 0
              and "sales development representative" in sdr_variants
              and "sales development rep" in sdr_variants
              and "people operations manager" in ops_variants,
              {
                  "sdr_returncode": compile_runs["sdr"].returncode,
                  "ops_returncode": compile_runs["ops"].returncode,
                  "sdr_anchor_value": sdr_anchor.get("value"),
                  "sdr_query_variants": sdr_anchor.get("attributes", {}).get("query_variants"),
                  "ops_anchor_value": ops_anchor.get("value"),
                  "ops_query_variants": ops_anchor.get("attributes", {}).get("query_variants"),
              })

        sdr_pack = next((item for item in (compiled["sdr"] or {}).get("packs", [])
                         if item.get("pack_id") == "function_at_target"), {})
        ops_pack = next((item for item in (compiled["ops"] or {}).get("packs", [])
                         if item.get("pack_id") == "function_at_target"), {})
        check("Q2-variant-priority-within-budget",
              bool(sdr_pack)
              and bool(ops_pack)
              and normalise(sdr_pack.get("queries", [{}])[0].get("query"))
              .startswith("sales development representative northstar cloud")
              and normalise(ops_pack.get("queries", [{}])[0].get("query"))
              .startswith("people operations manager northstar workforce")
              and (compiled["sdr"] or {}).get("execution", {}).get("queries_compiled", 99) <= 12
              and (compiled["ops"] or {}).get("execution", {}).get("queries_compiled", 99) <= 12,
              {
                  "sdr_function_queries": [item.get("query") for item in sdr_pack.get("queries", [])],
                  "sdr_skipped_queries": [item.get("query") for item in sdr_pack.get("queries_skipped", [])],
                  "ops_function_queries": [item.get("query") for item in ops_pack.get("queries", [])],
                  "ops_skipped_queries": [item.get("query") for item in ops_pack.get("queries_skipped", [])],
                  "sdr_execution": (compiled["sdr"] or {}).get("execution"),
                  "ops_execution": (compiled["ops"] or {}).get("execution"),
              })

        sdr_rows = [
            (
                "https://www.linkedin.com/in/alex-sdr",
                "Alex Example - Sales Development Representative at Northstar Cloud | LinkedIn",
                "Current Sales Development Representative at Northstar Cloud.",
            ),
            (
                "https://www.linkedin.com/in/taylor-recruiter",
                "Taylor Example - Technical Recruiter at Northstar Cloud | LinkedIn",
                "Current technical recruiter at Northstar Cloud.",
            ),
            (
                "https://www.linkedin.com/in/casey-chief",
                "Casey Example - Chief Revenue Officer at Northstar Cloud | LinkedIn",
                "Current Chief Revenue Officer at Northstar Cloud.",
            ),
        ]
        ops_rows = [
            (
                "https://www.linkedin.com/in/jordan-ops",
                "Jordan Example - People Operations Manager at Northstar Workforce | LinkedIn",
                "Current People Operations Manager at Northstar Workforce.",
            ),
            (
                "https://www.linkedin.com/in/morgan-recruiter",
                "Morgan Example - Technical Recruiter at Northstar Workforce | LinkedIn",
                "Current technical recruiter at Northstar Workforce.",
            ),
            (
                "https://www.linkedin.com/in/riley-chief",
                "Riley Example - Chief People Officer at Northstar Workforce | LinkedIn",
                "Current Chief People Officer at Northstar Workforce.",
            ),
        ]
        sdr_rank, sdr_document = rank_job(workdir, "sdr", compiled["sdr"], "Northstar Cloud", sdr_rows)
        ops_rank, ops_document = rank_job(workdir, "ops", compiled["ops"], "Northstar Workforce", ops_rows)
        sdr_peers = (sdr_document or {}).get("candidates", [])
        ops_peers = (ops_document or {}).get("candidates", [])
        precision_ok = (
            sdr_rank.returncode == 0
            and ops_rank.returncode == 0
            and [item.get("name") for item in sdr_peers] == ["Alex Example"]
            and [item.get("name") for item in ops_peers] == ["Jordan Example"]
            and [item.get("name") for item in (sdr_document or {}).get("hiring_adjacent", [])] == ["Taylor Example"]
            and [item.get("name") for item in (ops_document or {}).get("hiring_adjacent", [])] == ["Morgan Example"]
            and any(item.get("reason") == "c_suite_excluded" for item in (sdr_document or {}).get("suppressed", []))
            and any(item.get("reason") == "c_suite_excluded" for item in (ops_document or {}).get("suppressed", []))
            and all("function_at_target" in item.get("paths", []) for item in sdr_peers + ops_peers)
        )
        check("Q3-rank-precision-preserved", precision_ok, {
            "sdr_rank_returncode": sdr_rank.returncode,
            "ops_rank_returncode": ops_rank.returncode,
            "sdr_peer_names": [item.get("name") for item in sdr_peers],
            "ops_peer_names": [item.get("name") for item in ops_peers],
            "sdr_hiring_names": [item.get("name") for item in (sdr_document or {}).get("hiring_adjacent", [])],
            "ops_hiring_names": [item.get("name") for item in (ops_document or {}).get("hiring_adjacent", [])],
            "sdr_suppressed_reasons": sorted({item.get("reason") for item in (sdr_document or {}).get("suppressed", [])}),
            "ops_suppressed_reasons": sorted({item.get("reason") for item in (ops_document or {}).get("suppressed", [])}),
        })

        _, sdr_repeat, _ = compile_job(workdir, "sdr", sdr_title, "Northstar Cloud", "GTM")
        repeat_equal = json.dumps(compiled["sdr"], sort_keys=True) == json.dumps(sdr_repeat, sort_keys=True)
        check("Q4-variant-compilation-deterministic", repeat_equal, {
            "documents_equal_with_fixed_timestamp": repeat_equal,
            "first_query": (compiled["sdr"] or {}).get("packs", [{}])[2].get("query") if compiled["sdr"] else None,
        })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "Q",
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
        sys.stderr.write(f"check_function_query_variants harness error: {exc!r}\n")
        print(json.dumps({
            "benchmark": "people-finder-v1",
            "criterion": "Q",
            "offline": True,
            "passed": False,
            "assertions": [{"id": "HARNESS", "passed": False, "evidence": {"error": repr(exc)}}],
            "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)
