#!/usr/bin/env python3
"""Criterion I — CLI and MCP parity on supplied results.

Command: python3 tests/benchmark/check_interfaces.py
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (BIN, JOB_A, RESUME_A, SERP_A, canonical, clean_env,
                      compile_fixture, expect, guard_env, guard_violations,
                      mcp_call, netguard, rank_fixture, read_json, read_text,
                      require_product, run_check, run_cli, run_cmd, scan_tokens,
                      scratch, source_files, tool_result_json)

FIXED_AT = "2026-09-10T08:00:00Z"
TOOL_NAMES = {"compile_people_queries", "rank_people_candidates", "start_people_research"}
NETWORK_CLIENT_TOKENS = ("urllib.request", "requests.", "http.client", "httpx",
                         "socket.socket", "aiohttp", "urlopen")
KEY_PROPERTY_RE = re.compile(r"(api[_-]?key|token|secret|password|credential|bearer)", re.I)


def candidate_columns(document):
    return (
        [(item["public_url"], item["name"], item["score"], item["paths"], item["unknowns"])
         for item in document["candidates"]],
        [(item["public_url"], item["name"], item["score"], item["paths"], item["unknowns"])
         for item in document["hiring_adjacent"]],
    )


def body(result):
    require_product()
    workdir = scratch("interfaces")
    net = netguard("interfaces-netguard")

    # ---- I1: CLI compile under a credential-free environment --------------
    cli_queries = os.path.join(workdir, "cli-queries.json")
    compile_run = compile_fixture(RESUME_A, JOB_A, cli_queries, at=FIXED_AT)
    expect(compile_run["returncode"] == 0, f"CLI compile failed: {compile_run['stderr'][:300]}")
    compiled = read_json(cli_queries)
    result.check(
        "I1",
        compile_run["returncode"] == 0
        and compiled["schema"] == "people-queries.v1"
        and compiled["packs"]
        and compiled["anchors"]
        and not any(key in clean_env() for key in ("EXA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")),
        {
            "command": compile_run["command"],
            "exit_code": compile_run["returncode"],
            "fixtures": {"resume": RESUME_A, "job": JOB_A},
            "schema": compiled["schema"],
            "pack_count": len(compiled["packs"]),
            "anchor_count": len(compiled["anchors"]),
            "environment_policy": "all credential-like variables removed (see K7/E6 for the itemised list)",
            "output_path": cli_queries,
        },
    )

    # ---- I2: CLI rank consumes only supplied JSON -------------------------
    cli_candidates = os.path.join(workdir, "cli-candidates.json")
    rank_run = rank_fixture(cli_queries, SERP_A, cli_candidates, at=FIXED_AT)
    expect(rank_run["returncode"] == 0, f"CLI rank failed: {rank_run['stderr'][:300]}")
    ranked = read_json(cli_candidates)
    validate_run = run_cmd([BIN, "validate", cli_candidates], env=clean_env())
    result.check(
        "I2",
        rank_run["returncode"] == 0
        and ranked["schema"] == "people-candidates.v1"
        and (validate_run["json"] or {}).get("ok") is True,
        {
            "command": rank_run["command"],
            "exit_code": rank_run["returncode"],
            "fixtures": {"queries": cli_queries, "results": SERP_A},
            "validate_command": validate_run["command"],
            "validate_result": validate_run["json"],
            "counts": ranked["counts"],
            "output_path": cli_candidates,
        },
    )

    # ---- I3: MCP initialize and tools/list over stdio ---------------------
    listing = mcp_call([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "benchmark", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ], env=clean_env())
    tools = ((listing["session"]["tools_list"] or {}).get("result") or {}).get("tools") or []
    tool_names = [tool["name"] for tool in tools]
    init_result = ((listing["session"]["initialize"] or {}).get("result") or {})
    result.check(
        "I3",
        listing["returncode"] == 0
        and bool(init_result)
        and TOOL_NAMES.issubset(set(tool_names))
        and not listing["invalid_stdout_lines"],
        {
            "command": listing["command"],
            "exit_code": listing["returncode"],
            "initialize_result": init_result,
            "advertised_tools": tool_names,
            "required_tools_present": sorted(TOOL_NAMES.intersection(tool_names)),
            "tool_input_schemas": {tool["name"]: tool["inputSchema"] for tool in tools},
        },
    )

    # ---- I4: MCP compile accepts supplied content without keys ------------
    resume_text = read_text(RESUME_A)
    job_card = read_json(JOB_A)
    inline = mcp_call([
        {"jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
         "params": {"name": "compile_people_queries",
                    "arguments": {"resume_text": resume_text, "job": job_card,
                                  "generated_at": FIXED_AT}}},
    ], env=clean_env())
    inline_compiled = tool_result_json(inline["session"].get("call-1")) or {}
    compile_schema = next((tool["inputSchema"] for tool in tools
                           if tool["name"] == "compile_people_queries"), {})
    compile_key_props = [name for name in (compile_schema.get("properties") or {})
                         if KEY_PROPERTY_RE.search(name)]
    result.check(
        "I4",
        inline["returncode"] == 0
        and inline_compiled.get("schema") == "people-queries.v1"
        and inline_compiled.get("packs")
        and not compile_key_props
        and not compile_schema.get("required"),
        {
            "command": inline["command"],
            "exit_code": inline["returncode"],
            "arguments_supplied": ["resume_text (supplied content)", "job (supplied object)", "generated_at"],
            "returned_schema": inline_compiled.get("schema"),
            "pack_ids": [pack["pack_id"] for pack in inline_compiled.get("packs", [])],
            "input_schema_required": compile_schema.get("required"),
            "credential_like_input_properties": compile_key_props,
            "is_error": (inline["session"].get("call-1") or {}).get("result", {}).get("isError"),
        },
    )

    # ---- I5: MCP rank accepts supplied results without a backend ----------
    supplied_results = read_json(SERP_A)
    inline_rank = mcp_call([
        {"jsonrpc": "2.0", "id": "call-2", "method": "tools/call",
         "params": {"name": "rank_people_candidates",
                    "arguments": {"queries": inline_compiled, "results": supplied_results,
                                  "generated_at": FIXED_AT}}},
    ], env=clean_env())
    inline_ranked = tool_result_json(inline_rank["session"].get("call-2")) or {}
    rank_schema = next((tool["inputSchema"] for tool in tools
                        if tool["name"] == "rank_people_candidates"), {})
    rank_key_props = [name for name in (rank_schema.get("properties") or {})
                      if KEY_PROPERTY_RE.search(name)]
    result.check(
        "I5",
        inline_rank["returncode"] == 0
        and inline_ranked.get("schema") == "people-candidates.v1"
        and inline_ranked.get("candidates") is not None
        and not rank_key_props
        and not rank_schema.get("required"),
        {
            "command": inline_rank["command"],
            "exit_code": inline_rank["returncode"],
            "arguments_supplied": ["queries (supplied object)", "results (supplied object)", "generated_at"],
            "returned_schema": inline_ranked.get("schema"),
            "counts": inline_ranked.get("counts"),
            "input_schema_required": rank_schema.get("required"),
            "credential_like_input_properties": rank_key_props,
            "network_backend_properties": [name for name in (rank_schema.get("properties") or {})
                                           if "backend" in name or "url" in name.lower()],
        },
    )

    # ---- I6: CLI and MCP compile agreement -------------------------------
    parity = mcp_call([
        {"jsonrpc": "2.0", "id": "call-3", "method": "tools/call",
         "params": {"name": "compile_people_queries",
                    "arguments": {"resume_path": RESUME_A, "job_path": JOB_A,
                                  "generated_at": FIXED_AT}}},
    ], env=clean_env())
    parity_compiled = tool_result_json(parity["session"].get("call-3")) or {}
    exact = canonical(parity_compiled) == canonical(compiled)
    def without_source_labels(document):
        """Drop only the supplied-content source labels (transport metadata)."""
        copy = json.loads(json.dumps(document))
        copy.get("seeker", {}).pop("source_resume", None)
        copy.get("target", {}).pop("job_source", None)
        for anchor in copy.get("anchors", []):
            anchor.get("evidence", {}).pop("source", None)
        return copy

    normalised_inline = without_source_labels(inline_compiled)
    normalised_cli = without_source_labels(compiled)
    inline_semantics = {
        "anchors": normalised_inline.get("anchors") == normalised_cli.get("anchors"),
        "packs": normalised_inline.get("packs") == normalised_cli.get("packs"),
        "target": normalised_inline.get("target") == normalised_cli.get("target"),
        "unknowns": normalised_inline.get("unknowns") == normalised_cli.get("unknowns"),
        "seeker": normalised_inline.get("seeker") == normalised_cli.get("seeker"),
    }
    result.check(
        "I6",
        exact and all(inline_semantics.values()),
        {
            "cli_command": compile_run["command"],
            "mcp_command": parity["command"],
            "mcp_arguments": {"resume_path": RESUME_A, "job_path": JOB_A, "generated_at": FIXED_AT},
            "documents_byte_identical": exact,
            "inline_content_route_semantics": inline_semantics,
            "inline_route_transport_metadata_normalised": [
                "seeker.source_resume", "target.job_source", "anchors[].evidence.source",
            ],
            "compiled_document_bytes": len(canonical(compiled)),
            "mcp_document_bytes": len(canonical(parity_compiled)),
        },
    )

    # ---- I7: CLI and MCP rank agreement ----------------------------------
    rank_parity = mcp_call([
        {"jsonrpc": "2.0", "id": "call-3", "method": "tools/call",
         "params": {"name": "rank_people_candidates",
                    "arguments": {"queries_path": cli_queries, "results_path": SERP_A,
                                  "generated_at": FIXED_AT}}},
    ], env=clean_env())
    rank_parity_doc = tool_result_json(rank_parity["session"].get("call-3")) or {}
    result.check(
        "I7",
        canonical(rank_parity_doc) == canonical(ranked)
        and candidate_columns(rank_parity_doc) == candidate_columns(ranked)
        and candidate_columns(inline_ranked) == candidate_columns(ranked),
        {
            "cli_command": rank_run["command"],
            "mcp_command": rank_parity["command"],
            "mcp_arguments": {"queries_path": cli_queries, "results_path": SERP_A,
                              "generated_at": FIXED_AT},
            "documents_byte_identical": canonical(rank_parity_doc) == canonical(ranked),
            "inline_route_same_candidates_order_paths_unknowns":
                candidate_columns(inline_ranked) == candidate_columns(ranked),
            "peer_order": [(item["public_url"], item["score"], item["paths"])
                           for item in ranked["candidates"]],
        },
    )

    # ---- I8: network is denied and never attempted ------------------------
    guarded_env = guard_env(net)
    guarded_rank = rank_fixture(cli_queries, SERP_A,
                                os.path.join(workdir, "guarded-candidates.json"),
                                env=guarded_env, at=FIXED_AT)
    guarded_compile_run = run_cli("compile", "--resume", RESUME_A, "--job", JOB_A,
                                  "--at", FIXED_AT, out=os.path.join(workdir, "guarded-queries.json"),
                                  env=guarded_env)
    guarded_mcp = mcp_call([
        {"jsonrpc": "2.0", "id": "call-3", "method": "tools/call",
         "params": {"name": "compile_people_queries",
                    "arguments": {"resume_path": RESUME_A, "job_path": JOB_A,
                                  "generated_at": FIXED_AT}}},
    ], env=guarded_env)
    guarded_compiled_doc = tool_result_json(guarded_mcp["session"].get("call-3")) or {}
    guarded_rank_doc = read_json(os.path.join(workdir, "guarded-candidates.json"))
    network_scan = scan_tokens(source_files("bin", "src"), NETWORK_CLIENT_TOKENS)
    violations = guard_violations(net)
    result.check(
        "I8",
        guarded_compile_run["returncode"] == 0
        and guarded_rank["returncode"] == 0
        and guarded_mcp["returncode"] == 0
        and not violations
        and not network_scan
        and canonical(guarded_rank_doc) == canonical(ranked)
        and canonical(guarded_compiled_doc) == canonical(compiled),
        {
            "netguard": "sitecustomize replaces socket.socket/create_connection/getaddrinfo with a denying call",
            "guarded_cli_compile": {"command": guarded_compile_run["command"],
                                    "exit_code": guarded_compile_run["returncode"]},
            "guarded_cli_rank": {"command": guarded_rank["command"],
                                 "exit_code": guarded_rank["returncode"]},
            "guarded_mcp_compile": {"command": guarded_mcp["command"],
                                    "exit_code": guarded_mcp["returncode"]},
            "network_attempts_logged": violations,
            "network_clients_in_product_source": network_scan,
            "guarded_outputs_identical_to_unguarded": {
                "compile": canonical(guarded_compiled_doc) == canonical(compiled),
                "rank": canonical(guarded_rank_doc) == canonical(ranked),
            },
            "product_source_files_scanned": len(source_files("bin", "src")),
        },
    )

    # ---- I9: stdout is protocol-clean ------------------------------------
    stdout_lines = [line for line in listing["stdout"].splitlines() if line.strip()]
    parsed_lines = []
    for line in stdout_lines:
        try:
            parsed_lines.append(json.loads(line))
        except ValueError:
            parsed_lines.append(None)
    protocol_clean = all(
        isinstance(item, dict) and item.get("jsonrpc") == "2.0" for item in parsed_lines
    ) and not listing["invalid_stdout_lines"]
    noise_markers = [marker for marker in ("usage:", "Traceback", "[harness]", "error:")
                     if marker in listing["stdout"]]
    result.check(
        "I9",
        protocol_clean and not noise_markers and listing["stdout_line_count"] == 2,
        {
            "command": listing["command"],
            "requests_sent": 2,
            "stdout_line_count": listing["stdout_line_count"],
            "every_stdout_line_is_json_rpc": protocol_clean,
            "non_json_stdout_lines": listing["invalid_stdout_lines"],
            "non_protocol_markers_in_stdout": noise_markers,
            "stderr_diagnostics_bytes": len(listing["stderr"]),
            "stderr_excerpt": listing["stderr"][:200],
            "first_response_id": parsed_lines[0].get("id") if parsed_lines and parsed_lines[0] else None,
        },
    )

    result.note(
        fixtures={"resume": RESUME_A, "job": JOB_A, "serp": SERP_A},
        scratch_dir=workdir,
        mcp_command=f"{BIN} mcp",
    )


if __name__ == "__main__":
    sys.exit(run_check("I", body))
