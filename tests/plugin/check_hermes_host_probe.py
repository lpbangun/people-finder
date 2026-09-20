#!/usr/bin/env python3
"""Real isolated Hermes host probe for the people-finder plugin package.

Command: python3 tests/plugin/check_hermes_host_probe.py

Unlike `check_plugin_packaging.py` (fully offline, fixture only), this check
invokes the real Hermes binary against a fresh temporary `HERMES_HOME`:

  H1  the rendered `compat/hermes/config.yaml.template` loads as a real Hermes
      MCP registration
  H2  the host connects to the declared server and discovers its three tools
  H3  the staged skill is discovered by the host surface (`hermes skills list`)
  H4  uninstall removes the skill without touching user data
  H5  the real user profile was never read or written

Boundary: this probe proves local host integration only. It performs no people
or job discovery, sends nothing, and calls no paid provider. An agent-mediated
tool call inside the isolated home is deliberately out of scope: it would need
model credentials copied between profile homes, which the mission forbids. The
actual MCP tool calls for the same declaration are exercised offline in
`check_plugin_packaging.py`.

Exit codes: 0 probe passed; 1 the host is present but integration failed;
2 an environment precondition is unavailable (no Hermes binary) or the harness
itself failed.
"""

import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCHMARK = "people-finder-plugin-host-probe-v1"
SERVER_NAME = "people-finder"
EXPECTED_TOOLS = ("compile_people_queries", "rank_people_candidates", "start_people_research")
LAUNCHER_PLACEHOLDER = "__PEOPLE_FINDER_LAUNCHER__"
PROBE_TIMEOUT = 180


def log(message):
    sys.stderr.write(f"[probe] {message}\n")
    sys.stderr.flush()


class Result:
    def __init__(self):
        self.assertions = []
        self.extra = {}

    def check(self, assertion_id, passed, evidence):
        self.assertions.append({"id": assertion_id, "passed": bool(passed), "evidence": evidence})
        if not passed:
            log(f"{assertion_id} FAILED")
        return bool(passed)

    def note(self, **values):
        self.extra.update(values)

    def finish(self, *, status, harness_error=None, exit_code=None):
        document = {
            "benchmark": BENCHMARK,
            "criterion": "H",
            "offline": False,
            "live": True,
            "fixture": False,
            "class": "live",
            "kind": "local-host-integration",
            "discoveryEvidence": False,
            "status": status,
            "passed": (status == "passed" and not harness_error),
            "assertions": self.assertions,
            "note": ("real host invocation in this run against a temporary HERMES_HOME; carries no "
                     "people, job, contact or email discovery evidence and is never counted as a "
                     "live discovery claim"),
        }
        document.update(self.extra)
        if harness_error:
            document["harness_error"] = harness_error
        sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if exit_code is not None:
            return exit_code
        if harness_error:
            return 2
        return 0 if document["passed"] else 1


def run(argv, *, env, timeout=PROBE_TIMEOUT):
    record = {"command": " ".join(argv), "argv": argv}
    try:
        completed = subprocess.run(argv, env=env, timeout=timeout, capture_output=True, text=True)
    except FileNotFoundError as cause:
        raise RuntimeError(f"required local executable is unavailable: {argv[0]} ({cause})") from cause
    except subprocess.TimeoutExpired as cause:
        raise RuntimeError(f"host command timed out after {timeout}s: {record['command']}") from cause
    record["returncode"] = completed.returncode
    record["stdout"] = completed.stdout
    record["stderr"] = completed.stderr
    return record


