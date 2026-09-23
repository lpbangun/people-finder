#!/usr/bin/env python3
"""Criterion K — hard keep-outs (binary: any fire forces K = 0).

Command: run with the active Python interpreter.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (CHECK_PATHS, EXA_ONE_PERSON, FIXTURE_PATHS,
                      FROZEN_ASSERTIONS, FROZEN_COMMAND_PATHS, JOB_A, RESUME_A, SERP_A,
                      audit_guard, canonical, claim_token_hits, clean_env,
                      compile_fixture, expect, guard_env, guard_violations, mcp_call,
                      netguard, product_command, rank_fixture, read_json, read_text,
                      require_product, run_check, run_cli, run_cmd, scan_tokens, scratch, source_files,
                      tool_result_json, truthy_forbidden_keys, write_json)

# --- keep-out token sets -----------------------------------------------------
LINKEDIN_KEEPOUT_TOKENS = (
    "scrap", "harvestapi", "harvest api", "apify", "sales navigator", "salesnav",
    "sales_nav", "recruiter lite", "li_at", "jsessionid", "linkedin_cookie",
    "cookies.json", "selenium", "playwright", "puppeteer", "chromium",
    "browser automation", "authenticated session",
)

PROVIDER_KEY_TOKENS = ("exa_api_key", "openai_api_key", "anthropic_api_key",
                       "serpapi_api_key", "apify_token", "harvest_api_key", "pdl_api_key")

ENV_READ_TOKENS = ("os.environ", "os.getenv", "getenv(", "environ.get", "environ[")

VECTOR_TOKENS = ("import numpy", "from numpy", "faiss", "sentence_transformers",
                 "sentence-transformers", "import torch", "from torch", "tensorflow",
                 "openai.embeddings", "embedding_model", "cosine_similarity", "knn",
                 "nearest neighbor", "pgvector", "pinecone", "weaviate", "chromadb",
                 "qdrant", "embedding" )

# The sibling write surface. "humanApproved" is intentionally absent: the product does
# carry that key name in its own output validator (a rejection list), while approval
# itself is asserted at runtime instead.
SIBLING_KEEPOUT_TOKENS = ("jobsss", "plugin_data", "import_contact", "record_research",
                          "auto-import", "auto_import")

AUTHORITY_NAME_RE = re.compile(r"(send|message|connect|auto-import|autoimport|apply|"
                               r"approve|publish|schedule|invite|dm|outreach)", re.I)

NEGATION_OR_FLAG_RE = re.compile(r"(never|not |no |false|disclaim|reject|without)", re.I)

TYPED_ANCHOR_TYPES = {"rare_community", "prior_employer", "school", "program",
                      "function", "target_employer", "location", "skill",
                      "hiring_adjacent_term"}

FORBIDDEN_TRUTHY = {"identity_confirmed", "identity_established", "approved", "human_approved",
                    "humanapproved", "reachable", "contactable", "connected", "connection",
                    "second_degree", "delivered", "sent", "emailed", "contacted"}

OTHER_CHECKS = {
    "D": "tests/benchmark/check_discovery.py",
    "R": "tests/benchmark/check_ranking.py",
    "E": "tests/benchmark/check_exa_import.py",
    "I": "tests/benchmark/check_interfaces.py",
    "J": "tests/benchmark/check_jobsss_composition.py",
}


def parse_cli_inventory():
    """Read the advertised subcommands and options from the product's own help."""
    record = run_cmd(product_command("--help"), env=clean_env())
    text = record["stdout"]
    commands = []
    for line in text.splitlines():
        match = re.match(r"\s*\{([a-z0-9,\-]+)\}", line)
        if match:
            commands.extend(match.group(1).split(","))
    return record, sorted(set(commands))


