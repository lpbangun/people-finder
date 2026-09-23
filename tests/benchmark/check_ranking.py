#!/usr/bin/env python3
"""Criterion R — ranking, provenance, and non-invention.

Command: run with the active Python interpreter.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (JOB_A, JOB_B, JOB_C, RESUME_A, RESUME_B, RESUME_C,
                      SERP_A, SERP_B, SERP_C, claim_token_hits, clean_env,
                      compile_fixture, digest, expect, rank_fixture, read_json,
                      product_command, read_text, require_product, run_check, run_cmd, scratch,
                      strip_nested, truthy_forbidden_keys, write_json)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PUBLIC_URL_RE = re.compile(r"^https://www\.linkedin\.com/in/[A-Za-z0-9%\-._~]+$")
STAMP_PATHS = {"alumni_at_target", "prior_employer_at_target", "community_at_target", "shared_stamp"}
FORBIDDEN_TRUTHY = {"identity_confirmed", "identity_established", "approved", "human_approved",
                    "humanapproved", "reachable", "contactable", "connected", "connection",
                    "second_degree", "delivered", "sent", "email", "emails", "phone",
                    "channel", "delivery_route"}


def all_items(document):
    return list(document["candidates"]) + list(document["hiring_adjacent"])


def ordered_summary(document):
    return [
        {
            "candidate_id": item["candidate_id"],
            "url": item["public_url"],
            "name": item["name"],
            "score": item["score"],
            "paths": item["paths"],
            "unknowns": item["unknowns"],
        }
        for item in all_items(document)
    ]


def body(result):
    require_product()
    workdir = scratch("ranking")

    queries = {}
    ranked = {}
    for key, (resume, job, serp) in {
        "a": (RESUME_A, JOB_A, SERP_A),
        "b": (RESUME_B, JOB_B, SERP_B),
        "c": (RESUME_C, JOB_C, SERP_C),
    }.items():
        q_out = os.path.join(workdir, f"{key}-queries.json")
        c_out = os.path.join(workdir, f"{key}-candidates-1.json")
        c2_out = os.path.join(workdir, f"{key}-candidates-2.json")
        first = compile_fixture(resume, job, q_out)
        expect(first["returncode"] == 0, f"compile {key} failed: {first['stderr'][:300]}")
        run_one = rank_fixture(q_out, serp, c_out)
        expect(run_one["returncode"] == 0, f"rank {key} failed: {run_one['stderr'][:300]}")
        run_two = rank_fixture(q_out, serp, c2_out)
        expect(run_two["returncode"] == 0, f"second rank {key} failed: {run_two['stderr'][:300]}")
        queries[key] = {"path": q_out, "document": read_json(q_out), "compile": first}
        ranked[key] = {
            "first": read_json(c_out), "second": read_json(c2_out),
            "run": run_one, "run_two": run_two, "path": c_out, "path_two": c2_out,
            "serp": serp, "serp_text": read_text(serp),
        }

    doc_a = ranked["a"]["first"]
    doc_b = ranked["b"]["first"]
    doc_c = ranked["c"]["first"]

    # ---- R1: shape, public URL provenance, arrays -------------------------
    validation_record = run_cmd(product_command("validate", ranked["a"]["path"]), env=clean_env())
    validate_json = validation_record["json"] or {}
    shape_rows = []
    shape_ok = doc_a.get("schema") == "people-candidates.v1"
    for lane_key, expected_lane in (("candidates", "peer"), ("hiring_adjacent", "hiring_adjacent")):
        for item in doc_a[lane_key]:
            url = item["public_url"]
            observed = url.replace("https://www.linkedin.com/in/", "").split("?")[0]
            row = {
                "candidate_id": item["candidate_id"],
                "lane_field": lane_key,
                "lane": item["lane"],
                "public_url": url,
                "url_is_public_profile": bool(PUBLIC_URL_RE.match(url)),
                "url_observed_in_fixture": observed in ranked["a"]["serp_text"],
                "paths_is_array": isinstance(item["paths"], list),
                "unknowns_is_array": isinstance(item["unknowns"], list),
                "unknowns_non_empty": bool(item["unknowns"]),
                "url_observed_in": item["url_observed_in"],
            }
            shape_rows.append(row)
            shape_ok = shape_ok and row["url_is_public_profile"] and row["url_observed_in_fixture"] \
                and row["paths_is_array"] and row["unknowns_is_array"] and row["unknowns_non_empty"] \
                and row["lane"] == expected_lane and bool(item["url_observed_in"])
    result.check("R1", shape_ok and validate_json.get("ok") is True, {
        "validate_invocation": validation_record["command"],
        "validate_result": validate_json,
        "rank_invocation": ranked["a"]["run"]["command"],
        "fixture": SERP_A,
        "items": shape_rows,
        "counts": doc_a["counts"],
    })

    # ---- R2: fired paths carry their own evidence -------------------------
    def path_evidence(document, pack_types):
        rows = []
        for item in all_items(document):
            for path_name in item["paths"]:
                allowed = pack_types.get(path_name, set())
                matching = [
                    row for row in item["score_breakdown"]
                    if row["path"] == path_name
                    and (row["anchor_id"] is None or row["anchor_type"] in allowed)
                ]
                rows.append({
                    "candidate_id": item["candidate_id"],
                    "path": path_name,
                    "evidence_rows": [
                        {"anchor_type": row["anchor_type"], "value": row["value"],
                         "observed_in": row["observed_in"]}
                        for row in matching
                    ],
                })
        return rows

    pack_types_a = {pack["pack_id"]: set(pack["match_anchor_types"]) for pack in queries["a"]["document"]["packs"]}
    evidence_a = path_evidence(doc_a, pack_types_a)
    alumni_rows = [row for row in evidence_a
                   if row["path"] == "alumni_at_target"
                   and any(item["anchor_type"] in ("school", "program") for item in row["evidence_rows"])]
    community_rows = [row for row in evidence_a
                      if row["path"] == "community_at_target"
                      and any(item["anchor_type"] == "rare_community" for item in row["evidence_rows"])]
    unsupported = [row for row in evidence_a if not row["evidence_rows"]]
    result.check("R2", bool(alumni_rows) and bool(community_rows) and not unsupported, {
        "rank_invocation": ranked["a"]["run"]["command"],
        "fixture": SERP_A,
        "fired_paths_by_candidate": {item["candidate_id"]: item["paths"] for item in all_items(doc_a)},
        "alumni_evidence": alumni_rows,
        "community_evidence": community_rows,
        "paths_without_evidence": unsupported,
    })

    # ---- R3: prior employer outranks generic function matches -------------
    prior_items = [item for item in doc_b["candidates"] if "prior_employer_at_target" in item["paths"]]
    generic_items = [item for item in doc_b["candidates"]
                     if set(item["anchor_types_matched"]) <= {"function", "target_employer"}]
    prior_score = max((item["score"] for item in prior_items), default=None)
    generic_score = max((item["score"] for item in generic_items), default=None)
    order_ok = bool(prior_items) and doc_b["candidates"][0]["candidate_id"] == prior_items[0]["candidate_id"]
    result.check(
        "R3",
        bool(prior_items) and bool(generic_items) and prior_score > generic_score and order_ok,
        {
            "rank_invocation": ranked["b"]["run"]["command"],
            "fixture": SERP_B,
            "peer_order": [(item["candidate_id"], item["name"], item["score"], item["paths"])
                           for item in doc_b["candidates"]],
            "prior_employer_candidates": [
                {"candidate_id": item["candidate_id"], "name": item["name"], "score": item["score"],
                 "top_anchor": next((row["value"] for row in item["score_breakdown"]
                                     if row["anchor_type"] == "prior_employer"), None)}
                for item in prior_items
            ],
            "generic_function_candidates": [
                {"candidate_id": item["candidate_id"], "name": item["name"], "score": item["score"],
                 "anchor_types": item["anchor_types_matched"]}
                for item in generic_items
            ],
            "prior_employer_score": prior_score,
            "best_generic_score": generic_score,
            "margin": (prior_score - generic_score) if (prior_score is not None and generic_score is not None) else None,
        },
    )

    # ---- R4: hiring-adjacent lane is separate and cannot reorder peers -----
    lane_rows = {}
    for key, document in (("a", doc_a), ("b", doc_b), ("c", doc_c)):
        lane_rows[key] = {
            "candidates": [(item["candidate_id"], item["lane"]) for item in document["candidates"]],
            "hiring_adjacent": [(item["candidate_id"], item["lane"], item["paths"])
                                for item in document["hiring_adjacent"]],
            "peer_urls_also_in_hiring_lane": [
                item["public_url"] for item in document["hiring_adjacent"]
                if item["public_url"] in {row["public_url"] for row in document["candidates"]}
            ],
        }
    # Isolate: swap the hiring-adjacent result for an unrelated profile hit with no
    # matching anchors in a scratch copy, so the supplied-hit population and the
    # idf denominator stay fixed and only the lane membership changes.
    isolated = read_json(SERP_A)
    placeholder = {
        "rank": 1,
        "url": "https://www.linkedin.com/in/unrelated-placeholder",
        "title": "Unrelated Placeholder",
        "snippet": "No relevant terms observed.",
    }
    for entry in isolated["pack_results"]:
        if entry["pack_id"] == "hiring_adjacent":
            entry["results"] = [placeholder]
    isolated_path = write_json(os.path.join(workdir, "a-without-hiring-adjacent.json"), isolated)
    isolated_out = os.path.join(workdir, "a-candidates-isolated.json")
    isolated_run = rank_fixture(queries["a"]["path"], isolated_path, isolated_out)
    expect(isolated_run["returncode"] == 0, f"isolated rank failed: {isolated_run['stderr'][:300]}")
    isolated_doc = read_json(isolated_out)
    peer_columns = ("candidate_id", "public_url", "score", "paths", "unknowns")
    peers_with = [[item[column] for column in peer_columns] for item in doc_a["candidates"]]
    peers_without = [[item[column] for column in peer_columns] for item in isolated_doc["candidates"]]
    lane_ok = all(item["lane"] == "hiring_adjacent" for document in (doc_a, doc_b, doc_c)
                  for item in document["hiring_adjacent"])
    lane_ok = lane_ok and all(bool(document["hiring_adjacent"]) for document in (doc_a, doc_b, doc_c))
    lane_ok = lane_ok and not any(row["peer_urls_also_in_hiring_lane"] for row in lane_rows.values())
    result.check(
        "R4",
        lane_ok and peers_with == peers_without
        and doc_a["ordering_policy"]["cross_lane_ordering"] == "not_supported"
        and doc_a["ordering_policy"]["score_comparison"] == "within_lane_only",
        {
            "lanes": lane_rows,
            "ordering_policy": doc_a["ordering_policy"],
            "peer_order_unaffected_by_hiring_lane": peers_with == peers_without,
            "isolation_experiment": {
                "rank_invocation": isolated_run["command"],
                "fixture_copy": os.path.relpath(isolated_path, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                "mutation": ("the hiring-adjacent result was replaced by an unrelated profile hit "
                             "with no matching anchors in a scratch copy, keeping the supplied-hit "
                             "population fixed; no frozen fixture was edited"),
                "peer_order_with_hiring_adjacent": peers_with,
                "peer_order_without_hiring_adjacent": peers_without,
                "peer_order_identical": peers_with == peers_without,
            },
        },
    )

    # ---- R5: thin/noisy fixture invents nothing ---------------------------
    fixture_text = ranked["c"]["serp_text"]
    invention_rows = []
    invention_ok = len(doc_c["candidates"]) + len(doc_c["hiring_adjacent"]) <= 1
    for item in all_items(doc_c):
        rows = {
            "candidate_id": item["candidate_id"],
            "name_observed_in_fixture": item["name"] in fixture_text if item["name"] else True,
            "url_observed_in_fixture": item["public_url"].split("/in/")[-1] in fixture_text,
            "headline_observed_in_fixture": item["headline_observed"] in fixture_text
            if item["headline_observed"] else True,
            "employer_observed_in_fixture": item["employer_observed"]["value"] in fixture_text,
        }
        invention_rows.append(rows)
        invention_ok = invention_ok and all(rows.values())
    import json as _json
    email_hits = EMAIL_RE.findall(_json.dumps(doc_c))
    forbidden_truthy = truthy_forbidden_keys(doc_c, FORBIDDEN_TRUTHY)
    suppressed_urls_observed = all(
        row["url"].split("/in/")[-1].split("?")[0] in fixture_text for row in doc_c["suppressed"]
    )
    result.check(
        "R5",
        invention_ok and not email_hits and not forbidden_truthy and suppressed_urls_observed,
        {
            "rank_invocation": ranked["c"]["run"]["command"],
            "fixture": SERP_C,
            "candidates_returned": len(doc_c["candidates"]),
            "hiring_adjacent_returned": len(doc_c["hiring_adjacent"]),
            "total_items": len(doc_c["candidates"]) + len(doc_c["hiring_adjacent"]),
            "at_most_one_candidate": len(doc_c["candidates"]) + len(doc_c["hiring_adjacent"]) <= 1,
            "invention_checks": invention_rows,
            "suppressed": doc_c["suppressed"],
            "suppressed_urls_observed_in_fixture": suppressed_urls_observed,
            "email_like_strings_in_output": email_hits,
            "forbidden_truthy_keys": forbidden_truthy,
            "serp_fixture_bytes": len(fixture_text),
        },
    )

    # ---- R6: unknowns and no relationship claims --------------------------
    unknowns_rows = [
        {"candidate_id": item["candidate_id"], "unknowns": item["unknowns"],
         "state": item["state"], "lane": item["lane"]}
        for item in all_items(doc_a) + all_items(doc_b)
    ]
    state_ok = all(item["state"]["stage"] == "discovery"
                   and item["state"]["identity"] == "not_established"
                   and item["state"]["approval"] == "not_requested"
                   for item in all_items(doc_a) + all_items(doc_b))
    stamp_items = [item for item in all_items(doc_a) if "shared_stamp" in item["paths"]]
    stamp_labelled = all(
        any("Public-stamp proxy" in note for note in item["notes"])
        and "public_stamp_proxy_only_not_member_graph_edge" in item["unknowns"]
        for item in stamp_items
    )
    result.check(
        "R6",
        all(row["unknowns"] for row in unknowns_rows)
        and bool(doc_a["unknowns"]) and state_ok and stamp_labelled
        and not claim_token_hits(doc_a) and not claim_token_hits(doc_b)
        and not truthy_forbidden_keys(doc_a, FORBIDDEN_TRUTHY),
        {
            "document_unknowns": doc_a["unknowns"],
            "candidates_with_unknowns": unknowns_rows,
            "shared_stamp_candidates": [
                {"candidate_id": item["candidate_id"], "paths": item["paths"],
                 "notes": item["notes"], "unknowns": item["unknowns"]}
                for item in stamp_items
            ],
            "shared_stamp_proxy_labelled": stamp_labelled,
            "claim_token_hits": claim_token_hits(doc_a) + claim_token_hits(doc_b),
            "forbidden_truthy_keys": truthy_forbidden_keys(doc_a, FORBIDDEN_TRUTHY),
        },
    )

    # ---- R7: duplicate URLs merge; unsupported path tags are not added ----
    variant_rows = []
    for item in doc_a["candidates"]:
        if "ananya-rao" in item["public_url"]:
            variant_rows.append({
                "candidate_id": item["candidate_id"],
                "public_url": item["public_url"],
                "url_observed_in": item["url_observed_in"],
                "paths": item["paths"],
                "unknowns": item["unknowns"],
            })
    urls = [item["public_url"] for item in all_items(doc_a)]
    run_serp_text = ranked["a"]["serp_text"]
    tracking_variants = [match for match in re.findall(r"https://www\.linkedin\.com/in/[A-Za-z0-9%\-._~?=&]+", run_serp_text)
                         if "?" in match]
    path_type_ok = True
    for pack_id, allowed in pack_types_a.items():
        for item in all_items(doc_a):
            for row in item["score_breakdown"]:
                if row["path"] != pack_id:
                    continue
                if row["anchor_id"] is not None and row["anchor_type"] not in allowed:
                    path_type_ok = False
    hiring_stamp_leak = [
        item["candidate_id"] for item in doc_a["hiring_adjacent"]
        if set(item["paths"]) & STAMP_PATHS
    ]
    result.check(
        "R7",
        len(urls) == len(set(urls))
        and bool(variant_rows)
        and len(variant_rows) == 1
        and len(variant_rows[0]["url_observed_in"]) >= 3
        and {"alumni_at_target", "community_at_target"}.issubset(set(variant_rows[0]["paths"]))
        and len(tracking_variants) >= 2
        and path_type_ok and not hiring_stamp_leak,
        {
            "merged_candidate": variant_rows,
            "requested_url_variants_in_fixture": tracking_variants,
            "distinct_candidate_urls": len(set(urls)),
            "emitted_items": len(urls),
            "every_claimed_path_matches_its_pack_anchor_types": path_type_ok,
            "hiring_adjacent_items_with_peer_stamp_paths": hiring_stamp_leak,
        },
    )

    # ---- R8: deterministic ranking ---------------------------------------
    determinism = {}
    identical = True
    for key in ("a", "b"):
        first_doc = ranked[key]["first"]
        second_doc = ranked[key]["second"]
        excluded = first_doc.get("excluded_for_determinism") or []
        left = strip_nested(first_doc, excluded)
        right = strip_nested(second_doc, excluded)
        same = left == right
        identical = identical and same
        determinism[key] = {
            "invocations": [ranked[key]["run"]["command"], ranked[key]["run_two"]["command"]],
            "excluded_for_determinism": excluded,
            "timestamps": [first_doc.get("generated_at"), second_doc.get("generated_at")],
            "order_scores_paths_unknowns_identical": ordered_summary(first_doc) == ordered_summary(second_doc),
            "full_document_identical_after_exclusions": same,
            "sha256_16_run1": digest(left),
            "sha256_16_run2": digest(right),
        }
    result.check(
        "R8",
        identical and all(row["order_scores_paths_unknowns_identical"] for row in determinism.values()),
        {"determinism": determinism, "output_paths": {key: [ranked[key]["path"], ranked[key]["path_two"]] for key in ("a", "b")}},
    )

    result.note(
        fixtures={"resumes": [RESUME_A, RESUME_B, RESUME_C], "jobs": [JOB_A, JOB_B, JOB_C],
                  "serps": [SERP_A, SERP_B, SERP_C]},
        commands_executed=[ranked[key]["run"]["command"] for key in ("a", "b", "c")]
        + [ranked[key]["run_two"]["command"] for key in ("a", "b", "c")] + [isolated_run["command"]],
        scratch_dir=workdir,
    )


if __name__ == "__main__":
    sys.exit(run_check("R", body))
