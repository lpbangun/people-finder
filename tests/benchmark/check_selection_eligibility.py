#!/usr/bin/env python3
"""Reviewer-shaped B3 checks for eligibility ordering and lead evidence."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import clean_env, product_command, run_cmd, scratch

AT = "2026-09-17T00:00:00Z"
TARGET_JOB = {
    "schema": "job-card.v1",
    "fictional": True,
    "job_id": "northstar-data-platform",
    "title": "Senior Data Platform Engineer",
    "company": "Northstar Labs",
    "department": "Data Platform",
    "location": "Remote",
    "posting_text": "Synthetic posting for Northstar Labs Data Platform.",
}
RENDER_JOB = dict(TARGET_JOB, job_id="render-data-platform", company="Render")
RESUME = "\n".join([
    "# Synthetic Seeker",
    "Location: Remote",
    "## Education",
    "- M.S. Computer Science, Northwind Institute of Technology, 2016-2018",
    "## Experience",
    "- Senior Data Platform Engineer, Ionwave Systems, 2018-2020",
    "## Community",
    "- Maintainer, OpenKelvin Collective",
    "## Skills",
    "- Python, SQL, Spark",
    "",
])
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def write_text(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(value, encoding="utf-8")


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def row(url, title, snippet):
    return {
        "rank": 1,
        "url": url,
        "title": title,
        "snippet": snippet,
        "source_url": "https://search.synthetic.example/result",
        "observed_at": AT,
    }


def compile_job(workdir, job, label):
    resume_path = os.path.join(workdir, label + "-resume.md")
    job_path = os.path.join(workdir, label + "-job.json")
    output_path = os.path.join(workdir, label + "-queries.json")
    write_text(resume_path, RESUME)
    write_json(job_path, job)
    run = run_cmd(product_command(
        "compile", "--resume", resume_path, "--job", job_path,
        "--at", AT, "--out", output_path, "--quiet",
    ), env=clean_env(), timeout=60)
    document = None
    if run["returncode"] == 0 and os.path.isfile(output_path):
        document = json.loads(Path(output_path).read_text(encoding="utf-8"))
    return {"run": run, "document": document, "path": output_path}


def recorded_results(compiled, rows_by_pack, label):
    pack_results = []
    for pack in compiled.get("packs", []):
        pack_id = pack["pack_id"]
        rows = []
        for index, item in enumerate(rows_by_pack.get(pack_id, []), start=1):
            value = dict(item)
            value["rank"] = index
            rows.append(value)
        pack_results.append({
            "pack_id": pack_id,
            "query": pack.get("query", ""),
            "retrieved_at": AT,
            "results": rows,
        })
    return {
        "schema": "recorded-serp.v1",
        "fixture": "synthetic-eligibility-" + label,
        "fictional": True,
        "backend": "recorded_fixture",
        "live_network": False,
        "retrieved_at": AT,
        "pack_results": pack_results,
    }


def rank_scenario(workdir, compiled, rows_by_pack, label):
    results_path = os.path.join(workdir, label + "-results.json")
    output_path = os.path.join(workdir, label + "-candidates.json")
    write_json(results_path, recorded_results(compiled["document"], rows_by_pack, label))
    run = run_cmd(product_command(
        "rank", "--queries", compiled["path"], "--results", results_path,
        "--at", AT, "--out", output_path, "--quiet",
    ), env=clean_env(), timeout=60)
    document = None
    if run["returncode"] == 0 and os.path.isfile(output_path):
        document = json.loads(Path(output_path).read_text(encoding="utf-8"))
    return {"run": run, "document": document, "results_path": results_path}


def all_items(document):
    return list(document.get("candidates", [])) + list(document.get("hiring_adjacent", []))


def suppressed_reasons(document):
    return {row.get("reason") for row in document.get("suppressed", [])}


def main():
    workdir = scratch("reviewer-selection")
    assertions = []

    def check(identifier, passed, evidence):
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    compiled = compile_job(workdir, TARGET_JOB, "northstar")
    render_compiled = compile_job(workdir, RENDER_JOB, "render")
    check("B3-compile-inputs", compiled["document"] is not None and render_compiled["document"] is not None, {
        "northstar_returncode": compiled["run"]["returncode"],
        "render_returncode": render_compiled["run"]["returncode"],
        "northstar_stderr": compiled["run"]["stderr"][:300],
        "render_stderr": render_compiled["run"]["stderr"][:300],
    })
    if compiled["document"] is None or render_compiled["document"] is None:
        payload = {
            "benchmark": "people-finder-v1", "criterion": "B3", "offline": True,
            "passed": False, "assertions": assertions, "test_count": len(assertions),
            "recorded_scenario_count": 7, "scratch_dir": workdir,
        }
        print(json.dumps(payload, sort_keys=True))
        return 1

    peers = rank_scenario(workdir, compiled, {
        "function_at_target": [
            row("https://www.linkedin.com/in/rina-peer", "Rina Peer - Senior Data Platform Engineer - Northstar Labs", "Data platform engineering peer."),
            row("https://www.linkedin.com/in/sam-head", "Sam Head - Head of Data Platform - Northstar Labs", "Leads the Data Platform group."),
        ],
    }, "peer-and-head")
    check("B3-recorded-peer-head", peers["run"]["returncode"] == 0 and peers["document"] is not None, {
        "returncode": peers["run"]["returncode"],
        "stderr": peers["run"]["stderr"][:300],
        "candidate_names": [item.get("name") for item in all_items(peers["document"] or {})],
    })
    peer_items = (peers["document"] or {}).get("candidates", [])
    lead_evidence_ok = bool(peer_items) and all(
        item.get("public_url", "").startswith("https://www.linkedin.com/in/")
        and item.get("selection_eligible") is True
        and (item.get("observed_at_target") or {}).get("status") == "observed"
        and (item.get("observed_at_target") or {}).get("evidence")
        and item.get("function_level_rationale")
        and (item.get("eligibility") or {}).get("status") == "eligible"
        and (item.get("eligibility") or {}).get("target_company_evidence")
        and (item.get("eligibility") or {}).get("function_evidence")
        and (item.get("eligibility") or {}).get("level_evidence")
        and (item.get("function_level") or {}).get("function_evidence")
        and (item.get("function_level") or {}).get("level_evidence")
        for item in peer_items
    )
    check("B3-selected-lead-evidence", lead_evidence_ok, {
        "selected": [
            {
                "name": item.get("name"), "url": item.get("public_url"),
                "lane": item.get("lane"),
                "target_evidence_count": len((item.get("observed_at_target") or {}).get("evidence", [])),
                "function_evidence_count": len((item.get("eligibility") or {}).get("function_evidence", [])),
                "level_evidence_count": len((item.get("eligibility") or {}).get("level_evidence", [])),
                "rationale": item.get("function_level_rationale"),
            }
            for item in peer_items
        ],
        "all_public_urls": all(item.get("public_url", "").startswith("https://www.linkedin.com/in/") for item in peer_items),
    })

    c_suite = rank_scenario(workdir, compiled, {
        "function_at_target": [row(
            "https://www.linkedin.com/in/alex-exec",
            "Alex Exec - Chief Technology Officer, Data Platform - Northstar Labs",
            "Executive profile for the Data Platform organization.",
        )],
    }, "c-suite")
    c_suite_doc = c_suite["document"] or {}
    check("B3-c-suite-excluded", c_suite["run"]["returncode"] == 0
          and not all_items(c_suite_doc)
          and "c_suite_excluded" in suppressed_reasons(c_suite_doc)
          and any(row.get("code") == "no_eligible_peer"
                  for row in c_suite_doc.get("selection_shortfalls", [])), {
              "returncode": c_suite["run"]["returncode"],
              "selected_names": [item.get("name") for item in all_items(c_suite_doc)],
              "suppressed_reasons": sorted(suppressed_reasons(c_suite_doc)),
          })

    recruiter = rank_scenario(workdir, compiled, {
        "function_at_target": [row(
            "https://www.linkedin.com/in/rina-peer",
            "Rina Peer - Senior Data Platform Engineer - Northstar Labs",
            "Data platform engineering peer.",
        )],
        "hiring_adjacent": [row(
            "https://www.linkedin.com/in/terry-recruiter",
            "Terry Recruiter - Technical Recruiter - Northstar Labs",
            "Talent acquisition surface for the Data Platform team.",
        )],
    }, "recruiter")
    recruiter_doc = recruiter["document"] or {}
    recruiter_items = recruiter_doc.get("hiring_adjacent", [])
    recruiter_ok = (recruiter["run"]["returncode"] == 0 and len(recruiter_items) == 1
                    and len(recruiter_doc.get("candidates", [])) == 1
                    and recruiter_doc["candidates"][0].get("name") == "Rina Peer"
                    and recruiter_items[0].get("name") == "Terry Recruiter"
                    and recruiter_items[0].get("lane") == "hiring_adjacent"
                    and recruiter_items[0].get("paths") == ["hiring_adjacent"]
                    and recruiter_items[0].get("public_url") not in
                    {item.get("public_url") for item in recruiter_doc.get("candidates", [])})
    check("B3-recruiter-separate-lane", recruiter_ok, {
        "returncode": recruiter["run"]["returncode"],
        "peer_names": [item.get("name") for item in recruiter_doc.get("candidates", [])],
        "hiring_names": [item.get("name") for item in recruiter_items],
        "hiring_paths": [item.get("paths") for item in recruiter_items],
        "selection_shortfalls": recruiter_doc.get("selection_shortfalls"),
        "recruiter_url_in_peer_lane": recruiter_items[0].get("public_url") in
        {item.get("public_url") for item in recruiter_doc.get("candidates", [])} if recruiter_items else False,
    })

    wrong_function = rank_scenario(workdir, compiled, {
        "alumni_at_target": [row(
            "https://www.linkedin.com/in/uma-alumni",
            "Uma Alumni - Senior Marketing Manager - Northstar Labs",
            "Northwind Institute of Technology alum.",
        )],
    }, "wrong-function-alumni")
    wrong_doc = wrong_function["document"] or {}
    check("B3-wrong-function-alumni-excluded", wrong_function["run"]["returncode"] == 0
          and not [item for item in all_items(wrong_doc) if item.get("name") == "Uma Alumni"]
          and "wrong_function_or_level" in suppressed_reasons(wrong_doc), {
              "returncode": wrong_function["run"]["returncode"],
              "selected_names": [item.get("name") for item in all_items(wrong_doc)],
              "suppressed_reasons": sorted(suppressed_reasons(wrong_doc)),
          })

    missing_target = rank_scenario(workdir, compiled, {
        "function_at_target": [row(
            "https://www.linkedin.com/in/nora-elsewhere",
            "Nora Elsewhere - Senior Data Platform Engineer - Southstar",
            "Data platform engineering work.",
        )],
    }, "missing-current-target")
    missing_doc = missing_target["document"] or {}
    check("B3-current-target-required", missing_target["run"]["returncode"] == 0
          and not all_items(missing_doc)
          and "target_company_evidence_missing" in suppressed_reasons(missing_doc), {
              "returncode": missing_target["run"]["returncode"],
              "selected_names": [item.get("name") for item in all_items(missing_doc)],
              "suppressed": missing_doc.get("suppressed"),
          })

    duplicate = rank_scenario(workdir, compiled, {
        "function_at_target": [row(
            "https://de.linkedin.com/in/rina-peer",
            "Rina Peer - Senior Data Platform Engineer - Northstar Labs",
            "Data platform engineering peer.",
        )],
        "alumni_at_target": [row(
            "https://www.linkedin.com/in/rina-peer?trk=profile",
            "Rina Peer - Senior Data Platform Engineer - Northstar Labs",
            "Northwind Institute of Technology alum.",
        )],
    }, "country-subdomain-duplicate")
    duplicate_doc = duplicate["document"] or {}
    duplicate_items = [item for item in all_items(duplicate_doc) if item.get("name") == "Rina Peer"]
    duplicate_ok = (duplicate["run"]["returncode"] == 0 and len(duplicate_items) == 1
                    and duplicate_items[0].get("public_url") == "https://www.linkedin.com/in/rina-peer"
                    and len(duplicate_items[0].get("url_observed_in", [])) == 2)
    check("B3-country-subdomain-collapse", duplicate_ok, {
        "returncode": duplicate["run"]["returncode"],
        "matching_items": [
            {"public_url": item.get("public_url"), "url_observed_in": item.get("url_observed_in"),
             "paths": item.get("paths")} for item in duplicate_items
        ],
        "all_urls": [item.get("public_url") for item in all_items(duplicate_doc)],
    })

    name_only = rank_scenario(workdir, render_compiled, {
        "function_at_target": [row(
            "https://www.linkedin.com/in/mark-render",
            "Mark Render - Senior Data Platform Engineer",
            "Data platform engineering work.",
        )],
    }, "name-only-employer")
    name_doc = name_only["document"] or {}
    check("B3-name-only-employer-excluded", name_only["run"]["returncode"] == 0
          and not all_items(name_doc)
          and "name_only_target_match" in suppressed_reasons(name_doc), {
              "returncode": name_only["run"]["returncode"],
              "selected_names": [item.get("name") for item in all_items(name_doc)],
              "suppressed": name_doc.get("suppressed"),
          })

    outcome_doc = peers["document"] or {}
    serialized = json.dumps(outcome_doc, ensure_ascii=False)
    check("B3-no-guessed-contact-outcome", not EMAIL_RE.search(serialized)
          and (outcome_doc.get("outcome_counters") or {}).get("attributed_email") == 0
          and (outcome_doc.get("outcome_counters") or {}).get("deliverability_claimed") is False, {
              "email_like_values": EMAIL_RE.findall(serialized),
              "outcome_counters": outcome_doc.get("outcome_counters"),
          })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "B3",
        "offline": True,
        "passed": all(row["passed"] for row in assertions),
        "assertions": assertions,
        "test_count": len(assertions),
        "recorded_scenario_count": 7,
        "scenarios": [
            "peer_and_function_head", "c_suite", "recruiter", "wrong_function_alumni",
            "missing_current_target", "country_subdomain_duplicate", "name_only_employer",
        ],
        "scratch_dir": workdir,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("check_selection_eligibility harness error: %r\n" % (exc,))
        print(json.dumps({
            "benchmark": "people-finder-v1", "criterion": "B3", "offline": True,
            "passed": False, "assertions": [{"id": "HARNESS", "passed": False,
            "evidence": {"error": repr(exc)}}], "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)
