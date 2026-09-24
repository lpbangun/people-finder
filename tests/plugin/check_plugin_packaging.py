#!/usr/bin/env python3
"""Plugin packaging check — Agent Plugins 1.0.0 conformance and offline install smoke.

Command: python3 tests/plugin/check_plugin_packaging.py

This check is additive and independent of the offline benchmark
(BENCHMARK.md) and the separately configured live journey
(tests/e2e/check_profile_jobs_people.py). No E2E_BENCHMARK.md contract is shipped.

What it proves, using only the Python standard library and the product's public
CLI/MCP surfaces:

  P1  plugin.json conforms to the Agent Plugins 1.0.0 manifest rules
  P2  mcp.json conforms to the Agent Plugins 1.0.0 stdio server rules
  P3  the package carries no credential, remote transport, host-state
      dependency or absolute user path
  P4  the declared launcher resolves inside the plugin root and runs through the active Python interpreter
  P5  the declared command + args serve exactly the documented MCP surface
  P6  the installed skill, the MCP catalogue and the CLI inventory agree
  P7  real offline calls over the declared server on labelled fixtures
  P8  miss semantics: shortfalls and suppress reasons, never an invented person
  P9  no generic search capability and no authority-bearing tool or command
  P10 the host-provided PLUGIN_DATA is neither declared nor touched
  P11 a relocated install serves identical results, and uninstall removes the
      tools without deleting user data
  P12 deterministic repetition and an honest, portable compatibility matrix

Every fixture used here is fictional and is labelled as such: no assertion in
this file is a live-discovery claim.
"""

import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "benchmark"))

from _harness import (BIN, JOB_A, JOB_C, RESUME_A, RESUME_C, SERP_A, SERP_C, clean_env,
                      canonical, guard_env, guard_violations, netguard, read_json,
                      read_text, run_cmd, scratch, sha256_file, expect)

BENCHMARK = "people-finder-plugin-packaging-v1"
FIXED_AT = "2026-09-20T00:00:00Z"

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
MANIFEST_NAME = "people-finder"
SERVER_NAME = "people-finder"
DECLARED_COMMAND = "./bin/people-finder"
DECLARED_ARGS = ["mcp"]

MANIFEST_KEYS = {"$schema", "name", "version", "description", "author", "homepage",
                 "repository", "license", "keywords", "extensions"}
MANIFEST_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
STDIO_KEYS = {"type", "command", "args", "env", "cwd"}
EXPECTED_TOOLS = {"compile_people_queries", "rank_people_candidates", "start_people_research"}
EXPECTED_TOOL_PROPS = {
    "compile_people_queries": {"resume_text", "resume_path", "job", "job_path",
                               "resume_source", "job_source", "generated_at"},
    "rank_people_candidates": {"queries", "queries_path", "results", "results_path",
                              "generated_at"},
    "start_people_research": {"resume_text", "resume_path", "job", "job_path",
                              "resume_source", "job_source", "generated_at",
                              "subject", "notes"},
}
EXPECTED_CLI_COMMANDS = {"compile", "import-exa", "mcp", "rank", "validate"}

# A free-text search dispatch or an authority surface would have to appear as a
# discrete property/command/tool token; these patterns catch that without
# flagging the plural document inputs (`queries`, `results`).
GENERIC_SEARCH_PROP_RE = re.compile(
    r"^(query|q|term|keyword|keywords|search|search_query|engine|backend|provider|"
    r"endpoint|url|host|api_key|apikey|token|secret|limit|max|max_results|count|"
    r"send|message|dm|apply|application|browser|social|email|address)$", re.I)
AUTHORITY_TOKEN_RE = re.compile(r"(send|message|connect|auto-import|autoimport|apply|"
                                r"approve|publish|schedule|invite|dm|outreach)", re.I)
LINKEDIN_ACTOR_TOKENS = ("harvestapi", "harvest api", "apify", "sales navigator", "salesnav",
                         "sales_nav", "recruiter lite", "li_at", "jsessionid",
                         "linkedin_cookie", "cookies.json", "selenium", "playwright",
                         "puppeteer", "chromium", "browser automation",
                         "authenticated session")
CREDENTIAL_VALUE_RE = re.compile(r"(api[_-]?key|apikey|\btoken\b|secret|password|bearer|"
                                 r"authorization|cookie|session_token)", re.I)
ABSOLUTE_PATH_RE = re.compile(r"(/home/|/Users/|/root/|[A-Za-z]:\\\\|[A-Za-z]:\\\\)")

PACKAGING_FILES = (
    "plugin.json",
    "mcp.json",
    "compat/README.md",
    "compat/matrix.json",
    "compat/hermes/config.yaml.template",
    "skills/people-finder/SKILL.md",
)


class HarnessError(RuntimeError):
    """Precondition failure -> exit 2."""


