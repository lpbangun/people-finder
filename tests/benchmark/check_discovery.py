#!/usr/bin/env python3
"""Criterion D — typed discovery and query packs.

Command: run with the active Python interpreter.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (EXA_NO_RESULT, FIXTURE_PATHS, JOB_A, JOB_B, JOB_C, RESUME_A,
                      RESUME_B, RESUME_C, claim_token_hits, compile_fixture, digest,
                      expect, path, read_json, read_text, require_product, run_check,
                      scratch, strip_fields)

RESTRICTED = ("rare_community", "school", "program", "prior_employer")
GENERIC_SKILLS = {"python", "sql", "git", "rest apis", "rest", "apis", "spark",
                  "dbt", "airflow", "kubernetes", "kafka", "terraform", "go"}


def anchors_of(compiled, types):
    return [item for item in compiled["anchors"] if item["type"] in types]


def traceable(anchor, resume_text, job_text):
    quote = anchor.get("evidence", {}).get("quote", "")
    if not quote:
        return False
    if anchor["evidence"]["field"].startswith("resume:"):
        return quote in resume_text
    return quote in job_text


def body(result):
    require_product()
    workdir = scratch("discovery")
    expect(os.path.isfile(path(RESUME_A)), f"missing fixture {RESUME_A}")

    resume_a = read_text(RESUME_A)
    resume_b = read_text(RESUME_B)
    job_a_text = read_text(JOB_A)
    job_b_text = read_text(JOB_B)

    # ---- run 1 and run 2 (D7 needs two identical invocations) --------------
    out_first = os.path.join(workdir, "a-queries-1.json")
    out_second = os.path.join(workdir, "a-queries-2.json")
    first = compile_fixture(RESUME_A, JOB_A, out_first)
    second = compile_fixture(RESUME_A, JOB_A, out_second)
    expect(first["returncode"] == 0, f"compile A failed rc={first['returncode']}: {first['stderr'][:300]}")
    expect(second["returncode"] == 0, f"second compile A failed rc={second['returncode']}")
    compiled = read_json(out_first)
    compiled_second = read_json(out_second)

    out_b = os.path.join(workdir, "b-queries.json")
    b_run = compile_fixture(RESUME_B, JOB_B, out_b)
    expect(b_run["returncode"] == 0, f"compile B failed rc={b_run['returncode']}: {b_run['stderr'][:300]}")
    compiled_b = read_json(out_b)

    out_c = os.path.join(workdir, "c-queries.json")
    c_run = compile_fixture(RESUME_C, JOB_C, out_c)
    expect(c_run["returncode"] == 0, f"compile C failed rc={c_run['returncode']}: {c_run['stderr'][:300]}")
    compiled_c = read_json(out_c)

    # ---- D1: typed anchors with traceable evidence -------------------------
    groups = {
        "school_or_program": ("school", "program"),
        "rare_community_or_lab": ("rare_community",),
        "target_employer": ("target_employer",),
        "job_derived_function": ("function",),
    }
    d1_evidence = {}
    d1_passed = True
    for label, types in groups.items():
        picked = anchors_of(compiled, types)
        rows = [
            {
                "anchor_id": anchor["anchor_id"],
                "type": anchor["type"],
                "value": anchor["value"],
                "weight": anchor["weight"],
                "evidence_field": anchor["evidence"]["field"],
                "evidence_quote": anchor["evidence"]["quote"],
                "evidence_source": anchor["evidence"]["source"],
                "quote_traceable_to_supplied_text": traceable(anchor, resume_a, job_a_text),
            }
            for anchor in picked
        ]
        d1_evidence[label] = {"count": len(rows), "anchors": rows[:3]}
        ok = bool(rows) and all(row["quote_traceable_to_supplied_text"] for row in rows)
        if label == "job_derived_function":
            ok = ok and all(row["evidence_field"].startswith("job:") for row in rows)
        d1_passed = d1_passed and ok
    result.check("D1", d1_passed, {
        "fixture_resume": RESUME_A,
        "fixture_job": JOB_A,
        "typed_anchor_groups": d1_evidence,
        "all_restricted_anchor_types_used_by_ranking": sorted({
            anchor["type"] for anchor in compiled["anchors"] if anchor["used_in_ranking"]
        }),
        "anchor_summary": compiled["anchor_summary"],
    })

    # ---- D2: several packs, not school x employer --------------------------
    pack_ids = [pack["pack_id"] for pack in compiled["packs"]]
    required = {"alumni_at_target", "community_at_target", "function_at_target"}
    school_packs = [pack["pack_id"] for pack in compiled["packs"]
                    if set(pack["match_anchor_types"]) & {"school", "program"}]
    non_school_packs = [pack["pack_id"] for pack in compiled["packs"]
                        if not (set(pack["match_anchor_types"]) & {"school", "program"})]
    result.check("D2", required.issubset(set(pack_ids)) and len(non_school_packs) >= 2, {
        "compiled_pack_ids": pack_ids,
        "required_packs_present": sorted(required.intersection(pack_ids)),
        "packs_relying_on_school_or_program": school_packs,
        "packs_that_do_not_rely_on_school": non_school_packs,
        "school_x_employer_is_not_the_whole_product": len(non_school_packs) >= 2,
        "compiled_document_sha256_16": digest(compiled),
        "invocation": first["command"],
        "output_path": out_first,
    })

    # ---- D3: prior employer from the seeker's own history ------------------
    b_employers = [anchor for anchor in compiled_b["anchors"] if anchor["type"] == "prior_employer"]
    b_values = [anchor["value"] for anchor in b_employers]
    b_expected = "Ionwave Systems"
    b_pack = next((pack for pack in compiled_b["packs"] if pack["pack_id"] == "prior_employer_at_target"), None)
    b_traceable = all(traceable(anchor, resume_b, job_b_text) for anchor in b_employers) and bool(b_employers)
    result.check(
        "D3",
        b_expected.lower() in [value.lower() for value in b_values]
        and b_pack is not None
        and b_expected.lower() in b_pack["query"].lower()
        and "Bluecrest Analytics".lower() in b_pack["query"].lower()
        and b_traceable,
        {
            "fixture_resume": RESUME_B,
            "fixture_job": JOB_B,
            "prior_employer_anchors": [
                {"value": anchor["value"],
                 "evidence_field": anchor["evidence"]["field"],
                 "evidence_quote": anchor["evidence"]["quote"],
                 "quote_traceable": traceable(anchor, resume_b, job_b_text)}
                for anchor in b_employers
            ],
            "compiled_pack": b_pack,
            "invocation": b_run["command"],
        },
    )

    # ---- D4: multiple packs, hiring_adjacent is its own lane ---------------
    lanes = {pack["pack_id"]: pack["lane"] for pack in compiled["packs"]}
    hiring = next((pack for pack in compiled["packs"] if pack["pack_id"] == "hiring_adjacent"), None)
    peer_packs = [pack_id for pack_id, lane in lanes.items() if lane == "peer"]
    result.check(
        "D4",
        len(compiled["packs"]) >= 4
        and hiring is not None
        and hiring["lane"] == "hiring_adjacent"
        and hiring.get("mixed_with_peer_lane") is False
        and len(peer_packs) >= 3
        and compiled["lane_policy"]["cross_lane_ordering"] == "not_supported"
        and bool(compiled["lane_policy"]["hiring_adjacent"]),
        {
            "pack_lanes": lanes,
            "peer_pack_count": len(peer_packs),
            "hiring_adjacent_lane": "hiring_adjacent",
            "hiring_adjacent_lexicon": hiring.get("lexicon") if hiring else None,
            "hiring_adjacent_mixed_with_peer_lane": hiring.get("mixed_with_peer_lane") if hiring else None,
            "lane_policy": compiled["lane_policy"],
        },
    )

    # ---- D5: shared_stamp is a public-stamp proxy only ---------------------
    stamp = next((pack for pack in compiled["packs"] if pack["pack_id"] == "shared_stamp"), None)
    claim_hits = claim_token_hits(compiled)
    stamp_queries = [entry["query"] for entry in (stamp or {}).get("queries", [])]
    result.check(
        "D5",
        stamp is not None
        and stamp.get("proxy_label") == "public_stamp_proxy"
        and stamp.get("graph_edge_claimed") is False
        and "linkedin_member_graph_edge" in stamp.get("not_claimed", [])
        and bool(stamp.get("disclaimer"))
        and not claim_hits,
        {
            "shared_stamp_pack": {
                "pack_id": stamp.get("pack_id") if stamp else None,
                "proxy_label": stamp.get("proxy_label") if stamp else None,
                "graph_edge_claimed": stamp.get("graph_edge_claimed") if stamp else None,
                "not_claimed": stamp.get("not_claimed") if stamp else None,
                "disclaimer": stamp.get("disclaimer") if stamp else None,
                "match_anchor_types": stamp.get("match_anchor_types") if stamp else None,
                "queries": stamp_queries,
            },
            "claim_token_hits_in_compiled_output": claim_hits,
        },
    )

    # ---- D6: thin/noisy fixture promotes nothing --------------------------
    c_restricted = anchors_of(compiled_c, RESTRICTED)
    c_skills = [anchor for anchor in compiled_c["anchors"] if anchor["type"] == "skill"]
    c_unknowns = [(item["anchor_type"], item["status"]) for item in compiled_c["unknowns"]]
    c_seeker_name = compiled_c["seeker"]["name"]
    skills_from_restricted = [
        anchor for anchor in c_restricted
        if anchor["evidence"]["field"] == "resume:skills"
        or anchor["value"].strip().lower() in GENERIC_SKILLS
        or anchor["value"].strip().lower() == c_seeker_name.strip().lower()
    ]
    required_unknowns = {"school", "prior_employer", "rare_community"}
    result.check(
        "D6",
        not c_restricted
        and not skills_from_restricted
        and all(anchor["default_off"] and not anchor["used_in_ranking"] and anchor["weight"] == 0
                for anchor in c_skills)
        and required_unknowns.issubset({item["anchor_type"] for item in compiled_c["unknowns"]})
        and all(item["status"] == "unknown" for item in compiled_c["unknowns"]),
        {
            "fixture_resume": RESUME_C,
            "restricted_anchor_count": len(c_restricted),
            "restricted_anchors": c_restricted,
            "skills_promoted_into_restricted_types": skills_from_restricted,
            "skill_anchors": [
                {"value": anchor["value"], "weight": anchor["weight"],
                 "default_off": anchor["default_off"], "used_in_ranking": anchor["used_in_ranking"],
                 "generic": anchor["generic"]}
                for anchor in c_skills
            ],
            "seeker_name_not_used_as_anchor": all(
                anchor["value"].strip().lower() != (compiled_c["seeker"]["name"] or "").strip().lower()
                for anchor in compiled_c["anchors"]
            ),
            "unknowns": compiled_c["unknowns"],
            "unknown_anchor_types": sorted({item["anchor_type"] for item in compiled_c["unknowns"]}),
            "packs_skipped": [item["pack_id"] for item in compiled_c["packs_skipped"]],
            "compiled_pack_ids": [pack["pack_id"] for pack in compiled_c["packs"]],
        },
    )

    # ---- D7: identical inputs -> identical output -------------------------
    excluded = compiled.get("excluded_for_determinism") or []
    reduced_first = strip_fields(compiled, excluded) if excluded else compiled
    reduced_second = strip_fields(compiled_second, excluded) if excluded else compiled_second
    result.check(
        "D7",
        bool(excluded)
        and reduced_first == reduced_second
        and ("generated_at" in excluded),
        {
            "excluded_for_determinism": excluded,
            "invocations": [first["command"], second["command"]],
            "timestamps_observed": [compiled.get("generated_at"), compiled_second.get("generated_at")],
            "semantically_identical_after_exclusions": reduced_first == reduced_second,
            "sha256_16_run1": digest(reduced_first),
            "sha256_16_run2": digest(reduced_second),
            "output_paths": [out_first, out_second],
        },
    )

    result.note(
        inputs={
            "fixtures": list(FIXTURE_PATHS),
            "scratch_dir": workdir,
            "exa_fixture_present": os.path.isfile(path(EXA_NO_RESULT)),
        },
        commands_executed=[first["command"], second["command"], b_run["command"], c_run["command"]],
    )


if __name__ == "__main__":
    sys.exit(run_check("D", body))
