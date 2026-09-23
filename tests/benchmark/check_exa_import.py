#!/usr/bin/env python3
"""Criterion E — recorded Exa import.

Command: run with the active Python interpreter.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (CREDENTIAL_ENV_KEYS, EXA_MULTIPLE_INVALID, EXA_NO_RESULT,
                      EXA_ONE_PERSON, JOB_A, RESUME_A, SERP_A, canonical, clean_env,
                      compile_fixture, expect, rank_fixture, read_json,
                      product_command, require_product, removed_env_keys, run_check,
                      run_cmd, scan_tokens, scratch, source_files, truthy_forbidden_keys,
                      write_json)

FORBIDDEN_TRUTHY = {"identity_confirmed", "identity_established", "approved", "human_approved",
                    "reachable", "contactable", "connected", "connection", "second_degree",
                    "contacted", "emailed", "sent"}

# Key names a provider route would need, plus every way this product could read
# one from the environment or reach a provider over the network.
PROVIDER_KEY_TOKENS = ("exa_api_key", "openai_api_key", "anthropic_api_key",
                       "serpapi_api_key", "apify_token", "harvest_api_key",
                       "pdl_api_key", "google_api_key")
ENV_READ_TOKENS = ("os.environ", "os.getenv", "getenv(", "environ.get", "environ[")
NETWORK_CLIENT_TOKENS = ("urllib.request", "requests.", "http.client", "httpx",
                         "socket.socket", "aiohttp")
REJECTION_CONTEXT_RE = re.compile(r"(banned|forbidden|must not|reject|invalid|never)", re.I)


def import_run(candidates, envelope, out):
    return run_cmd(product_command("import-exa", "--candidates", candidates, "--result", envelope,
                                   "--out", out), env=clean_env())


def envelope_shape(relative):
    document = read_json(relative)
    return {
        "envelope": relative,
        "keys": sorted(document.keys()),
        "route": document.get("route"),
        "tool": document.get("tool"),
        "retrieved_at": document.get("retrieved_at"),
        "status": document.get("status"),
        "subject": document.get("subject"),
        "has_lead_key": "lead" in document,
        "lead": document.get("lead"),
    }


def body(result):
    require_product()
    workdir = scratch("exa-import")

    queries_path = os.path.join(workdir, "a-queries.json")
    base_path = os.path.join(workdir, "a-candidates.json")
    expect(compile_fixture(RESUME_A, JOB_A, queries_path)["returncode"] == 0, "compile A failed")
    base_run = rank_fixture(queries_path, SERP_A, base_path)
    expect(base_run["returncode"] == 0, f"rank A failed: {base_run['stderr'][:300]}")
    base = read_json(base_path)
    base_peer_count = len(base["candidates"])

    # ---- E1: zero-result envelope imports cleanly, adds nothing ------------
    no_result_shape = envelope_shape(EXA_NO_RESULT)
    no_result_out = os.path.join(workdir, "no-result-candidates.json")
    no_result_run = import_run(base_path, EXA_NO_RESULT, no_result_out)
    no_result_doc = read_json(no_result_out) if no_result_run["returncode"] == 0 else {}
    no_result_record = (no_result_doc.get("exa_imports") or [None])[0]
    result.check(
        "E1",
        no_result_run["returncode"] == 0
        and no_result_shape["status"] in ("no_result", "unavailable")
        and no_result_shape["lead"] is None
        and no_result_shape["has_lead_key"]
        and no_result_record is not None
        and no_result_record["status"] == "no_result"
        and no_result_record["lead_count"] == 0
        and no_result_record["added_candidate_ids"] == []
        and len(no_result_doc["candidates"]) == base_peer_count,
        {
            "command": no_result_run["command"],
            "fixture": EXA_NO_RESULT,
            "envelope": no_result_shape,
            "exit_code": no_result_run["returncode"],
            "import_record": no_result_record,
            "peers_before": base_peer_count,
            "peers_after": len(no_result_doc.get("candidates", [])),
            "candidates_added": 0,
            "output_path": no_result_out,
        },
    )

    # ---- E2: one-person envelope records the handoff, adds <= 1 lead -------
    one_shape = envelope_shape(EXA_ONE_PERSON)
    lead = one_shape["lead"] or {}
    one_out = os.path.join(workdir, "one-person-candidates.json")
    one_run = import_run(base_path, EXA_ONE_PERSON, one_out)
    expect(one_run["returncode"] == 0, f"one-person import failed rc={one_run['returncode']}: {one_run['stderr'][:300]}")
    one_doc = read_json(one_out)
    record = one_doc["exa_imports"][0]
    added = [item for item in one_doc["candidates"] if item["candidate_id"] in record["added_candidate_ids"]]
    source_urls = [row.get("url") for row in lead.get("sources", [])]
    result.check(
        "E2",
        record["route"] == one_shape["route"]
        and record["tool"] == one_shape["tool"]
        and record["retrieved_at"] == one_shape["retrieved_at"]
        and record["status"] == "completed"
        and record["subject"] == one_shape["subject"]
        and all(url in record["cited_source_urls"] for url in source_urls)
        and len(added) == 1
        and len(added) <= 1
        and added[0]["recall_source"]["tool"] == one_shape["tool"]
        and added[0]["recall_source"]["route"] == one_shape["route"]
        and added[0]["recall_source"]["retrieved_at"] == one_shape["retrieved_at"],
        {
            "command": one_run["command"],
            "fixture": EXA_ONE_PERSON,
            "envelope": {key: one_shape[key] for key in
                         ("route", "tool", "retrieved_at", "status", "subject")},
            "envelope_lead": lead,
            "import_record": record,
            "leads_added": len(added),
            "added_candidate": {
                "candidate_id": added[0]["candidate_id"] if added else None,
                "public_url": added[0]["public_url"] if added else None,
                "name_status": added[0]["name_status"] if added else None,
                "recall_source": added[0]["recall_source"] if added else None,
            },
            "peers_before": base_peer_count,
            "peers_after": len(one_doc["candidates"]),
            "output_path": one_out,
        },
    )

    # ---- E3: imported lead stays discovery evidence only ------------------
    added_state = added[0]["state"] if added else {}
    forbidden = truthy_forbidden_keys(one_doc, FORBIDDEN_TRUTHY)
    validate_record = run_cmd(product_command("validate", one_out), env=clean_env())
    validate_json = validate_record["json"] or {}
    result.check(
        "E3",
        added_state.get("stage") == "discovery"
        and added_state.get("identity") == "not_established"
        and added_state.get("approval") == "not_requested"
        and added_state.get("reachability") == "not_established"
        and added_state.get("contactability") == "not_established"
        and added[0]["paths"] == []
        and "path_evidence_not_observed_by_compiled_packs" in added[0]["unknowns"]
        and added[0]["recall_source"]["recall_only"] is True
        and added[0]["recall_source"]["provider_output"] is True
        and not forbidden
        and validate_json.get("ok") is True
        and one_doc["recall"]["provider_output_is_recall_only"] is True
        and one_doc["recall"]["live_provider_calls"] == 0,
        {
            "imported_candidate_id": added[0]["candidate_id"] if added else None,
            "state": added_state,
            "paths": added[0]["paths"] if added else None,
            "unknowns": added[0]["unknowns"] if added else None,
            "recall_source": added[0]["recall_source"] if added else None,
            "recall_block": one_doc.get("recall"),
            "forbidden_truthy_keys": forbidden,
            "validate_command": validate_record["command"],
            "validate_result": validate_json,
        },
    )

    # ---- E4: multiple-lead envelope is rejected without artifacts ---------
    invalid_fixture = read_json(EXA_MULTIPLE_INVALID)
    invalid_out = os.path.join(workdir, "invalid-multi-out.json")
    if os.path.exists(invalid_out):
        os.unlink(invalid_out)
    invalid_run = import_run(base_path, EXA_MULTIPLE_INVALID, invalid_out)
    result.check(
        "E4",
        invalid_run["returncode"] == 2
        and not os.path.exists(invalid_out)
        and not invalid_run["stdout"].strip()
        and bool(invalid_run["stderr"].strip())
        and len(invalid_fixture.get("leads", [])) == 2,
        {
            "command": invalid_run["command"],
            "fixture": EXA_MULTIPLE_INVALID,
            "fixture_leads": len(invalid_fixture.get("leads", [])),
            "exit_code": invalid_run["returncode"],
            "output_artifact_created": os.path.exists(invalid_out),
            "stdout": invalid_run["stdout"].strip()[:200],
            "stderr": invalid_run["stderr"].strip()[:300],
            "output_path": invalid_out,
        },
    )

    # ---- E5: contradictory or unattributed provider data is rejected ------
    one_envelope = read_json(EXA_ONE_PERSON)
    contradictory_status = dict(one_envelope)
    contradictory_status["status"] = "no_result"
    no_lead = dict(one_envelope)
    no_lead["status"] = "completed"
    no_lead["lead"] = None
    unattributed = read_json(EXA_ONE_PERSON)
    unattributed["lead"] = dict(unattributed["lead"])
    unattributed["lead"]["sources"] = []
    unknown_status = dict(one_envelope)
    unknown_status["status"] = "partial"

    cases = []
    for label, document in (
        ("status_no_result_with_lead", contradictory_status),
        ("status_completed_with_null_lead", no_lead),
        ("lead_without_source_attribution", unattributed),
        ("unknown_status_value", unknown_status),
    ):
        envelope_path = write_json(os.path.join(workdir, f"{label}.json"), document)
        out_path = os.path.join(workdir, f"{label}-out.json")
        if os.path.exists(out_path):
            os.unlink(out_path)
        run = import_run(base_path, envelope_path, out_path)
        cases.append({
            "case": label,
            "envelope": os.path.relpath(envelope_path, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "exit_code": run["returncode"],
            "artifact_created": os.path.exists(out_path),
            "stderr": run["stderr"].strip()[:200],
        })
    result.check(
        "E5",
        all(case["exit_code"] == 2 and not case["artifact_created"] for case in cases),
        {
            "base_envelope_fixture": EXA_ONE_PERSON,
            "cases": cases,
            "rejected_with_exit_2": all(case["exit_code"] == 2 for case in cases),
        },
    )

    # ---- E6: no provider key is required or read --------------------------
    sentinel_out = os.path.join(workdir, "sentinel-key-candidates.json")
    sentinel_env = clean_env({"EXA_API_KEY": "sentinel-value-never-read",
                              "OPENAI_API_KEY": "sentinel-value-never-read"})
    sentinel_run = run_cmd(product_command("import-exa", "--candidates", base_path,
                                           "--result", EXA_ONE_PERSON, "--out", sentinel_out),
                            env=sentinel_env)
    stripped_out = os.path.join(workdir, "stripped-env-candidates.json")
    stripped_run = import_run(base_path, EXA_ONE_PERSON, stripped_out)
    files = source_files("bin", "src")
    key_scan = scan_tokens(files, PROVIDER_KEY_TOKENS)
    env_read_scan = scan_tokens(files, ENV_READ_TOKENS)
    network_scan = scan_tokens(files, NETWORK_CLIENT_TOKENS)
    api_key_lines = [row for rows in scan_tokens(files, ("api_key",)).values() for row in rows]
    non_rejection_api_key_lines = [
        row for row in api_key_lines if not REJECTION_CONTEXT_RE.search(row.get("text", ""))
    ]
    identical = (sentinel_run["returncode"] == 0 and stripped_run["returncode"] == 0
                 and canonical(read_json(sentinel_out)) == canonical(read_json(stripped_out)))
    result.check(
        "E6",
        identical and not key_scan and not env_read_scan and not network_scan
        and not non_rejection_api_key_lines,
        {
            "credential_like_names_checked": len(CREDENTIAL_ENV_KEYS),
            "credential_like_env_removed_from_run": removed_env_keys(),
            "sentinel_keys_present_during_run": ["EXA_API_KEY", "OPENAI_API_KEY"],
            "sentinel_run": {"command": sentinel_run["command"], "exit_code": sentinel_run["returncode"]},
            "stripped_env_run": {"command": stripped_run["command"], "exit_code": stripped_run["returncode"],
                                 "env_keys_present": sorted(k for k in stripped_run.get("env_used", [])
                                                            if k in ("EXA_API_KEY", "OPENAI_API_KEY"))},
            "outputs_identical_with_and_without_provider_keys": identical,
            "provider_key_names_in_product_source": key_scan,
            "environment_reads_in_product_source": env_read_scan,
            "network_clients_in_product_source": network_scan,
            "api_key_occurrences_in_product_source": api_key_lines,
            "api_key_lines_without_rejection_context": non_rejection_api_key_lines,
            "product_source_files_scanned": len(files),
        },
    )

    # ---- E7: repeats are identical and do not duplicate the lead ----------
    repeat_out = os.path.join(workdir, "one-person-candidates-repeat.json")
    repeat_run = import_run(base_path, EXA_ONE_PERSON, repeat_out)
    chained_out = os.path.join(workdir, "one-person-candidates-chained.json")
    chained_run = import_run(one_out, EXA_ONE_PERSON, chained_out)
    repeat_doc = read_json(repeat_out) if repeat_run["returncode"] == 0 else {}
    chained_doc = read_json(chained_out) if chained_run["returncode"] == 0 else {}
    first_identical = canonical(one_doc) == canonical(repeat_doc)
    chain_identical = canonical(one_doc) == canonical(chained_doc)
    result.check(
        "E7",
        repeat_run["returncode"] == 0 and chained_run["returncode"] == 0
        and first_identical and chain_identical
        and len(one_doc["exa_imports"]) == 1 and len(chained_doc.get("exa_imports", [])) == 1
        and len(one_doc["candidates"]) == base_peer_count + 1
        and len(chained_doc.get("candidates", [])) == base_peer_count + 1,
        {
            "invocations": [one_run["command"], repeat_run["command"], chained_run["command"]],
            "output_paths": [one_out, repeat_out, chained_out],
            "identical_repeat_from_same_input": first_identical,
            "identical_when_reimported_from_its_own_output": chain_identical,
            "peer_counts": {
                "base": base_peer_count,
                "after_import": len(one_doc["candidates"]),
                "after_repeat": len(repeat_doc.get("candidates", [])),
                "after_chained_import": len(chained_doc.get("candidates", [])),
            },
            "exa_import_records": len(one_doc["exa_imports"]),
            "cited_source_urls": one_doc["exa_imports"][0]["cited_source_urls"],
        },
    )

    result.note(
        fixtures=[EXA_NO_RESULT, EXA_ONE_PERSON, EXA_MULTIPLE_INVALID],
        scratch_dir=workdir,
        base_candidate_document=base_path,
    )


if __name__ == "__main__":
    sys.exit(run_check("E", body))