class Result:
    def __init__(self):
        self.assertions = []
        self.extra = {}

    def check(self, assertion_id, passed, evidence):
        self.assertions.append({"id": assertion_id, "passed": bool(passed), "evidence": evidence})
        if not passed:
            sys.stderr.write(f"[plugin] {assertion_id} FAILED\n")
            sys.stderr.flush()
        return bool(passed)

    def note(self, **values):
        self.extra.update(values)

    def finish(self, harness_error=None):
        document = {
            "benchmark": BENCHMARK,
            "criterion": "P",
            "offline": True,
            "live": False,
            "fixture": True,
            "class": "fixture",
            "passed": all(item["passed"] for item in self.assertions) and not harness_error,
            "assertions": self.assertions,
        }
        document.update(self.extra)
        if harness_error:
            document["harness_error"] = harness_error
        sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if harness_error:
            return 2
        return 0 if document["passed"] else 1


def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def root_path(relative):
    return os.path.join(plugin_root(), relative)


def speak(argv, requests, *, env=None, cwd=None, timeout=120):
    """One bounded stdio JSON-RPC session against an explicitly chosen executable."""
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    record = run_cmd([*argv], env=env if env is not None else clean_env(),
                     cwd=cwd, timeout=timeout, stdin_text=payload)
    lines = [line for line in record["stdout"].splitlines() if line.strip()]
    responses, invalid = [], []
    for line in lines:
        try:
            responses.append(json.loads(line))
        except ValueError:
            invalid.append(line[:200])
    session = {}
    for response in responses:
        if isinstance(response, dict) and "id" in response:
            session[str(response["id"])] = response
    record["responses"] = responses
    record["invalid_stdout_lines"] = invalid
    record["session"] = session
    return record


def tool_payload(response):
    try:
        return json.loads(response["result"]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def listing_requests():
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "plugin-packaging-check", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]


def tools_from(record):
    return ((record["session"].get("2") or {}).get("result") or {}).get("tools") or []


def tree_state(directory):
    """Relative path -> sha256 (files) or 'dir' (directories), or {} if absent."""
    rows = {}
    if not os.path.isdir(directory):
        return rows
    for current, dirs, files in os.walk(directory):
        for name in sorted(dirs):
            rows[os.path.relpath(os.path.join(current, name), directory) + "/"] = "dir"
        for name in sorted(files):
            full = os.path.join(current, name)
            rows[os.path.relpath(full, directory)] = sha256_file(full)
    return rows


def cli_commands():
    record = run_cmd([BIN, "--help"], env=clean_env())
    names = []
    for line in record["stdout"].splitlines():
        match = re.match(r"\s*\{([a-z0-9,\-]+)\}", line)
        if match:
            names.extend(match.group(1).split(","))
    return record, sorted(set(names))


def compile_arguments():
    """The same supplied inputs for every in-place and relocated run."""
    return {
        "resume_text": read_text(RESUME_A),
        "job": read_json(JOB_A),
        "resume_source": RESUME_A,
        "job_source": JOB_A,
        "generated_at": FIXED_AT,
    }


def rank_arguments(compiled):
    return {"queries": compiled, "results": read_json(SERP_A), "generated_at": FIXED_AT}


