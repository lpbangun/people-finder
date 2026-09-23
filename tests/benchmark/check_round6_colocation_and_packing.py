#!/usr/bin/env python3
"""Round-6 adversarial coverage for strict co-location and fair query packing."""

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


def clean_env():
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
        [str(value) for value in argv],
        cwd=ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def query_key(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def result_row(url: str, title: str, snippet: str, rank: int) -> dict:
    return {
        "rank": rank,
        "url": url,
        "title": title,
        "snippet": snippet,
        "source_url": "https://search.synthetic.example/round6",
        "observed_at": AT,
    }


def main() -> int:
    assertions = []

    def check(identifier: str, passed: bool, evidence: dict) -> None:
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    with tempfile.TemporaryDirectory(prefix="people-finder-round6-") as raw_dir:
        workdir = Path(raw_dir)
        job_path = workdir / "job.json"
        queries_path = workdir / "queries.json"
        queries_repeat_path = workdir / "queries-repeat.json"
        results_path = workdir / "results.json"
        candidates_path = workdir / "candidates.json"

        write_json(job_path, {
            "schema": "job-card.v1",
            "fictional": True,
            "job_id": "synthetic-round6-adversarial",
            "title": "People Ops Manager",
            "company": "Example Target",
            "department": "People operations",
            "location": "Remote",
            "posting_text": "Synthetic round-6 eligibility and packing coverage.",
        })
        compile_run = run(product_command(
            "compile", "--resume", RESUME, "--job", job_path,
            "--at", AT, "--out", queries_path, "--quiet",
        ))
        compiled = json.loads(queries_path.read_text(encoding="utf-8")) if compile_run.returncode == 0 else {}

        repeat_run = run(product_command(
            "compile", "--resume", RESUME, "--job", job_path,
            "--at", AT, "--out", queries_repeat_path, "--quiet",
        ))
        repeated = json.loads(queries_repeat_path.read_text(encoding="utf-8")) if repeat_run.returncode == 0 else {}

        packs = compiled.get("packs", [])
        execution = compiled.get("execution", {})
        selected_rows = [row for pack in packs for row in pack.get("queries", [])]
        skipped_rows = [row for pack in packs for row in pack.get("queries_skipped", [])]
        selected_keys = [query_key(row.get("query")) for row in selected_rows]
        all_pack_ids = {pack.get("pack_id") for pack in packs}
        floor_pack_ids = {
            pack.get("pack_id") for pack in packs
            if int(pack.get("queries_compiled_before_cap", 0) or 0) > 0
        }

        check("R6-P1-query-cap-and-fair-pack-floor", (
            compile_run.returncode == 0
            and execution.get("query_budget") == 12
            and len(selected_rows) <= 12
            and floor_pack_ids.issubset({
                pack.get("pack_id") for pack in packs if pack.get("queries")
            })
            and {"community_at_target", "hiring_adjacent", "shared_stamp"}.issubset(all_pack_ids)
            and all(
                any(pack.get("pack_id") == pack_id and pack.get("queries")
                    for pack in packs)
                for pack_id in ("community_at_target", "hiring_adjacent", "shared_stamp")
            )
        ), {
            "compile_returncode": compile_run.returncode,
            "pack_ids": [pack.get("pack_id") for pack in packs],
            "floor_pack_ids": sorted(floor_pack_ids),
            "selected_count": len(selected_rows),
            "query_budget": execution.get("query_budget"),
            "community_queries": next((len(pack.get("queries", [])) for pack in packs
                                       if pack.get("pack_id") == "community_at_target"), 0),
            "hiring_queries": next((len(pack.get("queries", [])) for pack in packs
                                   if pack.get("pack_id") == "hiring_adjacent"), 0),
            "shared_stamp_queries": next((len(pack.get("queries", [])) for pack in packs
                                         if pack.get("pack_id") == "shared_stamp"), 0),
        })

        check("R6-P2-normalized-query-deduplication", (
            len(selected_keys) == len(set(selected_keys))
            and int(execution.get("queries_duplicate_skipped", 0) or 0) >= 1
            and any(row.get("skip_reason_code") == "duplicate_query_text" for row in skipped_rows)
        ), {
            "selected_queries": [row.get("query") for row in selected_rows],
            "normalized_selected_queries": selected_keys,
            "duplicate_skips": [
                {"query": row.get("query"), "reason": row.get("skip_reason")}
                for row in skipped_rows
                if row.get("skip_reason_code") == "duplicate_query_text"
            ],
            "queries_duplicate_skipped": execution.get("queries_duplicate_skipped"),
        })

        raw_count = sum(int(pack.get("queries_compiled_before_cap", 0) or 0) for pack in packs)
        check("R6-P3-explicit-skip-ledger-and-conserved-cap", (
            raw_count == execution.get("queries_compiled_before_cap")
            and len(selected_rows) == execution.get("queries_compiled")
            and len(skipped_rows) == execution.get("queries_skipped")
            and len(selected_rows) + len(skipped_rows) == raw_count
            and skipped_rows
            and all(str(row.get("skip_reason", "")).strip() for row in skipped_rows)
            and any(row.get("skip_reason_code") == "query_budget_cap" for row in skipped_rows)
        ), {
            "raw_count": raw_count,
            "execution": execution,
            "skipped_count": len(skipped_rows),
            "skip_reasons": sorted({row.get("skip_reason") for row in skipped_rows}),
        })

        check("R6-P4-query-compilation-deterministic", (
            compile_run.returncode == 0
            and repeat_run.returncode == 0
            and compiled == repeated
        ), {
            "first_returncode": compile_run.returncode,
            "repeat_returncode": repeat_run.returncode,
            "documents_equal": compiled == repeated,
        })

        valid_url = "https://www.linkedin.com/in/valid-peer"
        split_url = "https://www.linkedin.com/in/split-peer"
        name_only_url = "https://www.linkedin.com/in/example-target"
        rows_by_pack = {
            "function_at_target": [
                result_row(
                    valid_url,
                    "Valid Peer - People Ops Manager at Example Target | LinkedIn",
                    "Valid Peer is a current People Ops Manager at Example Target.",
                    1,
                ),
                result_row(
                    split_url,
                    "Split Peer - People Ops Manager | LinkedIn",
                    "Function evidence without an employer marker.",
                    2,
                ),
                result_row(
                    name_only_url,
                    "Example Target - People Ops Manager | LinkedIn",
                    "Example Target has People Ops Manager experience.",
                    3,
                ),
            ],
            "alumni_at_target": [
                result_row(
                    split_url,
                    "Split Peer - Example Target | LinkedIn",
                    "Current Example Target employee.",
                    1,
                ),
            ],
        }
        pack_results = []
        for pack in packs:
            pack_id = pack.get("pack_id")
            rows = rows_by_pack.get(pack_id, [])
            pack_results.append({
                "pack_id": pack_id,
                "query": pack.get("query", ""),
                "retrieved_at": AT,
                "results": rows,
            })
        write_json(results_path, {
            "schema": "recorded-serp.v1",
            "fixture": "synthetic-round6-adversarial",
            "backend": "synthetic_public_index",
            "live_network": False,
            "retrieved_at": AT,
            "pack_results": pack_results,
        })
        rank_run = run(product_command(
            "rank", "--queries", queries_path, "--results", results_path,
            "--at", AT, "--out", candidates_path, "--quiet",
        ))
        candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.exists() else {}
        peers = candidates_doc.get("candidates", [])
        all_emitted = peers + candidates_doc.get("hiring_adjacent", [])
        peer_names = {item.get("name") for item in peers}
        suppressed = candidates_doc.get("suppressed", [])

        check("R6-P5-strict-colocation-accepts-one-live-segment", (
            rank_run.returncode == 0
            and "Valid Peer" in peer_names
            and all(
                item.get("lane") != "peer"
                or item.get("selection_eligible") is True
                for item in all_emitted
            )
            and all(
                item.get("lane") != "peer"
                or (item.get("eligibility") or {}).get("name_only_target_match") is not True
                for item in all_emitted
            )
            and all(
                item.get("lane") != "peer"
                or (item.get("eligibility") or {}).get("co_location", {}).get("status") == "passed"
                for item in all_emitted
            )
        ), {
            "rank_returncode": rank_run.returncode,
            "peer_names": sorted(name for name in peer_names if name),
            "peer_flags": [
                {
                    "name": item.get("name"),
                    "lane": item.get("lane"),
                    "selection_eligible": item.get("selection_eligible"),
                    "name_only_target_match": (item.get("eligibility") or {}).get("name_only_target_match"),
                    "co_location": (item.get("eligibility") or {}).get("co_location"),
                }
                for item in all_emitted
            ],
        })

        check("R6-P6-strict-colocation-rejects-cross-row-and-name-only", (
            "Split Peer" not in peer_names
            and "Example Target" not in peer_names
            and any(
                row.get("reason") == "wrong_function_or_level"
                and row.get("url") == split_url
                for row in suppressed
            )
            and any(
                row.get("reason") == "name_only_target_match"
                and row.get("url") == name_only_url
                for row in suppressed
            )
        ), {
            "peer_names": sorted(name for name in peer_names if name),
            "suppressed": [
                {"url": row.get("url"), "reason": row.get("reason")}
                for row in suppressed
                if row.get("url") in {split_url, name_only_url}
            ],
        })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "R6",
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
        sys.stderr.write(f"check_round6_colocation_and_packing harness error: {exc!r}\n")
        print(json.dumps({
            "benchmark": "people-finder-v1",
            "criterion": "R6",
            "offline": True,
            "passed": False,
            "assertions": [{"id": "HARNESS", "passed": False, "evidence": {"error": repr(exc)}}],
            "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)

