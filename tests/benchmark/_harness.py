"""Shared helpers for the frozen benchmark checks.

Standard library only, no network, no import of the product package: every check
talks to the product through its public CLI or MCP surface, so the checks stay an
independent verification of the shipped artifact.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BIN = os.path.join(ROOT, "bin", "people-finder")
JOBSSS = os.environ.get(
    "PEOPLE_FINDER_JOBSSS_EXECUTABLE", "/home/logani/projects/jobsss/bin/jobsss"
)
BENCHMARK = "people-finder-v1"

RESUME_A = "tests/fixtures/resumes/a-rich-stamps.md"
RESUME_B = "tests/fixtures/resumes/b-career-hop.md"
RESUME_C = "tests/fixtures/resumes/c-thin-noisy.md"
JOB_A = "tests/fixtures/jobs/a-target.json"
JOB_B = "tests/fixtures/jobs/b-target.json"
JOB_C = "tests/fixtures/jobs/c-target.json"
SERP_A = "tests/fixtures/serps/a-recorded.json"
SERP_B = "tests/fixtures/serps/b-recorded.json"
SERP_C = "tests/fixtures/serps/c-recorded.json"
EXA_NO_RESULT = "tests/fixtures/exa/no-result.json"
EXA_ONE_PERSON = "tests/fixtures/exa/one-person.json"
EXA_MULTIPLE_INVALID = "tests/fixtures/exa/multiple-people-invalid.json"

FIXTURE_PATHS = (
    RESUME_A, RESUME_B, RESUME_C, JOB_A, JOB_B, JOB_C, SERP_A, SERP_B, SERP_C,
    EXA_NO_RESULT, EXA_ONE_PERSON, EXA_MULTIPLE_INVALID,
)

CHECK_PATHS = (
    "tests/benchmark/check_discovery.py",
    "tests/benchmark/check_ranking.py",
    "tests/benchmark/check_exa_import.py",
    "tests/benchmark/check_interfaces.py",
    "tests/benchmark/check_jobsss_composition.py",
    "tests/benchmark/check_keepouts.py",
)

FROZEN_COMMAND_PATHS = CHECK_PATHS

FROZEN_ASSERTIONS = {
    "D": [f"D{i}" for i in range(1, 8)],
    "R": [f"R{i}" for i in range(1, 9)],
    "E": [f"E{i}" for i in range(1, 8)],
    "I": [f"I{i}" for i in range(1, 10)],
    "J": [f"J{i}" for i in range(1, 12)],
    "K": [f"K{i}" for i in range(1, 11)],
}

# Credential-like environment variables that must never be required. Any name
# ending in one of these suffixes is removed as well.
CREDENTIAL_ENV_KEYS = (
    "EXA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
    "HF_TOKEN", "HUGGINGFACE_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "GH_TOKEN", "SLACK_TOKEN",
    "BRAVE_API_KEY", "SERPAPI_API_KEY", "PDL_API_KEY", "HARVEST_API_KEY",
    "APIFY_TOKEN", "SUPABASE_SERVICE_KEY", "STRIPE_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN", "MCP_TOKEN", "PLUGIN_DATA", "DATABASE_URL",
)

CREDENTIAL_SUFFIX_RE = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|_CREDENTIALS?)$")

CLAIM_TOKENS = (
    "connected", "connection", "second_degree", "second-degree", "second degree",
    "2nd_degree", "2nd-degree", "2nd degree", "1st-degree", "first-degree",
    "third-degree", "3rd-degree",
)


class HarnessError(RuntimeError):
    """Precondition/harness failure -> exit 2."""


def log(message):
    sys.stderr.write(f"[harness] {message}\n")
    sys.stderr.flush()


def expect(condition, message):
    if not condition:
        raise HarnessError(message)


def path(relative):
    return os.path.join(ROOT, relative)


def read_text(relative_or_abs):
    target = relative_or_abs if os.path.isabs(relative_or_abs) else path(relative_or_abs)
    with open(target, "r", encoding="utf-8") as handle:
        return handle.read()


def read_json(relative_or_abs):
    return json.loads(read_text(relative_or_abs))


def write_json(target, document):
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return target


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:16]


def sha256_file(target):
    digest_obj = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest_obj.update(chunk)
    return digest_obj.hexdigest()


def strip_fields(document, fields):
    """Copy the document and drop top-level fields (determinism comparisons)."""
    copy = json.loads(json.dumps(document))
    for field in fields:
        copy.pop(field, None)
    return copy


def strip_nested(document, dotted):
    copy = json.loads(json.dumps(document))
    for item in dotted:
        parts = item.split(".")
        cursor = copy
        for part in parts[:-1]:
            cursor = cursor.get(part) if isinstance(cursor, dict) else None
            if cursor is None:
                break
        if isinstance(cursor, dict):
            cursor.pop(parts[-1], None)
    return copy


def clean_env(extra=None):
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in CREDENTIAL_ENV_KEYS or CREDENTIAL_SUFFIX_RE.search(upper):
            continue
        env[key] = value
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    if extra:
        env.update(extra)
    return env


def removed_env_keys():
    removed = []
    for key in os.environ:
        upper = key.upper()
        if upper in CREDENTIAL_ENV_KEYS or CREDENTIAL_SUFFIX_RE.search(upper):
            removed.append(key)
    return sorted(removed)


def scratch(label):
    directory = os.path.join(ROOT, ".tmp", "harness", f"{label}-{os.getpid()}")
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory, exist_ok=True)
    return directory


def run_cmd(argv, *, env=None, cwd=None, timeout=180, stdin_text=None):
    record = {"argv": argv, "cwd": cwd or ROOT}
    try:
        completed = subprocess.run(
            argv, cwd=cwd or ROOT, env=env, timeout=timeout, input=stdin_text,
            capture_output=True, text=True,
        )
    except FileNotFoundError as cause:
        raise HarnessError(f"required local executable is unavailable: {argv[0]} ({cause})") from cause
    except subprocess.TimeoutExpired as cause:
        raise HarnessError(f"command timed out after {timeout}s: {' '.join(argv)}") from cause
    record["returncode"] = completed.returncode
    record["stdout"] = completed.stdout
    record["stderr"] = completed.stderr
    record["command"] = " ".join(argv)
    parsed = None
    try:
        parsed = json.loads(completed.stdout) if completed.stdout.strip() else None
    except ValueError:
        parsed = None
    record["json"] = parsed
    record["stdout_is_single_json"] = parsed is not None
    return record


def product_command(*args, executable=BIN):
    """Build a product CLI command through the active Python interpreter."""
    return [sys.executable, os.fspath(executable), *(os.fspath(arg) for arg in args)]


def run_cli(*args, env=None, timeout=180, out=None, quiet=True):
    argv = product_command(*args)
    if out:
        argv += ["--out", out]
    if quiet:
        argv += ["--quiet"]
    return run_cmd(argv, env=env if env is not None else clean_env(), timeout=timeout)


def compile_fixture(resume, job, out, env=None, at=None):
    args = ["compile", "--resume", resume, "--job", job]
    if at:
        args += ["--at", at]
    return run_cli(*args, out=out, env=env if env is not None else clean_env())


def rank_fixture(queries, results, out, env=None, at=None):
    args = ["rank", "--queries", queries, "--results", results]
    if at:
        args += ["--at", at]
    return run_cli(*args, out=out, env=env if env is not None else clean_env())


NETGUARD_TEMPLATE = '''\
import os
import socket

_LOG = os.environ.get("PEOPLE_FINDER_NETGUARD_LOG")


def _report(detail):
    if _LOG:
        with open(_LOG, "a", encoding="utf-8") as handle:
            handle.write("socket\\t" + detail + "\\n")


class NetworkDenied(OSError):
    pass


def _deny(*args, **kwargs):
    _report("denied call")
    raise NetworkDenied("network access is denied by the benchmark netguard")


socket.socket = _deny
socket.create_connection = _deny
socket.getaddrinfo = _deny
socket.gethostbyname = _deny
socket.gethostbyname_ex = _deny
socket.socketpair = _deny

try:
    import urllib.request

    urllib.request.urlopen = _deny
except Exception:
    pass
'''

AUDIT_TEMPLATE = '''\
import os
import sys

_ROOT = os.environ.get("PEOPLE_FINDER_FORBIDDEN_PATH")
_LOG = os.environ.get("PEOPLE_FINDER_AUDIT_LOG")

if _ROOT:
    _EVENTS = ("open", "os.open", "os.listdir", "os.scandir", "os.stat", "os.remove")

    def _hook(event, args):
        if event not in _EVENTS:
            return
        for arg in args:
            if isinstance(arg, bytes):
                text = arg.decode("utf-8", "replace")
            elif isinstance(arg, str):
                text = arg
            else:
                continue
            if _ROOT in text:
                if _LOG:
                    with open(_LOG, "a", encoding="utf-8") as handle:
                        handle.write(event + "\\t" + text + "\\n")
                raise RuntimeError("forbidden path access: " + text)

    sys.addaudithook(_hook)
'''


def sitecustomize_dir(label, *, statements):
    directory = scratch(label)
    with open(os.path.join(directory, "sitecustomize.py"), "w", encoding="utf-8") as handle:
        handle.write(statements)
    return directory


def netguard(label="netguard"):
    """Directory with a sitecustomize that denies every socket operation."""
    directory = sitecustomize_dir(label, statements=NETGUARD_TEMPLATE)
    log_path = os.path.join(directory, "network-attempts.log")
    return {"dir": directory, "log": log_path}


def audit_guard(label, forbidden_root):
    directory = sitecustomize_dir(label, statements=AUDIT_TEMPLATE)
    log_path = os.path.join(directory, "forbidden-access.log")
    return {"dir": directory, "log": log_path, "root": forbidden_root}


def guard_env(guard, base=None, extra=None):
    env = dict(base if base is not None else clean_env())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = guard["dir"] + (os.pathsep + existing if existing else "")
    env["PEOPLE_FINDER_NETGUARD_LOG"] = guard.get("log", "")
    if "root" in guard:
        env["PEOPLE_FINDER_FORBIDDEN_PATH"] = guard["root"]
        env["PEOPLE_FINDER_AUDIT_LOG"] = guard["log"]
    if extra:
        env.update(extra)
    return env


def guard_violations(guard):
    log_path = guard.get("log")
    if not log_path or not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def iter_strings(value, where="root"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from iter_strings(key, f"{where}.{key}")
            yield from iter_strings(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{where}[{index}]")
    elif isinstance(value, str):
        yield f"{where}", value


def claim_token_hits(document):
    hits = []
    for where, text in iter_strings(document):
        lowered = text.lower()
        for token in CLAIM_TOKENS:
            if token in lowered:
                hits.append({"path": where, "token": token})
    return hits


def truthy_forbidden_keys(document, keys, where="root"):
    hits = []
    if isinstance(document, dict):
        for key, item in document.items():
            if str(key).lower() in keys and item not in (False, None, "", 0, "false"):
                hits.append({"path": f"{where}.{key}", "value": item})
            hits.extend(truthy_forbidden_keys(item, keys, f"{where}.{key}"))
    elif isinstance(document, list):
        for index, item in enumerate(document):
            hits.extend(truthy_forbidden_keys(item, keys, f"{where}[{index}]"))
    return hits


def source_files(*relative_dirs):
    found = []
    for relative in relative_dirs:
        base = path(relative)
        if os.path.isfile(base):
            found.append(base)
            continue
        for current, _dirs, files in os.walk(base):
            for name in sorted(files):
                if name.endswith((".py", ".sh", ".json", ".md")) or name == "people-finder":
                    found.append(os.path.join(current, name))
    return found


def scan_tokens(files, tokens):
    """Return {token: [ {file, line, text} ]} for every token hit."""
    hits = {token: [] for token in tokens}
    for target in files:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    lowered = line.lower()
                    for token in tokens:
                        if token in lowered:
                            hits[token].append({
                                "file": os.path.relpath(target, ROOT),
                                "line": number,
                                "text": line.strip()[:160],
                            })
        except OSError as cause:
            raise HarnessError(f"cannot read source file {target}: {cause}") from cause
    return {token: rows for token, rows in hits.items() if rows}


class Result:
    def __init__(self, criterion):
        self.criterion = criterion
        self.assertions = []
        self.extra = {}

    def check(self, assertion_id, passed, evidence):
        self.assertions.append({
            "id": assertion_id,
            "passed": bool(passed),
            "evidence": evidence,
        })
        if not passed:
            log(f"{assertion_id} FAILED")
        return bool(passed)

    def note(self, **values):
        self.extra.update(values)

    def document(self, harness_error=None):
        document = {
            "benchmark": BENCHMARK,
            "criterion": self.criterion,
            "offline": True,
            "passed": all(item["passed"] for item in self.assertions) and not harness_error,
            "assertions": self.assertions,
        }
        document.update(self.extra)
        if harness_error:
            document["harness_error"] = harness_error
        return document

    def finish(self, harness_error=None):
        document = self.document(harness_error)
        sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if harness_error:
            return 2
        return 0 if document["passed"] else 1


def run_check(criterion, body):
    result = Result(criterion)
    try:
        body(result)
    except HarnessError as cause:
        result.check("HARNESS", False, {"error": str(cause)})
        return result.finish(harness_error=str(cause))
    except Exception as cause:  # noqa: BLE001 - reported, never swallowed
        result.check("HARNESS", False, {
            "error": repr(cause),
            "traceback": traceback.format_exc().splitlines()[-6:],
        })
        return result.finish(harness_error=repr(cause))
    return result.finish()


def optional_skip(criterion, reason):
    """Emit an explicit optional skip without manufacturing assertion passes."""
    document = {
        "benchmark": BENCHMARK,
        "criterion": criterion,
        "offline": True,
        "passed": None,
        "status": "optional/skipped",
        "optional": True,
        "skip_reason": reason,
        "assertions": [],
    }
    sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


def require_product():
    expect(os.path.isfile(BIN), f"missing required executable: {BIN}")
    expect(os.access(BIN, os.X_OK), f"not executable: {BIN}")


def mcp_call(requests, *, env=None, extra_env=None, timeout=120, executable=None):
    """Speak stdio JSON-RPC to the product; returns per-request responses."""
    argv = product_command("mcp", executable=executable or BIN)
    payload = "".join(json.dumps(request) + "\n" for request in requests)
    record = run_cmd(argv, env=env if env is not None else clean_env(extra_env),
                     timeout=timeout, stdin_text=payload)
    lines = [line for line in record["stdout"].splitlines() if line.strip()]
    responses = []
    invalid = []
    for line in lines:
        try:
            responses.append(json.loads(line))
        except ValueError:
            invalid.append(line[:200])
    record["responses"] = responses
    record["invalid_stdout_lines"] = invalid
    record["stdout_line_count"] = len(lines)
    session = {"initialize": responses[0] if responses else None, "tools_list": None}
    for response in responses:
        if not isinstance(response, dict):
            continue
        if "id" in response:
            session[str(response["id"])] = response
        result = response.get("result") or {}
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            session["tools_list"] = response
    record["session"] = session
    return record


def tool_result_text(response):
    try:
        return response["result"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


def tool_result_json(response):
    text = tool_result_text(response)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None