def body(result):
    root = plugin_root()
    workdir = scratch("plugin-packaging")
    net = netguard("plugin-packaging-netguard")

    manifest = read_json(root_path("plugin.json"))
    mcp_config = read_json(root_path("mcp.json"))
    matrix = read_json(root_path("compat/matrix.json"))
    skill_text = read_text(root_path("skills/people-finder/SKILL.md"))
    adapter_template = read_text(root_path("compat/hermes/config.yaml.template"))
    inventory = {relative: sha256_file(root_path(relative)) for relative in PACKAGING_FILES}

    # ---- P1: Agent Plugins 1.0.0 manifest conformance ----------------------
    unknown_manifest_keys = sorted(set(manifest) - MANIFEST_KEYS)
    author_ok = (isinstance(manifest.get("author"), dict)
                 and set(manifest["author"]).issubset({"name", "email", "url"})
                 and all(isinstance(value, str) for value in manifest["author"].values())
                 ) if "author" in manifest else True
    types_ok = (
        isinstance(manifest.get("name"), str)
        and isinstance(manifest.get("version", ""), str)
        and isinstance(manifest.get("description", ""), str)
        and isinstance(manifest.get("license", ""), str)
        and isinstance(manifest.get("keywords", []), list)
        and all(isinstance(item, str) for item in manifest.get("keywords", []))
    )
    result.check(
        "P1",
        manifest.get("$schema") == PLUGIN_SCHEMA
        and manifest.get("name") == MANIFEST_NAME
        and bool(MANIFEST_NAME_RE.match(manifest.get("name", "")))
        and len(manifest.get("name", "")) <= 64
        and not unknown_manifest_keys
        and types_ok
        and author_ok
        and "plugin" in manifest.get("description", "").lower(),
        {
            "file": "plugin.json",
            "sha256": inventory["plugin.json"],
            "schema_field": manifest.get("$schema"),
            "expected_schema": PLUGIN_SCHEMA,
            "name": manifest.get("name"),
            "name_matches_spec_pattern": bool(MANIFEST_NAME_RE.match(manifest.get("name", ""))),
            "declared_fixed_locations_in_manifest": sorted(
                key for key in manifest if key in ("skills", "mcpServers", "tools")),
            "unknown_manifest_keys": unknown_manifest_keys,
            "metadata_types_ok": types_ok,
            "author_object_ok": author_ok,
            "keywords": manifest.get("keywords"),
            "license": manifest.get("license"),
        },
    )

    # ---- P2: Agent Plugins 1.0.0 MCP stdio registration -------------------
    servers = mcp_config.get("mcpServers") or {}
    entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    extra_top_level = sorted(set(mcp_config) - {"$schema", "mcpServers"})
    env_block = entry.get("env") or {}
    placeholder_keys = sorted(key for key in env_block if key in ("PLUGIN_ROOT", "PLUGIN_DATA"))
    args_placeholders = [arg for arg in entry.get("args", []) if "${" in str(arg)]
    cwd_ok = ("cwd" not in entry) or bool(re.match(
        r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))", str(entry.get("cwd", ""))))
    result.check(
        "P2",
        mcp_config.get("$schema") == MCP_SCHEMA
        and not extra_top_level
        and isinstance(servers, dict) and list(servers) == [SERVER_NAME]
        and set(entry) <= STDIO_KEYS
        and entry.get("type") == "stdio"
        and entry.get("command") == DECLARED_COMMAND
        and entry.get("args") == DECLARED_ARGS
        and not placeholder_keys
        and not args_placeholders
        and cwd_ok
        and "url" not in entry and "headers" not in entry,
        {
            "file": "mcp.json",
            "sha256": inventory["mcp.json"],
            "schema_field": mcp_config.get("$schema"),
            "extra_top_level_fields": extra_top_level,
            "server_names": list(servers) if isinstance(servers, dict) else servers,
            "server_entry": entry,
            "declared_command_is_plugin_relative": str(entry.get("command", "")).startswith("./"),
            "no_placeholder_expansion_in_command": "${" not in str(entry.get("command", "")),
            "placeholder_arguments_present": args_placeholders,
            "env_placeholder_keys_present": placeholder_keys,
            "cwd_form_ok": cwd_ok,
            "remote_transport_declared": any(key in entry for key in ("url", "headers")),
        },
    )

    # ---- P3: no credentials, remote transport, host state or absolute paths --
    scanned = {relative: read_text(root_path(relative)) for relative in PACKAGING_FILES}
    credential_hits = {relative: sorted(set(match.group(0).lower()
                                            for match in CREDENTIAL_VALUE_RE.finditer(text)))
                       for relative, text in scanned.items()
                       if relative not in ("skills/people-finder/SKILL.md",)}
    credential_hits = {key: value for key, value in credential_hits.items() if value}
    absolute_hits = {relative: sorted(set(match.group(0) for match in ABSOLUTE_PATH_RE.finditer(text)))
                     for relative, text in scanned.items()}
    absolute_hits = {key: value for key, value in absolute_hits.items() if value}
    # Prose may name a forbidden capability only to forbid it; configuration files
    # may not mention it at all.
    NEGATION_RE = re.compile(r"(never|not |no |without|denies|refuses)", re.I)
    actor_lines = {}
    for relative, text in scanned.items():
        hits = [line.strip() for line in text.splitlines()
                if any(token in line.lower() for token in LINKEDIN_ACTOR_TOKENS)]
        hits = [line for line in hits
                if relative != "skills/people-finder/SKILL.md" or not NEGATION_RE.search(line)]
        if hits:
            actor_lines[relative] = hits
    remote_hits = [relative for relative, text in scanned.items()
                   if re.search(r"\"(url|headers)\"\s*:", text) and relative.endswith(".json")]
    skill_negations = [line.strip() for line in skill_text.splitlines()
                       if "credential" in line.lower() or "api key" in line.lower()]
    result.check(
        "P3",
        not credential_hits and not absolute_hits and not actor_lines and not remote_hits
        and any(line.lower().startswith("- no credentials") for line in skill_negations),
        {
            "scanned_files": sorted(scanned),
            "credential_like_tokens_in_configuration": credential_hits,
            "absolute_user_paths_in_package": absolute_hits,
            "third_party_actor_tokens": actor_lines,
            "remote_transport_fields_in_json": remote_hits,
            "skill_credential_statements": skill_negations,
            "negation_aware_policy": "the skill may name a forbidden capability only inside a "
                                     "negated statement; configuration files may not mention it at all",
        },
    )

    # ---- P4: the declared launcher resolves inside the plugin root ---------
    relative_command = entry.get("command", DECLARED_COMMAND)
    spec_relative = relative_command[2:] if relative_command.startswith("./") else relative_command
    resolved = os.path.realpath(os.path.join(root, spec_relative))
    real_root = os.path.realpath(root)
    contained = resolved == real_root or resolved.startswith(real_root + os.sep)
    expect(contained, "declared command escapes the plugin root")
    expect(os.path.isfile(resolved), f"declared launcher is missing: {resolved}")
    in_root_run = run_cmd([BIN, "--version"], env=clean_env())
    declared_run = speak([resolved, *DECLARED_ARGS], [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "c", "version": "1"}}},
    ], cwd=root)
    declared_init = (declared_run["session"].get("1") or {}).get("result") or {}
    portable_argv = declared_run.get("argv") or []
    portable_python_launch = portable_argv[:2] == [sys.executable, resolved]
    result.check(
        "P4",
        contained and portable_python_launch and in_root_run["returncode"] == 0
        and declared_run["returncode"] == 0 and bool(declared_init)
        and os.path.realpath(BIN) == resolved,
        {
            "declared_command_token": relative_command,
            "resolved_launcher": resolved,
            "resolved_inside_plugin_root": contained,
            "portable_python_launch": portable_argv[:2],
            "active_python_interpreter": sys.executable,
            "version_command": in_root_run["command"],
            "version_exit_code": in_root_run["returncode"],
            "version_stdout": in_root_run["stdout"].strip(),
            "declared_launcher_matches_repository_launcher": os.path.realpath(BIN) == resolved,
            "declared_launcher_session_exit_code": declared_run["returncode"],
            "declared_launcher_server_info": declared_init.get("serverInfo"),
        },
    )

    # ---- P5: the declared command + args serve exactly the documented surface --
    listing = speak([resolved, *DECLARED_ARGS], listing_requests(), cwd=root)
    tools = tools_from(listing)
    names = sorted(tool["name"] for tool in tools)
    schemas = {tool["name"]: tool.get("inputSchema") or {} for tool in tools}
    protocol_clean = all(isinstance(item, dict) and item.get("jsonrpc") == "2.0"
                         for item in listing["responses"]) and not listing["invalid_stdout_lines"]
    result.check(
        "P5",
        listing["returncode"] == 0
        and protocol_clean
        and set(names) == EXPECTED_TOOLS
        and len(listing["responses"]) == 2
        and (declared_init.get("capabilities", {}).get("tools") is not None
             or bool((listing["session"].get("1") or {}).get("result"))),
        {
            "command": f"{relative_command} {' '.join(DECLARED_ARGS)}",
            "cwd": root,
            "exit_code": listing["returncode"],
            "stdout_line_count": len(listing["responses"]),
            "every_stdout_line_is_json_rpc": protocol_clean,
            "advertised_tools": names,
            "expected_tools": sorted(EXPECTED_TOOLS),
            "unexpected_tools": sorted(set(names) - EXPECTED_TOOLS),
            "missing_tools": sorted(EXPECTED_TOOLS - set(names)),
            "tool_input_schemas": schemas,
            "environment_policy": "credential-like environment variables removed before launch",
        },
    )

    # ---- P6: skill, MCP catalogue and CLI inventory agree ------------------
    frontmatter = ""
    if skill_text.startswith("---"):
        frontmatter = skill_text.split("---", 2)[1]
    frontmatter_keys = sorted(re.findall(r"^(name|description|license|allowed-tools)\s*:",
                                         frontmatter, re.M))
    skill_dir = os.path.dirname(root_path("skills/people-finder/SKILL.md"))
    skill_dirs = sorted(name for name in os.listdir(root_path("skills"))
                        if os.path.isfile(os.path.join(root_path("skills"), name, "SKILL.md")))
    help_record, commands = cli_commands()
    command_help_exit = {name: run_cmd([BIN, name, "--help"], env=clean_env())["returncode"]
                         for name in commands}
    missing_tool_mentions = sorted(name for name in EXPECTED_TOOLS if name not in skill_text)
    missing_prop_mentions = sorted(
        f"{tool}.{prop}" for tool, props in EXPECTED_TOOL_PROPS.items()
        for prop in sorted(props) if prop not in skill_text)
    missing_commands = sorted(name for name in EXPECTED_CLI_COMMANDS if name not in skill_text)
    result.check(
        "P6",
        frontmatter_keys == ["description", "name"]
        and f"name: {MANIFEST_NAME}" in frontmatter
        and skill_dirs == ["people-finder"]
        and os.path.basename(skill_dir) == MANIFEST_NAME
        and set(commands) == EXPECTED_CLI_COMMANDS
        and all(code == 0 for code in command_help_exit.values())
        and not missing_tool_mentions and not missing_prop_mentions and not missing_commands
        and set(schemas) == EXPECTED_TOOLS
        and all(set((schemas[name].get("properties") or {})) == props
                for name, props in EXPECTED_TOOL_PROPS.items())
        and all(not schemas[name].get("required") for name in EXPECTED_TOOLS)
        and all(schemas[name].get("additionalProperties") is False for name in EXPECTED_TOOLS),
        {
            "skill_path": "skills/people-finder/SKILL.md",
            "skill_sha256": inventory["skills/people-finder/SKILL.md"],
            "skill_frontmatter_keys": frontmatter_keys,
            "skill_directories_with_SKILL_md": skill_dirs,
            "cli_commands": commands,
            "cli_command_help_exit_codes": command_help_exit,
            "advertised_tool_properties": {name: sorted((schemas[name].get("properties") or {}))
                                           for name in EXPECTED_TOOLS},
            "tools_not_named_in_skill": missing_tool_mentions,
            "tool_properties_not_named_in_skill": missing_prop_mentions,
            "cli_commands_not_named_in_skill": missing_commands,
            "no_tool_has_required_arguments": all(not schemas[name].get("required")
                                                 for name in EXPECTED_TOOLS),
            "schemas_are_closed": all(schemas[name].get("additionalProperties") is False
                                      for name in EXPECTED_TOOLS),
        },
    )

    # ---- P7: real offline calls over the declared server on labelled fixtures --
    compile_request = {
        "jsonrpc": "2.0", "id": "compile-1", "method": "tools/call",
        "params": {"name": "compile_people_queries", "arguments": compile_arguments()},
    }
    compile_run = speak([resolved, *DECLARED_ARGS], [compile_request], cwd=root)
    compiled = tool_payload(compile_run["session"].get("compile-1")) or {}
    rank_request = {
        "jsonrpc": "2.0", "id": "rank-1", "method": "tools/call",
        "params": {"name": "rank_people_candidates", "arguments": rank_arguments(compiled)},
    }
    rank_run = speak([resolved, *DECLARED_ARGS], [rank_request], cwd=root)
    ranked = tool_payload(rank_run["session"].get("rank-1")) or {}
    target_job = read_json(JOB_A)
    fixture_serp = read_json(SERP_A)
    candidates = ranked.get("candidates") or []
    top = candidates[0] if candidates else {}
    provenance_ok = all(key in (top.get("score_breakdown") or [{}])[0]
                        for key in ("anchor_type",)) if top.get("score_breakdown") else False
    result.check(
        "P7",
        compile_run["returncode"] == 0 and rank_run["returncode"] == 0
        and compiled.get("schema") == "people-queries.v1"
        and compiled.get("packs")
        and (compiled.get("target") or {}).get("company") == target_job.get("company")
        and (compiled.get("seeker") or {}).get("source_resume") == RESUME_A
        and isinstance(compiled.get("unknowns"), list)
        and ranked.get("schema") == "people-candidates.v1"
        and candidates
        and str(top.get("public_url", "")).startswith("https://www.linkedin.com/in/")
        and bool(top.get("url_observed_in")) and bool(top.get("paths"))
        and top.get("unknowns") is not None
        and provenance_ok
        and (ranked.get("provenance") or {}).get("network_used") is False
        and (ranked.get("counts") or {}).get("peers", 0) >= 1,
        {
            "class": "fixture",
            "live": False,
            "fixture_files": {
                "resume": RESUME_A,
                "job": JOB_A,
                "supplied_results": SERP_A,
                "supplied_results_fictional": fixture_serp.get("fictional"),
                "supplied_results_live_network": fixture_serp.get("live_network"),
            },
            "compile": {
                "command": f"{relative_command} {' '.join(DECLARED_ARGS)} (tools/call compile_people_queries)",
                "exit_code": compile_run["returncode"],
                "schema": compiled.get("schema"),
                "target": compiled.get("target"),
                "seeker_source": (compiled.get("seeker") or {}).get("source_resume"),
                "pack_ids": [pack.get("pack_id") for pack in compiled.get("packs", [])],
                "pack_query_count": sum(len(pack.get("queries", [])) for pack in compiled.get("packs", [])),
                "boundary": compiled.get("boundary"),
            },
            "rank": {
                "exit_code": rank_run["returncode"],
                "schema": ranked.get("schema"),
                "counts": ranked.get("counts"),
                "top_candidate": {
                    "candidate_id": top.get("candidate_id"),
                    "public_url": top.get("public_url"),
                    "paths": top.get("paths"),
                    "url_observed_in": top.get("url_observed_in"),
                    "unknowns": top.get("unknowns"),
                    "name_status": top.get("name_status"),
                    "score_breakdown_first": (top.get("score_breakdown") or [None])[0],
                },
                "provenance": ranked.get("provenance"),
                "boundary": ranked.get("boundary"),
                "outcome_counters": ranked.get("outcome_counters"),
            },
            "non_live_note": "fixtures are fabricated documents; this assertion is an offline "
                             "mechanics proof and is never reported as live discovery",
        },
    )

    # ---- P8: miss semantics — shortfalls and suppress reasons, no invention --
    miss_compile_run = speak([resolved, *DECLARED_ARGS], [
        {"jsonrpc": "2.0", "id": "compile-miss", "method": "tools/call",
         "params": {"name": "compile_people_queries", "arguments": {
             "resume_text": read_text(RESUME_C), "job": read_json(JOB_C),
             "resume_source": RESUME_C, "job_source": JOB_C, "generated_at": FIXED_AT}}},
    ], cwd=root)
    miss_compiled = tool_payload(miss_compile_run["session"].get("compile-miss")) or {}
    miss_rank_run = speak([resolved, *DECLARED_ARGS], [
        {"jsonrpc": "2.0", "id": "rank-miss", "method": "tools/call",
         "params": {"name": "rank_people_candidates", "arguments": {
             "queries": miss_compiled, "results": read_json(SERP_C),
             "generated_at": FIXED_AT}}},
    ], cwd=root)
    miss_ranked = tool_payload(miss_rank_run["session"].get("rank-miss")) or {}
    shortfalls = (miss_ranked.get("selection") or {}).get("shortfalls") or []
    shortfall_codes = sorted(item.get("code") for item in shortfalls)
    suppressed = miss_ranked.get("suppressed") or []
    suppress_reasons = sorted({item.get("reason") for item in suppressed})
    miss_unknowns = miss_ranked.get("unknowns") or []
    invented = [item for item in (miss_ranked.get("candidates") or [])]
    email_state = (miss_ranked.get("outcome_counters") or {})
    result.check(
        "P8",
        miss_compile_run["returncode"] == 0 and miss_rank_run["returncode"] == 0
        and not invented
        and (miss_ranked.get("counts") or {}).get("peers") == 0
        and shortfalls and all(item.get("reason") for item in shortfalls)
        and "no_eligible_peer" in shortfall_codes
        and suppress_reasons
        and miss_unknowns
        and email_state.get("guessed_addresses_excluded") is True
        and email_state.get("deliverability_claimed") is False
        and (email_state.get("mailbox_outcome") or {}).get("status") == "not_checked"
        and (email_state.get("attributed_email") == 0
             or (email_state.get("mailbox_outcome") or {}).get("checked") == 0),
        {
            "class": "fixture",
            "live": False,
            "fixtures": {"resume": RESUME_C, "job": JOB_C, "supplied_results": SERP_C},
            "exit_codes": [miss_compile_run["returncode"], miss_rank_run["returncode"]],
            "counts": miss_ranked.get("counts"),
            "peer_candidates_returned": len(invented),
            "selection_shortfalls": shortfalls,
            "shortfall_codes": shortfall_codes,
            "suppressed_hit_count": len(suppressed),
            "suppress_reasons": suppress_reasons,
            "suppress_reason_counts": (miss_ranked.get("selection") or {}).get("suppressed_reason_counts"),
            "unknowns": miss_unknowns,
            "email_state": email_state,
            "note": "a miss is reported as a shortfall with reason codes and suppress reasons; no "
                    "candidate, employer or address is invented to fill the gap",
        },
    )

    # ---- P9: no generic search capability and no authority surface ----------
    generic_props = {name: sorted(prop for prop in (schemas[name].get("properties") or {})
                                  if GENERIC_SEARCH_PROP_RE.match(prop))
                     for name in EXPECTED_TOOLS}
    generic_props = {key: value for key, value in generic_props.items() if value}
    tool_name_hits = [name for name in names if AUTHORITY_TOKEN_RE.search(name)]
    tool_desc_hits = [tool["name"] for tool in tools
                      if AUTHORITY_TOKEN_RE.search(tool.get("description", ""))]
    command_name_hits = {name: sorted({match.group(0).lower()
                                       for match in AUTHORITY_TOKEN_RE.finditer(name)})
                         for name in commands}
    command_name_hits = {key: value for key, value in command_name_hits.items() if value}
    result.check(
        "P9",
        not generic_props and not tool_name_hits and not tool_desc_hits and not command_name_hits
        and list(entry.get("args", [])) == DECLARED_ARGS
        and set(commands) == EXPECTED_CLI_COMMANDS,
        {
            "advertised_tools": names,
            "search_or_authority_shaped_tool_properties": generic_props,
            "authority_tokens_in_tool_names": tool_name_hits,
            "authority_tokens_in_tool_descriptions": tool_desc_hits,
            "authority_tokens_in_cli_commands": command_name_hits,
            "declared_server_args": entry.get("args"),
            "cli_commands": commands,
            "note": "the host owns the search, the identity step and every external action; the "
                    "plugin exposes no query, backend, provider, key, limit, message, apply or "
                    "browser input and no authority-bearing command",
        },
    )

    # ---- P10: host-provided PLUGIN_DATA is neither declared nor touched -----
    canary_dir = os.path.join(workdir, "host-plugin-data")
    os.makedirs(canary_dir, exist_ok=True)
    canary_file = os.path.join(canary_dir, "store.json")
    with open(canary_file, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"canary": "untouched", "records": 0}) + "\n")
    canary_before = tree_state(canary_dir)
    host_env = guard_env(net, base=clean_env({"PLUGIN_DATA": canary_dir}))
    host_run = speak([resolved, *DECLARED_ARGS], [compile_request], env=host_env, cwd=root)
    host_compiled = tool_payload(host_run["session"].get("compile-1")) or {}
    bare_run = speak([resolved, *DECLARED_ARGS], [compile_request], cwd=root)
    bare_compiled = tool_payload(bare_run["session"].get("compile-1")) or {}
    canary_after = tree_state(canary_dir)
    violations = guard_violations(net)
    serialised = canonical(host_compiled)
    leaked = [value for value in (canary_dir, canary_file, "store.json") if value in serialised]
    declaration = "\n".join(
        line for line in (read_text(root_path("mcp.json"))
                          + read_text(root_path("compat/hermes/config.yaml.template"))).splitlines()
        if not line.strip().startswith("#"))
    documented = {relative: read_text(root_path(relative)).lower()
                  for relative in ("skills/people-finder/SKILL.md", "compat/README.md")}
    missing_data_dir_documented = all("data directory" in text for text in documented.values())
    result.check(
        "P10",
        canary_before == canary_after
        and not violations
        and not leaked
        and host_run["returncode"] == 0 and bare_run["returncode"] == 0
        and canonical(host_compiled) == canonical(bare_compiled)
        and canonical(host_compiled) == canonical(compiled)
        and "PLUGIN_DATA" not in declaration
        and missing_data_dir_documented,
        {
            "host_provided_plugin_data": canary_dir,
            "canary_tree_before": canary_before,
            "canary_tree_after": canary_after,
            "canary_unchanged": canary_before == canary_after,
            "audit_violations": violations,
            "canary_path_in_output": leaked,
            "plugin_data_declared_in_package_body": "PLUGIN_DATA" in declaration,
            "output_identical_with_and_without_host_data": canonical(host_compiled) == canonical(bare_compiled),
            "output_identical_to_canonical_run": canonical(host_compiled) == canonical(compiled),
            "documented_no_data_directory": missing_data_dir_documented,
            "declaration_source": "mcp.json plus the non-comment body of the rendered adapter template",
            "note": "the plugin declares no data directory and stores nothing, so no host state "
                    "can be read or written through it",
        },
    )

    # ---- P11: relocated install, identical results, clean uninstall --------
    install_root = os.path.join(workdir, "installed-plugin")
    os.makedirs(install_root, exist_ok=True)
    for relative in ("plugin.json", "mcp.json", "bin", "src", "skills", "compat"):
        source = root_path(relative)
        target = os.path.join(install_root, relative)
        if os.path.isdir(source):
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    installed_state = tree_state(install_root)
    installed_launcher = os.path.realpath(os.path.join(install_root, spec_relative))
    installed_contained = installed_launcher.startswith(os.path.realpath(install_root) + os.sep)
    expect(installed_contained, "installed launcher escapes the installed plugin root")
    installed_listing = speak([installed_launcher, *DECLARED_ARGS], listing_requests(), cwd=install_root)
    installed_tools = sorted(tool["name"] for tool in tools_from(installed_listing))
    installed_compile = speak([installed_launcher, *DECLARED_ARGS], [compile_request], cwd=install_root)
    installed_compiled = tool_payload(installed_compile["session"].get("compile-1")) or {}
    after_runs_state = tree_state(install_root)
    absent_absolute_path = installed_launcher not in read_text(root_path("mcp.json"))
    shutil.rmtree(install_root)
    launcher_removed = not os.path.exists(installed_launcher)
    canary_after_uninstall = tree_state(canary_dir)
    result.check(
        "P11",
        installed_contained
        and installed_listing["returncode"] == 0
        and installed_tools == sorted(EXPECTED_TOOLS)
        and installed_compile["returncode"] == 0
        and canonical(installed_compiled) == canonical(compiled)
        and installed_state == after_runs_state
        and absent_absolute_path
        and launcher_removed
        and canary_after_uninstall == canary_before,
        {
            "install_root": install_root,
            "installed_launcher": installed_launcher,
            "installed_command_token_is_relative": True,
            "absolute_install_path_required_by_package": not absent_absolute_path,
            "installed_tools": installed_tools,
            "installed_listing_exit_code": installed_listing["returncode"],
            "installed_compile_exit_code": installed_compile["returncode"],
            "installed_output_identical_to_in_place_run": canonical(installed_compiled) == canonical(compiled),
            "package_tree_unchanged_by_running_the_mcp_server": installed_state == after_runs_state,
            "files_created_inside_package": sorted(set(after_runs_state) - set(installed_state)),
            "launcher_absent_after_uninstall": launcher_removed,
            "host_data_survived_uninstall": canary_after_uninstall == canary_before,
            "note": "the plugin has no installer step and no data directory: uninstalling it removes "
                    "the tools and leaves every host-provided path untouched",
        },
    )

    # ---- P12: deterministic repetition and an honest compatibility matrix --
    repeat_compile = speak([resolved, *DECLARED_ARGS], [compile_request], cwd=root)
    repeat_compiled = tool_payload(repeat_compile["session"].get("compile-1")) or {}
    repeat_rank = speak([resolved, *DECLARED_ARGS], [rank_request], cwd=root)
    repeat_ranked = tool_payload(repeat_rank["session"].get("rank-1")) or {}
    clients = matrix.get("clients") or {}
    allowed_statuses = set((matrix.get("verification") or {}).get("statuses") or [])
    status_rows = {name: entry_doc.get("status") for name, entry_doc in clients.items()}
    verified_claims = {name: entry_doc for name, entry_doc in clients.items()
                       if entry_doc.get("status") == "verified"}
    verified_backed = all(entry_doc.get("verifiedBy") and entry_doc.get("probe")
                          for entry_doc in verified_claims.values())
    probe_exists = os.path.isfile(root_path("tests/plugin/check_hermes_host_probe.py"))
    adapter_rendered = adapter_template.replace(
        "__PEOPLE_FINDER_LAUNCHER__", resolved)
    adapter_lines = [line.strip() for line in adapter_rendered.splitlines()
                     if line.strip() and not line.strip().startswith("#")]
    adapter_expected = ["mcp_servers:", "people-finder:", f"command: {resolved}", "args: [mcp]"]
    stale_markers = sorted({marker for marker in ("__JOBSSS", "__PEOPLE_FINDER_LAUNCHER__")
                            if marker in adapter_rendered})
    package_hashes_after = {relative: sha256_file(root_path(relative)) for relative in PACKAGING_FILES}
    result.check(
        "P12",
        canonical(repeat_compiled) == canonical(compiled)
        and canonical(repeat_ranked) == canonical(ranked)
        and (control(repeat_ranked, ranked))
        and (ranked.get("provenance") or {}).get("deterministic_ops") is not None
        and bool(clients) and set(status_rows.values()) <= allowed_statuses
        and verified_backed and probe_exists
        and adapter_lines == adapter_expected
        and not stale_markers
        and package_hashes_after == inventory,
        {
            "repetition": {
                "compile_byte_identical": canonical(repeat_compiled) == canonical(compiled),
                "rank_byte_identical": canonical(repeat_ranked) == canonical(ranked),
                "candidate_order_identical": [item.get("candidate_id") for item in
                                              (repeat_ranked.get("candidates") or [])]
                == [item.get("candidate_id") for item in candidates],
                "paths_identical": [item.get("paths") for item in (repeat_ranked.get("candidates") or [])]
                == [item.get("paths") for item in candidates],
                "determinism_note_from_document": (ranked.get("provenance") or {}).get("deterministic_ops"),
            },
            "compatibility_matrix": {
                "file": "compat/matrix.json",
                "sha256": inventory["compat/matrix.json"],
                "client_statuses": status_rows,
                "allowed_statuses": sorted(allowed_statuses),
                "verified_entries": sorted(verified_claims),
                "every_verified_entry_has_receipt": verified_backed,
                "probe_script_present": probe_exists,
            },
            "rendered_adapter": {
                "template": "compat/hermes/config.yaml.template",
                "rendered_lines": adapter_lines,
                "expected_lines": adapter_expected,
                "unexpanded_placeholders": stale_markers,
            },
            "packaging_hashes_unchanged_by_run": package_hashes_after == inventory,
        },
    )

    result.note(
        packaging_inventory=inventory,
        plugin_root=root,
        launcher=resolved,
        scratch_dir=workdir,
        hash_frozen_benchmark=sha256_file(root_path("BENCHMARK.md")),
        live_e2e_journey="tests/e2e/check_profile_jobs_people.py",
        live_e2e_journey_sha256=sha256_file(root_path("tests/e2e/check_profile_jobs_people.py")),
        evidence_classes={
            "offline": True,
            "fixture": True,
            "live": False,
            "note": "every assertion here is offline and fixture-based; nothing in this check is "
                    "reported as live discovery",
        },
    )


def control(repeat_ranked, ranked):
    """Repeated runs must reproduce the same selection set, not merely the same bytes."""
    return ([(item.get("candidate_id"), item.get("score"))
             for item in (repeat_ranked.get("candidates") or [])]
            == [(item.get("candidate_id"), item.get("score"))
                for item in (ranked.get("candidates") or [])])


def main():
    result = Result()
    try:
        body(result)
    except HarnessError as cause:
        result.check("HARNESS", False, {"error": str(cause)})
        return result.finish(harness_error=str(cause))
    except Exception as cause:  # noqa: BLE001 - reported, never swallowed
        import traceback
        result.check("HARNESS", False, {"error": repr(cause),
                                       "traceback": traceback.format_exc().splitlines()[-6:]})
        return result.finish(harness_error=repr(cause))
    return result.finish()


if __name__ == "__main__":
    sys.exit(main())