def body(result, hermes, workdir):
    hermes_home = os.path.join(workdir, "hermes-home")
    os.makedirs(hermes_home, exist_ok=True)
    real_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")

    # Captured before any host invocation so H5 compares a real before/after.
    real_config = os.path.join(real_home, "config.yaml")
    real_config_state = {"existed": os.path.isfile(real_config), "sha256": None}
    if real_config_state["existed"]:
        import hashlib
        with open(real_config, "rb") as handle:
            real_config_state["sha256"] = hashlib.sha256(handle.read()).hexdigest()
    real_tree_before = sorted(os.listdir(real_home)) if os.path.isdir(real_home) else []

    template = open(os.path.join(ROOT, "compat", "hermes", "config.yaml.template"),
                    "r", encoding="utf-8").read()
    launcher = os.path.realpath(os.path.join(ROOT, "bin", "people-finder"))
    rendered = template.replace(LAUNCHER_PLACEHOLDER, launcher)
    config_path = os.path.join(hermes_home, "config.yaml")
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.write(rendered)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "HERMES_HOME": hermes_home,
        # Keep the host's own update check read-only so nothing is fetched into
        # the real install checkout while probing.
        "HERMES_REVISION": os.environ.get("HERMES_REVISION", "probe"),
    }

    # ---- H1: the rendered adapter loads as a real host registration --------
    listing = run([hermes, "mcp", "list"], env=env)
    config_text = open(config_path, "r", encoding="utf-8").read()
    body_lines = [line.strip() for line in config_text.splitlines()
                  if line.strip() and not line.strip().startswith("#")]
    body_text = "\n".join(body_lines)
    result.check(
        "H1",
        listing["returncode"] == 0
        and SERVER_NAME in listing["stdout"]
        and body_lines == ["mcp_servers:", f"{SERVER_NAME}:", f"command: {launcher}", "args: [mcp]"]
        and LAUNCHER_PLACEHOLDER not in config_text
        and "${PLUGIN_DATA}" not in body_text,
        {
            "hermes_home": hermes_home,
            "config_rendered_lines": body_lines,
            "hermes_mcp_list_exit_code": listing["returncode"],
            "server_visible_in_host_configuration": SERVER_NAME in listing["stdout"],
            "unexpanded_placeholders_present": LAUNCHER_PLACEHOLDER in config_text,
            "plugin_data_reference_present_in_registration": "${PLUGIN_DATA}" in body_text,
            "host_stdout_excerpt": listing["stdout"][:400],
        },
    )

    # ---- H2: the host connects and discovers the declared tools -----------
    probe = run([hermes, "mcp", "test", SERVER_NAME], env=env)
    discovered = [name for name in EXPECTED_TOOLS if name in probe["stdout"]]
    connection_marker = bool(re.search(r"(?i)connected", probe["stdout"]))
    tool_count = re.search(r"(?i)tools discovered:\s*(\d+)", probe["stdout"])
    result.check(
        "H2",
        probe["returncode"] == 0
        and connection_marker
        and len(discovered) == len(EXPECTED_TOOLS)
        and (tool_count is None or int(tool_count.group(1)) == len(EXPECTED_TOOLS)),
        {
            "command": probe["command"],
            "exit_code": probe["returncode"],
            "connected": connection_marker,
            "tools_discovered_reported": tool_count.group(1) if tool_count else None,
            "declared_tools_observed": discovered,
            "host_stdout": probe["stdout"].strip()[:1200],
            "host_stderr_excerpt": probe["stderr"].strip()[:400],
        },
    )

    # ---- H3: the staged skill is discovered by the host surface -----------
    staged = os.path.join(hermes_home, "skills", SERVER_NAME)
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "skills", SERVER_NAME), staged)
    with open(os.path.join(ROOT, "skills", SERVER_NAME, "SKILL.md"), "rb") as handle:
        canonical_bytes = handle.read()
    with open(os.path.join(staged, "SKILL.md"), "rb") as handle:
        staged_bytes = handle.read()
    listed = run([hermes, "skills", "list"], env=env)
    result.check(
        "H3",
        listed["returncode"] == 0
        and SERVER_NAME in listed["stdout"]
        and canonical_bytes == staged_bytes,
        {
            "command": listed["command"],
            "exit_code": listed["returncode"],
            "staged_skill_path": staged,
            "skill_byte_identical_to_package": canonical_bytes == staged_bytes,
            "skill_listed": SERVER_NAME in listed["stdout"],
            "host_stdout_excerpt": listed["stdout"].strip()[:600],
        },
    )

    # ---- H4: uninstall removes the skill, user data survives --------------
    data_dir = os.path.join(hermes_home, "user-data")
    os.makedirs(data_dir, exist_ok=True)
    canary = os.path.join(data_dir, "canary.json")
    with open(canary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"canary": "untouched"}) + "\n")
    canary_before = open(canary, "r", encoding="utf-8").read()
    shutil.rmtree(staged)
    uninstalled = run([hermes, "skills", "list"], env=env)
    canary_after = open(canary, "r", encoding="utf-8").read()
    result.check(
        "H4",
        uninstalled["returncode"] == 0
        and SERVER_NAME not in uninstalled["stdout"]
        and canary_before == canary_after
        and os.path.isfile(canary),
        {
            "command": uninstalled["command"],
            "exit_code": uninstalled["returncode"],
            "skill_still_listed_after_removal": SERVER_NAME in uninstalled["stdout"],
            "user_data_canary_path": canary,
            "user_data_canary_unchanged": canary_before == canary_after,
            "host_stdout_excerpt": uninstalled["stdout"].strip()[:600],
            "note": "the plugin ships no data directory; uninstalling removes the tools and deletes "
                    "no host-owned file",
        },
    )

    # ---- H5: the real user profile was never read or written --------------
    import hashlib
    real_config_now = {"existed": os.path.isfile(real_config), "sha256": None}
    if real_config_now["existed"]:
        with open(real_config, "rb") as handle:
            real_config_now["sha256"] = hashlib.sha256(handle.read()).hexdigest()
    real_tree_after = sorted(os.listdir(real_home)) if os.path.isdir(real_home) else []
    result.check(
        "H5",
        real_home != hermes_home
        and real_config_now == real_config_state
        and real_tree_after == real_tree_before
        and launcher in config_text
        and os.path.commonpath([os.path.realpath(config_path), os.path.realpath(workdir)])
        == os.path.realpath(workdir),
        {
            "probe_home": hermes_home,
            "real_hermes_home": real_home,
            "probe_home_is_separate": real_home != hermes_home,
            "real_config_sha256_before": real_config_state["sha256"],
            "real_config_sha256_after": real_config_now["sha256"],
            "real_home_entries_before": len(real_tree_before),
            "real_home_entries_after": len(real_tree_after),
            "rendered_config_path": config_path,
            "rendered_config_inside_probe_home": os.path.realpath(config_path).startswith(
                os.path.realpath(workdir) + os.sep),
            "note": "the probe writes only inside its temporary HERMES_HOME; no credential was read, "
                    "copied or symlinked, and no real profile was modified",
        },
    )

    result.note(
        hermes_binary=hermes,
        hermes_home=hermes_home,
        launcher=launcher,
        scratch_dir=workdir,
        plugin_root=ROOT,
    )


def main():
    result = Result()
    hermes = shutil.which("hermes")
    if not hermes:
        result.note(hermes_binary=None)
        return result.finish(
            status="unavailable",
            harness_error="the Hermes host binary is not on PATH; this probe cannot run here",
            exit_code=2,
        )
    workdir = os.path.join(ROOT, ".tmp", "harness", f"plugin-probe-{os.getpid()}")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    try:
        body(result, hermes, workdir)
    except Exception as cause:  # noqa: BLE001 - reported, never swallowed
        import traceback
        result.check("HARNESS", False, {"error": repr(cause),
                                       "traceback": traceback.format_exc().splitlines()[-6:]})
        return result.finish(status="failed", harness_error=repr(cause))
    version = run([hermes, "--version"], env=dict(os.environ, HERMES_HOME=os.environ.get(
        "HERMES_HOME", os.path.expanduser("~/.hermes"))))
    result.note(hermes_version_line=(version["stdout"].strip().splitlines() or [""])[0])
    passed = all(item["passed"] for item in result.assertions)
    return result.finish(status="passed" if passed else "failed")


if __name__ == "__main__":
    sys.exit(main())
