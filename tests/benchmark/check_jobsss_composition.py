#!/usr/bin/env python3
"""Criterion J — Jobsss host composition.

Command: python3 tests/benchmark/check_jobsss_composition.py

The harness owns every Jobsss call: people-finder is invoked as a product and the
sibling ``jobsss`` executable is driven over its own stdio MCP surface against an
isolated temporary PLUGIN_DATA store. The store is removed on success and on
failure.
"""

import json
import os
import shutil
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (BIN, JOBSSS, JOB_B, RESUME_B, SERP_B, audit_guard, clean_env,
                      expect, guard_env, guard_violations, read_json, read_text,
                      require_product, run_check, run_cmd, scan_tokens, scratch,
                      sha256_file, source_files)

ALLOWED_JOBSSS_TOOLS = {
    "start", "create_profile", "import_job", "import_contact", "record_research",
    "list_contacts", "list_research", "map_reachable_network",
}

FORBIDDEN_JOBSSS_TOOLS = {
    "draft_outreach", "plan_outreach", "save_answer", "match_answers",
    "update_application_status", "create_decision_handoff", "list_decision_handoffs",
    "interview_debrief_handoff", "pursue_job", "tailor_resume", "draft_cover_letter",
    "preview_sync", "daily_discovery",
}

AUTHORITY_CLAIM_KEYS = {"relationship", "referral", "permission", "message_sent",
                        "delivered", "sent", "outreach_sent", "contacted"}

DESIGNATED_CANDIDATE = "elodie-marchand-platform"