def help_description_text(text):
    """Help text minus argparse's own boilerplate lines."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("-h"))


def parse_cli_options(command):
    record = run_cmd(product_command(command, "--help"), env=clean_env())
    return record, sorted(set(re.findall(r"--[a-z][a-z0-9\-]*", record["stdout"])))


def body(result):
    require_product()
    workdir = scratch("keepouts")
    net = netguard("keepouts-netguard")
    product_files = source_files("bin", "src")

    # ---- K1: no LinkedIn scraping, cookies, session or third-party actors --
    static_hits = scan_tokens(product_files, LINKEDIN_KEEPOUT_TOKENS)
    help_record, commands = parse_cli_inventory()
    options = []
    for command in commands:
        _record, command_options = parse_cli_options(command)
        options.extend(command_options)
    mcp_listing = mcp_call([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ], env=clean_env())
    mcp_tools = (((mcp_listing["session"]["tools_list"] or {}).get("result") or {}).get("tools") or [])
    runtime_text = " ".join([help_description_text(help_record["stdout"]), json.dumps(mcp_tools),
                             " ".join(options)]).lower()
    runtime_hits = [token for token in LINKEDIN_KEEPOUT_TOKENS if token in runtime_text]
    result.check("K1", not static_hits and not runtime_hits, {
        "product_source_files_scanned": [os.path.relpath(target, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))) for target in product_files],
        "banned_token_hits_in_product_source": static_hits,
        "banned_token_hits_in_cli_help_or_mcp_catalog": runtime_hits,
        "cli_options_advertised": options,
        "mcp_tool_names": [tool["name"] for tool in mcp_tools],
        "note": "ARCHITECTURE.md/BENCHMARK.md name the forbidden vendors in their keep-out text; "
                "they are review documents, not product code, and are not scanned here.",
    })

    # ---- K2: nothing sends or schedules anything ---------------------------
    compile_out = os.path.join(workdir, "out")
    os.makedirs(compile_out, exist_ok=True)
    queries_path = os.path.join(compile_out, "a-queries.json")
    candidates_path = os.path.join(compile_out, "a-candidates.json")
    compile_run = compile_fixture(RESUME_A, JOB_A, queries_path)
    rank_run = rank_fixture(queries_path, SERP_A, candidates_path)
    expect(compile_run["returncode"] == 0, f"compile failed: {compile_run['stderr'][:200]}")
    expect(rank_run["returncode"] == 0, f"rank failed: {rank_run['stderr'][:200]}")
    out_dir_listing = sorted(os.listdir(compile_out))
    command_text = " ".join([help_description_text(help_record["stdout"]),
                             json.dumps(mcp_tools)]).lower()
    authority_hits = sorted(set(match.group(0).lower() for match in AUTHORITY_NAME_RE.finditer(command_text)))
    result.check("K2", not authority_hits and out_dir_listing == ["a-candidates.json", "a-queries.json"], {
        "cli_commands": commands,
        "mcp_tool_names": [tool["name"] for tool in mcp_tools],
        "authority_tokens_in_command_surface": authority_hits,
        "files_written_by_the_product": out_dir_listing,
        "product_operations_are_local_only": True,
    })

    # ---- K3: typed anchors, never opaque similarity -----------------------
    static_vector_hits = scan_tokens(product_files, VECTOR_TOKENS)
    loose_hits = {token: rows for token, rows in static_vector_hits.items() if token == "embedding"}
    hard_hits = {token: rows for token, rows in static_vector_hits.items() if token != "embedding"}
    flagged_lines = [
        row for row in loose_hits.get("embedding", [])
        if not NEGATION_OR_FLAG_RE.search(row.get("text", ""))
    ]
    compiled = read_json(queries_path)
    ranked = read_json(candidates_path)
    plan = compiled["ranking_plan"]
    breakdown_types = sorted({
        row["anchor_type"] for item in ranked["candidates"] + ranked["hiring_adjacent"]
        for row in item["score_breakdown"]
    })
    opaque_fields = sorted({
        key for document in (compiled, ranked)
        for key in json.dumps(document).split('"')[1::2]
        if key.lower() in ("similarity", "cosine", "vector", "embedding", "embeddings")
    })
    result.check(
        "K3",
        not hard_hits and not flagged_lines
        and plan["uses_embeddings"] is False
        and plan["uses_opaque_semantic_similarity"] is False
        and set(breakdown_types).issubset(TYPED_ANCHOR_TYPES)
        and not opaque_fields,
        {
            "vector_or_embedding_library_usage_in_product_source": hard_hits,
            "embedding_word_occurrences": loose_hits.get("embedding", []),
            "embedding_occurrences_without_negation_or_false_flag": flagged_lines,
            "ranking_plan": plan,
            "score_breakdown_anchor_types": breakdown_types,
            "opaque_similarity_fields_in_output": opaque_fields,
            "weights_are_typed_anchor_weights": plan["weights"],
        },
    )

    # ---- K4: no automatic import or approval into the sibling store -------
    sibling_hits = scan_tokens(product_files, SIBLING_KEEPOUT_TOKENS)
    approvals = sorted({item["state"]["approval"] for item in ranked["candidates"] + ranked["hiring_adjacent"]})
    truthy = truthy_forbidden_keys(ranked, FORBIDDEN_TRUTHY)
    result.check(
        "K4",
        not sibling_hits and approvals == ["not_requested"] and not truthy
        and ranked["boundary"]["performs_external_action"] is False
        and ranked["boundary"]["writes_other_tool_state"] is False,
        {
            "sibling_write_surface_references_in_product_source": sibling_hits,
            "candidate_approval_states": approvals,
            "forbidden_truthy_keys": truthy,
            "boundary": ranked["boundary"],
            "state_block_sample": ranked["candidates"][0]["state"] if ranked["candidates"] else None,
        },
    )

    # ---- K5: no PLUGIN_DATA access, with a live canary --------------------
    canary = os.path.join(workdir, "canary-plugin-data")
    os.makedirs(canary, exist_ok=True)
    canary_file = os.path.join(canary, "store.json")
    write_json(canary_file, {"canary": "untouched", "records": 0})
    canary_before = read_text(canary_file)
    guard = audit_guard("keepouts-canary", os.path.realpath(canary))
    canary_env = guard_env(guard, base=clean_env({"PLUGIN_DATA": canary}))
    canary_compile = run_cli("compile", "--resume", RESUME_A, "--job", JOB_A,
                             out=os.path.join(workdir, "canary-queries.json"), env=canary_env)
    canary_rank = run_cli("rank", "--queries", os.path.join(workdir, "canary-queries.json"),
                          "--results", SERP_A, out=os.path.join(workdir, "canary-candidates.json"),
                          env=canary_env)
    canary_import = run_cli("import-exa", "--candidates", os.path.join(workdir, "canary-candidates.json"),
                            "--result", EXA_ONE_PERSON, out=os.path.join(workdir, "canary-imported.json"),
                            env=canary_env)
    canary_mcp = mcp_call([
        {"jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
         "params": {"name": "compile_people_queries",
                    "arguments": {"resume_path": RESUME_A, "job_path": JOB_A}}},
    ], env=canary_env)
    canary_after = read_text(canary_file)
    canary_violations = guard_violations(guard)
    product_outputs = [
        read_text(os.path.join(workdir, "canary-queries.json")),
        read_text(os.path.join(workdir, "canary-candidates.json")),
        json.dumps(tool_result_json(canary_mcp["session"].get("call-1")) or {}),
    ]
    leaked_path = [value for value in [canary, canary_file, "store.json"]
                   if any(value in output for output in product_outputs)]
    result.check(
        "K5",
        canary_before == canary_after
        and canary_violations == []
        and not leaked_path
        and all(run["returncode"] == 0 for run in (canary_compile, canary_rank, canary_import))
        and canary_mcp["returncode"] == 0,
        {
            "canary_plugin_data": canary,
            "canary_file": canary_file,
            "canary_env_values_seen_by_product": {"PLUGIN_DATA": canary},
            "canary_unchanged": canary_before == canary_after,
            "audit_violations": canary_violations,
            "canary_path_or_filename_in_product_output": leaked_path,
            "commands": [canary_compile["command"], canary_rank["command"],
                         canary_import["command"], canary_mcp["command"]],
            "exit_codes": [canary_compile["returncode"], canary_rank["returncode"],
                           canary_import["returncode"], canary_mcp["returncode"]],
        },
    )

    # ---- K6: no relationship claims in any product output -----------------
    imported = read_json(os.path.join(workdir, "canary-imported.json"))
    claim_scan = {name: claim_token_hits(document) for name, document in (
        ("queries", compiled), ("candidates", ranked), ("imported", imported),
    )}
    stamp_pack = next((pack for pack in compiled["packs"] if pack["pack_id"] == "shared_stamp"), None)
    stamp_candidates = [item for item in ranked["candidates"] if "shared_stamp" in item["paths"]]
    stamp_labelled = (
        (stamp_pack is None or (
            stamp_pack.get("proxy_label") == "public_stamp_proxy"
            and stamp_pack.get("graph_edge_claimed") is False))
        and all(any("Public-stamp proxy" in note for note in item["notes"])
                for item in stamp_candidates)
    )
    result.check(
        "K6",
        not any(claim_scan.values()) and stamp_labelled
        and not truthy_forbidden_keys(imported, FORBIDDEN_TRUTHY),
        {
            "claim_token_hits": claim_scan,
            "shared_stamp_pack_proxy_label": (stamp_pack or {}).get("proxy_label"),
            "shared_stamp_graph_edge_claimed": (stamp_pack or {}).get("graph_edge_claimed"),
            "shared_stamp_candidates": [
                {"candidate_id": item["candidate_id"], "notes": item["notes"]}
                for item in stamp_candidates
            ],
            "forbidden_truthy_keys_in_imported_output": truthy_forbidden_keys(imported, FORBIDDEN_TRUTHY),
        },
    )

    # ---- K7: no key required anywhere in the core paths ------------------
    empty_env = {key: value for key, value in clean_env().items() if key in ("PATH", "HOME", "LANG", "LC_ALL")}
    static_key_hits = scan_tokens(product_files, PROVIDER_KEY_TOKENS)
    env_read_hits = scan_tokens(product_files, ENV_READ_TOKENS)
    bare_compile = run_cmd(product_command("compile", "--resume", RESUME_A, "--job", JOB_A,
                                           "--out", os.path.join(workdir, "bare-queries.json")),
                            env=empty_env)
    bare_rank = run_cmd(product_command("rank", "--queries", os.path.join(workdir, "bare-queries.json"),
                                        "--results", SERP_A, "--out",
                                        os.path.join(workdir, "bare-candidates.json")), env=empty_env)
    bare_import = run_cmd(product_command("import-exa", "--candidates",
                                          os.path.join(workdir, "bare-candidates.json"),
                                          "--result", EXA_ONE_PERSON, "--out",
                                          os.path.join(workdir, "bare-imported.json")), env=empty_env)
    bare_mcp = mcp_call([
        {"jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
         "params": {"name": "compile_people_queries",
                    "arguments": {"resume_path": RESUME_A, "job_path": JOB_A}}},
    ], env=empty_env)
    identical = (
        canonical(read_json(os.path.join(workdir, "bare-candidates.json")))
        == canonical(read_json(candidates_path))
    )
    result.check(
        "K7",
        not static_key_hits and not env_read_hits
        and all(run["returncode"] == 0 for run in (bare_compile, bare_rank, bare_import))
        and bare_mcp["returncode"] == 0
        and not [key for key in empty_env if key not in ("PATH", "HOME", "LANG", "LC_ALL")],
        {
            "environment_given_to_product": sorted(empty_env),
            "credential_like_names_present_in_process_environment": [
                key for key in os.environ if key.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))
            ],
            "commands": [bare_compile["command"], bare_rank["command"], bare_import["command"],
                         bare_mcp["command"]],
            "exit_codes": [bare_compile["returncode"], bare_rank["returncode"],
                           bare_import["returncode"], bare_mcp["returncode"]],
            "rank_output_identical_to_full_environment_run": identical,
            "provider_key_names_in_product_source": static_key_hits,
            "environment_reads_in_product_source": env_read_hits,
        },
    )

    # ---- K8: the five other benchmark commands stay offline ---------------
    offline_rows = {}
    offline_documents = {}
    all_offline = True
    for criterion, relative in OTHER_CHECKS.items():
        target = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), relative)
        run = run_cmd([sys.executable, target], env=guard_env(net, base=clean_env()), timeout=600)
        document = run["json"] or {}
        violations = guard_violations(net)
        optional_skipped = (
            criterion == "J"
            and document.get("status") == "optional/skipped"
            and document.get("optional") is True
            and document.get("passed") is None
            and bool(document.get("skip_reason"))
            and not document.get("assertions")
        )
        row = {
            "command": run["command"],
            "exit_code": run["returncode"],
            "criterion": document.get("criterion"),
            "offline": document.get("offline"),
            "passed": document.get("passed"),
            "status": document.get("status", "passed" if document.get("passed") is True else "failed"),
            "optional_skipped": optional_skipped,
            "network_attempts": violations,
            "stdout_is_single_json_object": run["json"] is not None,
        }
        offline_rows[criterion] = row
        offline_documents[criterion] = document
        checks_offline_or_optional = document.get("passed") is True or optional_skipped
        all_offline = all_offline and row["criterion"] == criterion and row["offline"] is True \
            and row["stdout_is_single_json_object"] and run["returncode"] == 0 \
            and checks_offline_or_optional and not violations
    result.check("K8", all_offline, {
        "netguard": "PYTHONPATH sitecustomize denies socket creation in every check and its subprocesses",
        "commands": offline_rows,
        "all_checks_offline_or_optional_skip": all_offline,
        "optional_skip_did_not_claim_a_J_success": (
            not offline_rows.get("J", {}).get("optional_skipped")
            or offline_rows.get("J", {}).get("passed") is None
        ),
        "live_network_scored_as_zero": True,
    })

    # ---- K9: no authority-bearing command exists --------------------------
    inventory_hits = {name: sorted(set(match.group(0).lower()
                                      for match in AUTHORITY_NAME_RE.finditer(name)))
                      for name in commands}
    inventory_hits = {name: hits for name, hits in inventory_hits.items() if hits}
    description_hits = sorted(set(match.group(0).lower()
                                  for match in AUTHORITY_NAME_RE.finditer(
                                      help_description_text(help_record["stdout"]))))
    tool_name_hits = [tool for tool in mcp_tools if AUTHORITY_NAME_RE.search(tool["name"])]
    tool_description_hits = [tool["name"] for tool in mcp_tools if AUTHORITY_NAME_RE.search(tool["description"])]
    result.check(
        "K9",
        commands == sorted(["compile", "rank", "import-exa", "validate", "mcp"])
        and not inventory_hits and not description_hits and not tool_name_hits
        and not tool_description_hits,
        {
            "cli_commands": commands,
            "cli_help_command_names_with_authority_tokens": inventory_hits,
            "cli_help_authority_tokens": description_hits,
            "mcp_tools": [tool["name"] for tool in mcp_tools],
            "mcp_tools_with_authority_tokens_in_name": tool_name_hits,
            "mcp_tools_with_authority_tokens_in_description": tool_description_hits,
            "cli_options_advertised": options,
            "help_excerpt": help_record["stdout"][:400],
        },
    )

    # ---- K10: the frozen benchmark inventory is intact --------------------
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    missing_checks = [relative for relative in CHECK_PATHS if not os.path.isfile(os.path.join(repo, relative))]
    missing_fixtures = [relative for relative in FIXTURE_PATHS if not os.path.isfile(os.path.join(repo, relative))]
    benchmark_text = read_text("BENCHMARK.md")
    score_text = read_text("SCORE.md")
    missing_assertion_ids = {
        criterion: [assertion_id for assertion_id in assertion_ids
                    if f"`{assertion_id}`" not in benchmark_text]
        for criterion, assertion_ids in FROZEN_ASSERTIONS.items()
    }
    missing_assertion_ids = {key: value for key, value in missing_assertion_ids.items() if value}
    missing_commands = [
        relative for relative in FROZEN_COMMAND_PATHS
        if not re.search(rf"\bpython(?:3)?\s+{re.escape(relative)}\b", benchmark_text)
    ]
    checks_missing_ids = {}
    for criterion, relative in OTHER_CHECKS.items():
        text = read_text(relative)
        absent = [assertion_id for assertion_id in FROZEN_ASSERTIONS[criterion]
                  if f'"{assertion_id}"' not in text]
        if absent:
            checks_missing_ids[criterion] = absent
    runs_missing_ids = {}
    optional_skipped_runs = []
    invalid_optional_skips = []
    for criterion, run_document in offline_documents.items():
        if criterion == "J" and run_document.get("status") == "optional/skipped":
            valid_skip = (
                run_document.get("optional") is True
                and run_document.get("passed") is None
                and bool(run_document.get("skip_reason"))
                and not run_document.get("assertions")
            )
            if valid_skip:
                optional_skipped_runs.append(criterion)
            else:
                invalid_optional_skips.append(criterion)
            continue
        emitted = {assertion["id"] for assertion in run_document.get("assertions", [])}
        absent = [assertion_id for assertion_id in FROZEN_ASSERTIONS[criterion]
                  if assertion_id not in emitted]
        if absent:
            runs_missing_ids[criterion] = absent
    result.check(
        "K10",
        not missing_checks and not missing_fixtures and not missing_assertion_ids
        and not missing_commands and not checks_missing_ids and not runs_missing_ids
        and not invalid_optional_skips,
        {
            "frozen_check_paths_present": len(CHECK_PATHS) - len(missing_checks),
            "frozen_fixture_paths_present": len(FIXTURE_PATHS) - len(missing_fixtures),
            "missing_check_paths": missing_checks,
            "missing_fixture_paths": missing_fixtures,
            "assertion_ids_absent_from_BENCHMARK_md": missing_assertion_ids,
            "frozen_commands_absent_from_BENCHMARK_md": missing_commands,
            "assertion_ids_absent_from_check_sources": checks_missing_ids,
            "assertion_ids_absent_from_check_output": runs_missing_ids,
            "explicit_optional_skips": optional_skipped_runs,
            "invalid_optional_skips": invalid_optional_skips,
            "benchmark_sha256": __import__("hashlib").sha256(benchmark_text.encode()).hexdigest(),
            "score_sha256": __import__("hashlib").sha256(score_text.encode()).hexdigest(),
            "converge_rule_present_in_SCORE_md": "D ≥ 9.0" in score_text and "is not an average" in score_text,
        },
    )

    result.note(
        scratch_dir=workdir,
        fixtures=list(FIXTURE_PATHS),
        product_source_files=len(product_files),
    )


if __name__ == "__main__":
    sys.exit(run_check("K", body))
