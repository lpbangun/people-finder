#!/usr/bin/env python3
"""Reviewer-shaped B1 checks for profile dialects, exact anchors, and provenance."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import BIN, ROOT, clean_env, run_cmd, scratch

AT = "2026-09-17T00:00:00Z"
DIALECTS = ("heading", "bullet", "mixed", "aliased", "repeated")
FAMILY_SPECS = {
    "data": {
        "schools": ("University of Toronto", "Northwind Institute of Technology"),
        "employers": ("Ionwave Systems", "Copperline Analytics"),
        "community": "OpenKelvin Collective",
        "title": "Data Platform Engineer",
        "company": "Helioscale",
        "department": "Data Platform",
    },
    "learning": {
        "schools": ("Harvard Graduate School of Education", "Riverside University"),
        "employers": ("Brightline Revenue", "PeopleFlow Labs"),
        "community": "Learning Systems Guild",
        "title": "Learning Operations Manager",
        "company": "Brightlearn",
        "department": "Learning Operations",
    },
    "revenue": {
        "schools": ("Ashvale Polytechnic Institute", "University of Waterloo"),
        "employers": ("Cloudharbor Revenue", "MarketSpring Labs"),
        "community": "RevOps Commons",
        "title": "Revenue Operations Manager",
        "company": "Harborloop",
        "department": "Revenue Operations",
    },
}
JUNK = {
    "learning materials", "tools", "implementation plans", "job interviews",
    "soft skills training", "bogasari flour mills - jakarta", "riau",
    "indonesia", "remote",
}
ALLOWED_DIALECTS = {"heading", "heading_alias", "bullet", "plain", "mixed", "job_card", "labeled"}


def job_card(spec):
    return {
        "schema": "job-card.v1",
        "fictional": True,
        "job_id": spec["company"].lower() + "-synthetic",
        "title": spec["title"],
        "company": spec["company"],
        "department": spec["department"],
        "location": "Remote",
        "posting_text": (
            "Synthetic posting. Title: %s. Company: %s. Department: %s."
            % (spec["title"], spec["company"], spec["department"])
        ),
    }


def _heading_resume(spec):
    s0, s1 = spec["schools"]
    e0, e1 = spec["employers"]
    return "\n".join([
        "# Synthetic Profile",
        "Location: Remote",
        "## Education",
        "### " + s0,
        "### " + s1,
        "## Experience",
        "### " + e0 + " — Toronto",
        "Senior " + spec["title"] + " | 2020-2023",
        "### " + e1 + " — Boston",
        spec["title"] + " | 2018-2020",
        "## Community",
        "### " + spec["community"],
        "## Skills",
        "- Python, SQL, Systems Thinking",
        "",
    ])


def _bullet_resume(spec):
    s0, s1 = spec["schools"]
    e0, e1 = spec["employers"]
    return "\n".join([
        "# Synthetic Profile",
        "Location: Remote",
        "Education:",
        "- M.S. Systems Practice, %s, 2016-2018" % s0,
        "- B.S. Applied Studies, %s, 2012-2016" % s1,
        "Experience:",
        "- Senior %s, %s, 2020-2023" % (spec["title"], e0),
        "- %s, %s, 2018-2020" % (spec["title"], e1),
        "Community:",
        "- Maintainer, " + spec["community"] + " (synthetic public group)",
        "Skills:",
        "- Python, SQL, Systems Thinking",
        "",
    ])


def _mixed_resume(spec):
    s0, s1 = spec["schools"]
    e0, e1 = spec["employers"]
    return "\n".join([
        "# Synthetic Profile",
        "Location: Remote",
        "## Education",
        "### " + s0,
        "- M.S. Systems Practice, %s, 2016-2018" % s1,
        "## Professional Experience",
        "### " + e0 + " — Toronto",
        "- Senior %s, %s, 2020-2023" % (spec["title"], e1),
        "## Public Work",
        "### " + spec["community"],
        "## Core Skills",
        "- Python, SQL, Systems Thinking",
        "",
    ])


def _aliased_resume(spec):
    s0, s1 = spec["schools"]
    e0, e1 = spec["employers"]
    return "\n".join([
        "# Synthetic Profile",
        "Location: Remote",
        "## Academic Background",
        "- M.S. Systems Practice, %s, 2016-2018" % s0,
        "- B.S. Applied Studies, %s, 2012-2016" % s1,
        "## Work History",
        "- Senior %s, %s, 2020-2023" % (spec["title"], e0),
        "- %s, %s, 2018-2020" % (spec["title"], e1),
        "## Public Work",
        "- Maintainer, " + spec["community"],
        "## Core Skills",
        "- Python, SQL, Systems Thinking",
        "",
    ])


def _repeated_resume(spec):
    s0, s1 = spec["schools"]
    e0, e1 = spec["employers"]
    return "\n".join([
        "# Synthetic Profile",
        "Location: Remote",
        "## Education",
        "- M.S. Systems Practice, %s, 2016-2018" % s0,
        "## Education",
        "- B.S. Applied Studies, %s, 2012-2016" % s1,
        "## Education",
        "- Coursework and honors continued",
        "## Experience",
        "- Senior %s, %s, 2020-2023" % (spec["title"], e0),
        "## Experience",
        "- %s, %s, 2018-2020" % (spec["title"], e1),
        "## Experience",
        "- Built data platform improvements for internal users.",
        "## Community",
        "- Maintainer, " + spec["community"],
        "## Skills",
        "- Python, SQL, Systems Thinking",
        "",
    ])


RESUME_BUILDERS = {
    "heading": _heading_resume,
    "bullet": _bullet_resume,
    "mixed": _mixed_resume,
    "aliased": _aliased_resume,
    "repeated": _repeated_resume,
}


def write_text(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(value, encoding="utf-8")


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compile_one(workdir, family, dialect):
    spec = FAMILY_SPECS[family]
    resume = os.path.join(workdir, "%s-%s.md" % (family, dialect))
    job = os.path.join(workdir, "%s-job.json" % family)
    output = os.path.join(workdir, "%s-%s-queries.json" % (family, dialect))
    write_text(resume, RESUME_BUILDERS[dialect](spec))
    write_json(job, job_card(spec))
    run = run_cmd([
        BIN, "compile", "--resume", resume, "--job", job, "--at", AT,
        "--out", output, "--quiet",
    ], env=clean_env(), timeout=60)
    document = None
    if run["returncode"] == 0 and os.path.isfile(output):
        document = json.loads(Path(output).read_text(encoding="utf-8"))
    return {
        "run": run,
        "document": document,
        "resume_path": resume,
        "job_path": job,
        "resume_text": RESUME_BUILDERS[dialect](spec),
    }


def anchor_values(document, anchor_type):
    return sorted({item["value"] for item in document.get("anchors", []) if item.get("type") == anchor_type})


def summary(document):
    return {
        "schools": anchor_values(document, "school"),
        "employers": anchor_values(document, "prior_employer"),
        "communities": anchor_values(document, "rare_community"),
        "packs": sorted(item["pack_id"] for item in document.get("packs", [])),
    }


def provenance_ok(document, resume_text):
    raw_lines = resume_text.splitlines()
    failures = []
    for anchor in document.get("anchors", []):
        evidence = anchor.get("evidence") or {}
        provenance = anchor.get("provenance") or {}
        if not evidence.get("quote") or not provenance:
            failures.append({"anchor": anchor.get("anchor_id"), "reason": "missing provenance or quote"})
            continue
        if provenance != (evidence.get("provenance") or {}):
            failures.append({"anchor": anchor.get("anchor_id"), "reason": "anchor/evidence provenance mismatch"})
        if provenance.get("section") not in {"profile", "education", "experience", "community", "skills", "target_job"}:
            failures.append({"anchor": anchor.get("anchor_id"), "reason": "unknown section"})
        if provenance.get("dialect") not in ALLOWED_DIALECTS:
            failures.append({"anchor": anchor.get("anchor_id"), "reason": "unknown dialect"})
        if evidence.get("field", "").startswith("resume:"):
            line = evidence.get("line")
            offset = evidence.get("line_offset")
            if not isinstance(line, int) or line <= 0 or offset != line - 1:
                failures.append({"anchor": anchor.get("anchor_id"), "reason": "line offset is not zero-based exact"})
            elif offset >= len(raw_lines) or evidence["quote"] not in raw_lines[offset]:
                failures.append({"anchor": anchor.get("anchor_id"), "reason": "quote is not on its cited source line"})
        elif evidence.get("line") != 0 or evidence.get("line_offset") != 0:
            failures.append({"anchor": anchor.get("anchor_id"), "reason": "job evidence line contract changed"})
    return failures


def thin_skip_check(workdir):
    resume = os.path.join(workdir, "skip-reasons.md")
    job = os.path.join(workdir, "skip-job.json")
    output = os.path.join(workdir, "skip-queries.json")
    text = "\n".join([
        "# Synthetic Thin Profile", "## Experience", "- Built useful systems.",
        "## Skills", "- Python, SQL", "",
    ])
    spec = FAMILY_SPECS["data"]
    write_text(resume, text)
    write_json(job, job_card(spec))
    run = run_cmd([
        BIN, "compile", "--resume", resume, "--job", job, "--at", AT,
        "--out", output, "--quiet",
    ], env=clean_env(), timeout=60)
    if run["returncode"] != 0 or not os.path.isfile(output):
        return False, {"returncode": run["returncode"], "stderr": run["stderr"][:300]}
    document = json.loads(Path(output).read_text(encoding="utf-8"))
    skipped = document.get("packs_skipped", [])
    required = {"alumni_at_target", "prior_employer_at_target", "community_at_target", "shared_stamp"}
    good = required.issubset({row.get("pack_id") for row in skipped}) and all(
        row.get("reason") and row.get("absence_reason") and row.get("missing_anchor_types")
        and row.get("missing_anchor_ids") and row.get("missing_anchor_id")
        and "parse" not in row.get("reason", "").lower()
        for row in skipped if row.get("pack_id") in required
    )
    return good, {
        "skipped_pack_ids": sorted(row.get("pack_id") for row in skipped),
        "reasons": [
            {"pack_id": row.get("pack_id"), "reason": row.get("reason"),
             "missing_anchor_ids": row.get("missing_anchor_ids")}
            for row in skipped
        ],
    }


def main():
    workdir = scratch("reviewer-profile")
    assertions = []
    records = {}

    def check(identifier, passed, evidence):
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    for family in FAMILY_SPECS:
        for dialect in DIALECTS:
            key = (family, dialect)
            record = compile_one(workdir, family, dialect)
            records[key] = record
            document = record["document"]
            spec = FAMILY_SPECS[family]
            expected = {
                "schools": sorted(spec["schools"]),
                "employers": sorted(spec["employers"]),
                "communities": [spec["community"]],
                "packs": sorted({
                    "alumni_at_target", "prior_employer_at_target", "function_at_target",
                    "community_at_target", "shared_stamp", "hiring_adjacent",
                }),
            }
            actual = summary(document) if document else {}
            check("B1-%s-%s-exact" % (family, dialect), actual == expected, {
                "family": family, "dialect": dialect, "expected": expected,
                "actual": actual,
                "returncode": record["run"]["returncode"],
                "stderr": record["run"]["stderr"][:300],
            })
            provenance_failures = provenance_ok(document or {}, record["resume_text"])
            check("B1-%s-%s-provenance" % (family, dialect), document is not None and not provenance_failures, {
                "family": family, "dialect": dialect,
                "anchor_count": len((document or {}).get("anchors", [])),
                "failures": provenance_failures[:8],
            })
            junk = sorted(set(anchor_values(document or {}, "prior_employer")) & JUNK)
            check("B1-%s-%s-zero-junk" % (family, dialect), not junk, {
                "family": family, "dialect": dialect, "junk_employers": junk,
            })

    parity_rows = []
    for family in FAMILY_SPECS:
        baseline = summary(records[(family, "heading")]["document"] or {})
        for dialect in DIALECTS[1:]:
            actual = summary(records[(family, dialect)]["document"] or {})
            parity_rows.append({"family": family, "left": "heading", "right": dialect, "equal": actual == baseline})
    check("B1-cross-dialect-parity", len(parity_rows) >= 4 and all(row["equal"] for row in parity_rows), {
        "pair_count": len(parity_rows), "pairs": parity_rows,
    })

    repeated = records[("data", "repeated")]["document"] or {}
    school0 = next((item for item in repeated.get("anchors", [])
                    if item.get("type") == "school" and item.get("value") == "University of Toronto"), None)
    employer0 = next((item for item in repeated.get("anchors", [])
                      if item.get("type") == "prior_employer" and item.get("value") == "Ionwave Systems"), None)
    earliest_ok = bool(school0 and employer0)
    if earliest_ok:
        earliest_ok = school0["evidence"]["line"] == 4 and employer0["evidence"]["line"] == 10
        # The first of three repeated sections is the sole carrier of this
        # alumni anchor; the later section still contributes the second school.
        earliest_ok = earliest_ok and not school0.get("attributes", {}).get("also_observed_at")
        earliest_ok = earliest_ok and not employer0.get("attributes", {}).get("also_observed_at")
    check("B1-repeated-earliest-anchor", earliest_ok, {
        "school": school0,
        "employer": employer0,
    })

    skipped_ok, skipped_evidence = thin_skip_check(workdir)
    check("B1-explicit-skip-reasons", skipped_ok, skipped_evidence)
    check("B1-fixture-shape", len(FAMILY_SPECS) * len(DIALECTS) >= 10, {
        "synthetic_fixture_count": len(FAMILY_SPECS) * len(DIALECTS),
        "field_families": sorted(FAMILY_SPECS), "dialects": list(DIALECTS),
    })

    payload = {
        "benchmark": "people-finder-v1",
        "criterion": "B1",
        "offline": True,
        "passed": all(row["passed"] for row in assertions),
        "assertions": assertions,
        "test_count": len(assertions),
        "synthetic_fixture_count": len(FAMILY_SPECS) * len(DIALECTS),
        "scratch_dir": workdir,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("check_profile_understanding harness error: %r\n" % (exc,))
        print(json.dumps({
            "benchmark": "people-finder-v1", "criterion": "B1", "offline": True,
            "passed": False, "assertions": [{"id": "HARNESS", "passed": False,
            "evidence": {"error": repr(exc)}}], "test_count": 1,
        }, sort_keys=True))
        sys.exit(2)