class JobsssSession:
    """Harness-owned client for the sibling Jobsss stdio MCP server."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.calls = []
        self.cli_invocations = []

    def call(self, request_id, tool, arguments):
        self.calls.append({"id": request_id, "tool": tool, "arguments": arguments})
        record = run_cmd(
            [JOBSSS, "mcp", "--data", self.data_dir],
            env=clean_env({"PLUGIN_DATA": self.data_dir}),
            stdin_text=json.dumps({
                "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }) + "\n",
        )
        expect(record["returncode"] == 0,
               f"jobsss MCP call {tool} failed rc={record['returncode']}: {record['stderr'][:200]}")
        lines = [line for line in record["stdout"].splitlines() if line.strip()]
        expect(len(lines) == 1, f"jobsss MCP call {tool} produced {len(lines)} stdout lines")
        response = json.loads(lines[0])
        payload = json.loads(response["result"]["content"][0]["text"])
        return payload, record


def map_candidate_to_jobsss_args(candidate, *, profile_id, job_id, candidate_document,
                                 candidate_document_path):
    """Harness-owned mapping: one selected candidate -> explicit Jobsss arguments.

    This lives in host/composition code on purpose. The product never calls Jobsss
    and never writes its store.
    """
    target_company = candidate_document["target"]["company"]
    provenance = {
        "source_document": candidate_document_path,
        "schema": candidate_document["schema"],
        "candidate_id": candidate["candidate_id"],
        "public_url": candidate["public_url"],
        "url_observed_in": candidate["url_observed_in"],
        "paths": candidate["paths"],
        "unknowns": candidate["unknowns"],
        "score": candidate["score"],
        "stage": candidate["state"]["stage"],
        "relationship_status": "public_stamp_proxy_only",
    }
    contact_args = {
        "profileId": profile_id,
        "name": candidate["name"],
        "company": target_company,
        "role": candidate["headline_observed"],
        "relationship": "public_stamp_proxy_only",
        "source": "people-finder:people-candidates.v1",
        "notes": (
            "Discovery lead from people-finder ranking over recorded search results. "
            "Public profile URL kept in research findings. No prior relationship, "
            "permission, referral or delivery route is claimed; human review required."
        ),
        "text": json.dumps(provenance, indent=2),
    }
    research_args = {
        "profileId": profile_id,
        "jobId": job_id,
        "subjectName": candidate["name"],
        "subjectCompany": target_company,
        "source": f"{candidate_document['schema']}:{candidate['candidate_id']}",
        "notes": (
            "people-finder discovery evidence only. Identity, approval, reachability and "
            "contactability are not established by this record."
        ),
        "findings": [
            f"candidate_id: {candidate['candidate_id']}",
            f"public_url: {candidate['public_url']}",
            f"paths: {', '.join(candidate['paths'])}",
            f"unknowns: {', '.join(candidate['unknowns'])}",
            f"score: {candidate['score']}",
            f"observed_in: {', '.join(candidate['url_observed_in'])}",
            f"decision_owner: human (no automatic approval is requested or recorded)",
        ],
    }
    return contact_args, research_args, provenance


def snapshot_tree(root):
    rows = {}
    for current, dirs, files in os.walk(root):
        for name in sorted(files):
            full = os.path.join(current, name)
            rows[os.path.relpath(full, root)] = sha256_file(full)
        for name in sorted(dirs):
            rows[os.path.relpath(os.path.join(current, name), root) + "/"] = "dir"
    return rows


def body(result):
    require_product()
    workdir = scratch("composition")
    store_dir = os.path.join(workdir, "jobsss-plugin-data")
    expect(not os.path.exists(store_dir), "temporary store must not pre-exist")

    expect(os.path.isfile(JOBSSS), f"sibling Jobsss executable is unavailable: {JOBSSS}")
    expect(os.access(JOBSSS, os.X_OK), f"sibling Jobsss executable is not runnable: {JOBSSS}")

    session = JobsssSession(store_dir)
    people_work = os.path.join(workdir, "people-finder-out")
    os.makedirs(people_work, exist_ok=True)

    cleanup_done = False
    try:
        # ---- J1: fresh temporary store, sibling executable, no source import --
        contact_id = None
        research_id = None
        store_hashes_before = None

        result.check(
            "J1",
            not os.path.exists(store_dir)
            and os.path.isfile(JOBSSS)
            and not any("jobsss" in entry for entry in sys.path if entry)
            and "jobsss" not in sys.modules,
            {
                "sibling_executable": JOBSSS,
                "sibling_executable_bytes": os.path.getsize(JOBSSS),
                "temporary_plugin_data": store_dir,
                "store_existed_before": False,
                "invocation": f"{JOBSSS} mcp --data {store_dir}",
                "environment_passed_to_jobsss": {"PLUGIN_DATA": store_dir},
                "jobsss_source_imported_by_harness": "jobsss" in sys.modules,
                "sys_path_entries_referencing_jobsss": [entry for entry in sys.path if "jobsss" in entry],
            },
        )

        # ---- J2: real MCP calls: start, create_profile, import_job -----------
        resume_text = read_text(RESUME_B)
        job_card = read_json(JOB_B)
        seeker_name = next(
            (line.lstrip("# ").strip() for line in resume_text.splitlines() if line.startswith("# ")),
            "Marcus Delgado",
        )
        start_payload, start_record = session.call("j2-start", "start", {})
        profile_payload, profile_record = session.call(
            "j2-profile", "create_profile",
            {"name": seeker_name, "resumeText": resume_text},
        )
        job_payload, job_record = session.call(
            "j2-job", "import_job",
            {"profileId": profile_payload.get("profileId"), "text": job_card["posting_text"]},
        )
        profile_id = profile_payload.get("profileId")
        job_id = job_payload.get("jobId")
        result.check(
            "J2",
            start_payload.get("initialized") is True
            and bool(profile_id)
            and job_payload.get("job", {}).get("company") == job_card["company"]
            and bool(job_id),
            {
                "calls": [
                    {"id": "j2-start", "tool": "start", "command": start_record["command"]},
                    {"id": "j2-profile", "tool": "create_profile", "command": profile_record["command"],
                     "arguments": {"name": seeker_name, "resumeText": f"{len(resume_text)} bytes from {RESUME_B}"}},
                    {"id": "j2-job", "tool": "import_job", "command": job_record["command"],
                     "arguments": {"profileId": profile_id,
                                   "text": f"{len(job_card['posting_text'])} bytes from {JOB_B}"}},
                ],
                "start_result": {key: start_payload.get(key) for key in
                                 ("ok", "initialized", "schemaVersion", "revision", "storePath")},
                "profile_result": {"profileId": profile_id, "created": profile_payload.get("created"),
                                   "name": (profile_payload.get("profile") or {}).get("name")},
                "job_result": {"jobId": job_id, "created": job_payload.get("created"),
                               "company": (job_payload.get("job") or {}).get("company"),
                               "title": (job_payload.get("job") or {}).get("title")},
                "fixtures": {"resume": RESUME_B, "job": JOB_B},
            },
        )

        # ---- J3: people-finder runs with no PLUGIN_DATA and no reach ---------
        store_hashes_before = snapshot_tree(store_dir)
        original_mode = stat.S_IMODE(os.stat(store_dir).st_mode)
        os.chmod(store_dir, 0o000)
        guard = audit_guard("composition-audit", os.path.realpath(store_dir))
        env_for_product = guard_env(guard, base=clean_env())
        expect("PLUGIN_DATA" not in env_for_product, "PLUGIN_DATA leaked into the product environment")
        covered_mode = stat.S_IMODE(os.stat(store_dir).st_mode)
        queries_path = os.path.join(people_work, "b-queries.json")
        compile_run = run_cmd(
            [BIN, "compile", "--resume", RESUME_B, "--job", JOB_B,
             "--out", queries_path, "--quiet"],
            env=env_for_product,
        )
        candidates_path = os.path.join(people_work, "b-candidates.json")
        rank_run = run_cmd(
            [BIN, "rank", "--queries", queries_path, "--results", SERP_B,
             "--out", candidates_path, "--quiet"],
            env=env_for_product,
        )
        os.chmod(store_dir, original_mode)
        store_hashes_after = snapshot_tree(store_dir)
        violations = guard_violations(guard)
        result.check(
            "J3",
            compile_run["returncode"] == 0
            and rank_run["returncode"] == 0
            and "PLUGIN_DATA" not in env_for_product
            and covered_mode == 0o000
            and store_hashes_before == store_hashes_after
            and not violations,
            {
                "product_commands": [compile_run["command"], rank_run["command"]],
                "exit_codes": [compile_run["returncode"], rank_run["returncode"]],
                "product_env_has_PLUGIN_DATA": "PLUGIN_DATA" in env_for_product,
                "store_dir_mode_during_product_phase": oct(covered_mode),
                "store_dir_mode_restored_to": oct(stat.S_IMODE(os.stat(store_dir).st_mode)),
                "audit_guard": "sitecustomize audit hook rejects any open/stat/listdir under the store path",
                "audit_violations": violations,
                "store_file_hashes_before": store_hashes_before,
                "store_file_hashes_after": store_hashes_after,
                "store_unchanged": store_hashes_before == store_hashes_after,
            },
        )

        # ---- J4: ranking is offline and mutates nothing ---------------------
        candidates = read_json(candidates_path)
        contacts_after_rank, _ = session.call("j4-contacts", "list_contacts", {"profileId": profile_id})
        urls = [item["public_url"] for item in candidates["candidates"]]
        result.check(
            "J4",
            bool(candidates["candidates"])
            and all(url.startswith("https://www.linkedin.com/in/") for url in urls)
            and candidates["backend"]["network"] == "none"
            and contacts_after_rank.get("count") == 0
            and store_hashes_before == snapshot_tree(store_dir),
            {
                "rank_command": rank_run["command"],
                "fixture": SERP_B,
                "counts": candidates["counts"],
                "peer_urls": urls,
                "rank_backend": candidates["backend"],
                "jobsss_contacts_after_product_phase": contacts_after_rank.get("count"),
                "store_unchanged_after_product_phase": store_hashes_before == snapshot_tree(store_dir),
            },
        )

        # ---- J5: harness-owned mapping to explicit Jobsss arguments ---------
        selected = next(
            item for item in candidates["candidates"] if DESIGNATED_CANDIDATE in item["public_url"]
        )
        contact_args, research_args, provenance = map_candidate_to_jobsss_args(
            selected, profile_id=profile_id, job_id=job_id,
            candidate_document=candidates, candidate_document_path=candidates_path,
        )
        product_jobsss_refs = scan_tokens(source_files("bin", "src"), ("jobsss", "plugin_data",
                                                                      "import_contact", "record_research"))
        result.check(
            "J5",
            contact_args["name"] == selected["name"]
            and research_args["subjectName"] == selected["name"]
            and all(f"paths: {', '.join(selected['paths'])}" == item
                    for item in research_args["findings"] if item.startswith("paths: "))
            and not product_jobsss_refs,
            {
                "mapper_module": "tests/benchmark/check_jobsss_composition.py (host composition code)",
                "selected_candidate": {
                    "candidate_id": selected["candidate_id"],
                    "name": selected["name"],
                    "public_url": selected["public_url"],
                    "paths": selected["paths"],
                    "unknowns": selected["unknowns"],
                    "score": selected["score"],
                },
                "import_contact_arguments": contact_args,
                "record_research_arguments": research_args,
                "product_source_references_to_jobsss_surface": product_jobsss_refs,
            },
        )

        # ---- J6: the harness performs the Jobsss calls ----------------------
        contact_payload, contact_record = session.call("j6-contact", "import_contact", contact_args)
        research_payload, research_record = session.call("j6-research", "record_research", research_args)
        contact_id = contact_payload.get("contactId") or contact_payload.get("id")
        research_id = research_payload.get("researchId") or research_payload.get("id")
        result.check(
            "J6",
            bool(contact_id) and bool(research_id)
            and [call["tool"] for call in session.calls if call["id"].startswith("j6")] ==
            ["import_contact", "record_research"],
            {
                "calls": [
                    {"id": "j6-contact", "tool": "import_contact", "command": contact_record["command"],
                     "arguments": contact_args},
                    {"id": "j6-research", "tool": "record_research", "command": research_record["command"],
                     "arguments": research_args},
                ],
                "returned_contact_id": contact_id,
                "returned_research_id": research_id,
                "caller": "tests/benchmark/check_jobsss_composition.py (harness), not people-finder",
            },
        )

        # ---- J7: contact readback shows an unapproved record ----------------
        contacts, _ = session.call("j7-contacts", "list_contacts", {"profileId": profile_id})
        found_contact = next((item for item in contacts.get("contacts", [])
                              if item["id"] == contact_id), None)
        result.check(
            "J7",
            found_contact is not None
            and found_contact.get("humanApproved") is False
            and found_contact.get("name") == selected["name"]
            and found_contact.get("company") == candidates["target"]["company"],
            {
                "list_contacts_returned": contacts.get("count"),
                "contact_id": contact_id,
                "contact_record": {
                    "id": found_contact.get("id") if found_contact else None,
                    "name": found_contact.get("name") if found_contact else None,
                    "company": found_contact.get("company") if found_contact else None,
                    "humanApproved": found_contact.get("humanApproved") if found_contact else None,
                    "email": found_contact.get("email") if found_contact else None,
                    "relationshipEvidence": found_contact.get("relationshipEvidence") if found_contact else None,
                },
            },
        )

        # ---- J8: research readback preserves provenance ---------------------
        research, _ = session.call("j8-research", "list_research", {"profileId": profile_id})
        found_research = next((item for item in research.get("research", [])
                               if item["id"] == research_id), None)
        findings_text = " ".join((found_research or {}).get("findings") or [])
        provenance_ok = all(
            needle in findings_text for needle in
            [selected["public_url"], *selected["paths"], *selected["unknowns"], selected["candidate_id"]]
        )
        result.check(
            "J8",
            found_research is not None
            and provenance_ok
            and (found_research.get("notes") or "").strip() != ""
            and found_research.get("jobId") == job_id,
            {
                "list_research_returned": research.get("count"),
                "research_id": research_id,
                "research_record": {
                    "id": (found_research or {}).get("id"),
                    "jobId": (found_research or {}).get("jobId"),
                    "subjectName": (found_research or {}).get("subjectName"),
                    "subjectCompany": (found_research or {}).get("subjectCompany"),
                    "source": (found_research or {}).get("source"),
                    "findings": (found_research or {}).get("findings"),
                    "notes": (found_research or {}).get("notes"),
                },
                "provenance_preserved": {
                    "public_url": selected["public_url"] in findings_text,
                    "paths": [path_name for path_name in selected["paths"] if path_name in findings_text],
                    "unknowns": [unknown for unknown in selected["unknowns"] if unknown in findings_text],
                    "candidate_id": selected["candidate_id"] in findings_text,
                },
            },
        )

        # ---- J9: reachability map sees the record, claims nothing -----------
        mapped, mapped_record = session.call("j9-map", "map_reachable_network",
                                             {"profileId": profile_id, "jobId": job_id})
        mapped_people = mapped.get("people", [])
        mapped_research = mapped.get("research", [])
        seen_contact = any(item.get("id") == contact_id for item in mapped_people)
        seen_research = any(item.get("id") == research_id for item in mapped_research)
        serialized = json.dumps(mapped)
        claim_hits = {
            word: word in serialized.lower()
            for word in ("referral granted", "permission granted", "message delivered",
                         "we contacted", "introduced")
        }
        truthy_claims = [
            key for key in AUTHORITY_CLAIM_KEYS
            if isinstance(mapped.get(key), bool) and mapped.get(key)
        ]
        result.check(
            "J9",
            (seen_contact or seen_research) and not any(claim_hits.values()) and not truthy_claims,
            {
                "map_command": mapped_record["command"],
                "map_ok": mapped.get("ok"),
                "company": mapped.get("company"),
                "people_seen": [
                    {"id": item.get("id"), "name": item.get("name"), "company": item.get("company"),
                     "humanApproved": item.get("humanApproved"), "pathType": item.get("pathType"),
                     "reachable": item.get("reachable")}
                    for item in mapped_people
                ],
                "research_seen": [{"id": item.get("id"), "subjectName": item.get("subjectName"),
                                   "notes": item.get("notes")} for item in mapped_research],
                "contact_matched": seen_contact,
                "research_matched": seen_research,
                "top_level_reachable_flag": mapped.get("reachable"),
                "jobsss_message": mapped.get("message"),
                "authority_claims_found": [word for word, found in claim_hits.items() if found],
                "truthy_authority_keys": truthy_claims,
            },
        )

        # ---- J10: no authority-bearing tool was ever called -----------------
        called = [call["tool"] for call in session.calls]
        unauthorized = [name for name in called if name not in ALLOWED_JOBSSS_TOOLS]
        forbidden = [name for name in called if name in FORBIDDEN_JOBSSS_TOOLS]
        result.check(
            "J10",
            not unauthorized and not forbidden
            and "decide" not in json.dumps(session.cli_invocations)
            and all(segment not in " ".join(called)
                    for segment in ("send", "draft", "approve", "decide", "publish")),
            {
                "jobsss_calls_made": called,
                "allowed_tool_set": sorted(ALLOWED_JOBSSS_TOOLS),
                "unauthorized_calls": unauthorized,
                "forbidden_authority_calls": forbidden,
                "jobsss_cli_invocations": session.cli_invocations,
                "human_decision_tools_never_called": True,
            },
        )
    finally:
        if os.path.exists(store_dir):
            os.chmod(store_dir, 0o700)
            shutil.rmtree(store_dir, ignore_errors=True)
        cleanup_done = not os.path.exists(store_dir)

    # ---- J11: the temporary store is gone, even after failures ------------
    result.check(
        "J11",
        cleanup_done and not os.path.exists(store_dir),
        {
            "temporary_store": store_dir,
            "store_exists_after_check": os.path.exists(store_dir),
            "cleanup_path": "try/finally around the whole composition body",
            "contact_id_created": contact_id,
            "research_id_created": research_id,
            "workdir_retained_for_review": workdir,
        },
    )

    result.note(
        fixtures={"resume": RESUME_B, "job": JOB_B, "serp": SERP_B},
        sibling_executable=JOBSSS,
        people_finder_binary=BIN,
        scratch_dir=workdir,
    )


if __name__ == "__main__":
    sys.exit(run_check("J", body))
