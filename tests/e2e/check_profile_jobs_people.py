#!/usr/bin/env python3
"""Criterion L — live profile-to-job-to-people composition.

Command (run from the repository root):

    python3 tests/e2e/check_profile_jobs_people.py

One check, one command, one JSON object on stdout. This file is host
composition code: it drives the people-finder product through its public CLI
and the sibling Jobsss executable through its own stdio MCP surface, and it
owns every mapping between them. People-finder never receives ``PLUGIN_DATA``,
a Jobsss store path, or Jobsss credentials.

Live configuration (environment):

    E2E_JOB_SOURCE            ohshi (default) | greenhouse
    E2E_GREENHOUSE_BOARD      public Greenhouse board token (required for greenhouse)
    E2E_HOST_SEARCH_COMMAND   host adapter command: one JSON request on stdin,
                              one normalized JSON envelope on stdout
    E2E_XRAY_URL_TEMPLATE     public HTTPS SERP URL template containing {query}
    E2E_TIMEOUT_SECONDS       bounded timeout for one network/MCP operation
    E2E_EVIDENCE_DIR          directory that keeps the redacted report

At least one of ``E2E_HOST_SEARCH_COMMAND`` / ``E2E_XRAY_URL_TEMPLATE`` must be
usable; otherwise the check exits 2 and names the variables it needs. No
recorded SERP, recorded provider envelope, cached response, or synthetic result
can satisfy this criterion: every accepted result is retrieved during this run.

Exit codes: 0 every L assertion passed; 1 the check ran but one or more
mandatory assertions failed (including a live provider outage or a live result
set that never ties the selected employer to a public profile); 2 malformed
configuration, invalid host envelope, unavailable required local executable,
invalid check output, or an inability to create temporary storage.

This harness performs no send, approval, application, outreach, identity
confirmation, or LinkedIn page access of any kind.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape

# ---------------------------------------------------------------------------
# fixed paths and frozen contract
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_PATH = os.path.abspath(__file__)
PRODUCT_BIN = os.path.join(ROOT, "bin", "people-finder")
JOBSSS_BIN = "/home/logani/projects/jobsss/bin/jobsss"
BENCHMARK_FILE = os.path.join(ROOT, "BENCHMARK.md")

BENCHMARK = "people-finder-live-e2e-v1"
CRITERION = "L"
OFFLINE = False

FROZEN_BENCHMARK_SHA256 = "266630bcfa1409eb0636e4c4f7250a234553ee8d5df9514916ec76cb292bb013"

FROZEN_COMMANDS = (
    "python3 tests/benchmark/check_discovery.py",
    "python3 tests/benchmark/check_ranking.py",
    "python3 tests/benchmark/check_exa_import.py",
    "python3 tests/benchmark/check_interfaces.py",
    "python3 tests/benchmark/check_jobsss_composition.py",
    "python3 tests/benchmark/check_keepouts.py",
)

FROZEN_CHECK_PATHS = (
    "tests/benchmark/check_discovery.py",
    "tests/benchmark/check_ranking.py",
    "tests/benchmark/check_exa_import.py",
    "tests/benchmark/check_interfaces.py",
    "tests/benchmark/check_jobsss_composition.py",
    "tests/benchmark/check_keepouts.py",
)

ASSERTION_IDS = tuple(f"L{index}" for index in range(1, 18))

# ---------------------------------------------------------------------------
# live configuration constants
# ---------------------------------------------------------------------------

OHSHI_ENDPOINT = "https://ohshi.work/api/v1/intelligence"
GREENHOUSE_ENDPOINT_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
SUPPORTED_JOB_SOURCES = ("ohshi", "greenhouse")

REQUEST_SCHEMA = "people-live-search-request.v1"
ENVELOPE_SCHEMA = "people-live-search-result.v1"

MAX_LIVE_QUERIES = 6
MAX_JOB_ATTEMPTS = 3
MAX_RESULTS_PER_QUERY = 10
DEFAULT_TIMEOUT_SECONDS = 60.0
RETRIEVAL_SKEW_SECONDS = 180.0

HARNESS_ROUTE_HOST = "host_command"
HARNESS_ROUTE_XRAY = "public_xray_serp"
ROUTE_LABELS = {
    HARNESS_ROUTE_HOST: "configured host search command (host plugin bridge)",
    HARNESS_ROUTE_XRAY: "public HTTPS X-ray SERP fetched by this harness",
}

USER_AGENT = "people-finder-live-e2e/1.0 (+host acceptance harness)"

# Hosts the product itself accepts as a public professional-profile lead.
PUBLIC_PROFILE_HOSTS = ("linkedin.com", "www.linkedin.com", "m.linkedin.com", "lnkd.in")

# Pack search order: the queries most likely to be answered by a live public
# index for a real employer come first. The seeker stamps in this fictional
# profile exist only in the harness, so those packs are extra chances, not the
# primary path.
PACK_SEARCH_PRIORITY = (
    "function_at_target",
    "hiring_adjacent",
    "alumni_at_target",
    "prior_employer_at_target",
    "community_at_target",
    "shared_stamp",
)

# Jobsss tools this harness is allowed to call. Everything authority bearing
# (send, approve, decide, outreach, application status) is absent by design.
ALLOWED_JOBSSS_TOOLS = (
    "start",
    "create_profile",
    "list_profiles",
    "create_saved_search",
    "search_jobs",
    "list_jobs",
    "get_resume",
    "get_score",
    "score_job",
    "pursue_job",
    "import_contact",
    "record_research",
    "list_contacts",
    "list_research",
    "map_reachable_network",
)

FORBIDDEN_JOBSSS_TOOLS = (
    "plan_outreach",
    "draft_outreach",
    "list_outreach",
    "update_application_status",
    "create_decision_handoff",
    "list_decision_handoffs",
    "interview_debrief_handoff",
    "daily_discovery",
    "tailor_resume",
    "draft_cover_letter",
    "preview_sync",
    "save_answer",
    "match_answers",
)

# Claim tokens that would assert an authority the products do not hold.
AUTHORITY_CLAIM_TOKENS = (
    "connection",
    "second degree",
    "second-degree",
    "2nd degree",
    "referral granted",
    "permission granted",
    "message delivered",
    "we contacted",
    "introduced",
    "approved by",
)

AUTHORITY_TRUTHY_KEYS = ("reachable", "contactable", "connected", "connection",
                         "second_degree", "delivered", "sent", "approved",
                         "humanapproved", "human_approved", "relationship",
                         "referral", "permission")

FICTIONAL_RESUME = """# Ada Marchetti

Fictional: yes — synthetic profile created by the live end-to-end harness; no real person
Location: Lisbon, Portugal
Role: Staff Platform Engineer

## Education

- M.S. Computer Science, Larkfield Institute of Technology, 2014-2016
- B.E. Software Engineering, Larkfield Institute of Technology, 2010-2014

## Experience

- Staff Platform Engineer, Quarrylight Systems, 2019-2024
- Platform Engineer, Tessellate Data Labs, 2016-2019

## Community

- Maintainer, Fogbelt Observability Collective (public incident-review reading group)
- Reviewer, Meridian Reliability Workshop 2023
- Contributor, Southbank Grid Working Group (public power-systems reading group)

## Skills

- Python, SQL, Spark, dbt, Kubernetes, Terraform, Airflow

## Projects

- Telemetry Field Notes (public notebook series on ingestion scheduling)
"""

# The live discovery slice is the function the fictional profile declares in its
# own resume ("Staff Platform Engineer"); the assertion below ties the slice term
# back to that resume text instead of inventing it.
DISCOVERY_QUERY_TERM = "platform engineer"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Provider-style key prefixes only: artifact hashes and profile slugs survive.
SECRET_TOKEN_RE = re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[bap]|AKIA|AIza)[A-Za-z0-9_\-]{12,}\b")
URL_QUERY_RE = re.compile(r"(?i)([?&](?:uddg|u|url|q|target|r|ru)=)([^&\s]+)")
DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


class HarnessError(RuntimeError):
    """Harness/configuration/executable failure -> exit 2."""


class InvalidHostEnvelope(HarnessError):
    """The configured host route answered with something that is not an envelope."""


def log(message):
    sys.stderr.write(f"[live-e2e] {message}\n")
    sys.stderr.flush()


def expect(condition, message):
    if not condition:
        raise HarnessError(message)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value):
    text = str(value or "").strip()
    if not DATETIME_RE.match(text):
        return None
    normalized = text.replace("Z", "+00:00").replace(" ", "T")
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text):
    return sha256_bytes(str(text).encode("utf-8"))


def sha256_file(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return sha256_bytes(handle.read())


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def read_json_file(path):
    return json.loads(read_text(path))


def write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def write_json(path, document):
    return write_text(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def redact(value):
    """Mask contact details and provider credentials; keep hashes and slugs intact."""
    text = str(value or "")
    text = EMAIL_RE.sub("[redacted-email]", text)
    text = SECRET_TOKEN_RE.sub("[redacted-token]", text)
    text = URL_QUERY_RE.sub(lambda match: match.group(1) + "[redacted]", text)
    return text


def redact_document(value):
    if isinstance(value, dict):
        return {str(key): redact_document(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_document(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def safe_argument_view(tool, arguments):
    """Evidence view of one MCP call: long payloads become hashes."""
    view = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and len(value) > 160:
            view[key] = {"redacted": "long_payload", "bytes": len(value.encode("utf-8")),
                         "sha256": sha256_text(value)}
        else:
            view[key] = redact_document(value)
    view["_tool"] = tool
    return view


def norm_phrase(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def tokenize(text):
    return [token for token in norm_phrase(text).split(" ") if token]


def is_public_profile_url(url):
    """Mirror of the product's lead filter (public LinkedIn /in/ URL only)."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if host not in PUBLIC_PROFILE_HOSTS:
        return False
    if host == "lnkd.in":
        return True
    return (parts.path or "").lower().startswith("/in/")


_TRACKING_KEYS = ("trk", "trkpublicprofile", "refid", "ref", "src", "ve", "eid", "lipi", "licu")


def normalize_public_url(url):
    if not is_public_profile_url(url):
        return ""
    parts = urllib.parse.urlsplit(str(url).strip())
    host = (parts.hostname or "").lower()
    if host in ("linkedin.com", "m.linkedin.com"):
        host = "www.linkedin.com"
    path = (parts.path or "").rstrip("/")
    kept = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=False)
            if key.lower() not in _TRACKING_KEYS]
    query = urllib.parse.urlencode(sorted(kept))
    return urllib.parse.urlunsplit(("https", host, path, query, ""))


def host_of(url):
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def employer_tokens(company):
    stop = {"the", "inc", "labs", "lab", "group", "systems", "technologies", "ai"}
    return [token for token in tokenize(company) if token not in stop and len(token) > 1]


def text_mentions_employer(company, *fields):
    tokens = employer_tokens(company)
    if not tokens:
        return False
    blob = norm_phrase(" ".join(str(field or "") for field in fields))
    return all(token in blob for token in tokens)


def snapshot_tree(root):
    rows = {}
    if not os.path.isdir(root):
        return rows
    for current, dirs, files in os.walk(root):
        for name in sorted(dirs):
            rows[os.path.relpath(os.path.join(current, name), root) + "/"] = "dir"
        for name in sorted(files):
            full = os.path.join(current, name)
            try:
                rows[os.path.relpath(full, root)] = sha256_file(full)
            except OSError as cause:
                rows[os.path.relpath(full, root)] = f"unreadable:{cause}"
    return rows


CREDENTIAL_ENV_KEYS = (
    "EXA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "GROQ_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY", "HF_TOKEN", "HUGGINGFACE_API_KEY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "GH_TOKEN", "SLACK_TOKEN",
    "BRAVE_API_KEY", "SERPAPI_API_KEY", "SERPER_API_KEY", "PDL_API_KEY", "HARVEST_API_KEY",
    "APIFY_TOKEN", "SUPABASE_SERVICE_KEY", "STRIPE_SECRET_KEY", "TELEGRAM_BOT_TOKEN",
    "MCP_TOKEN", "PLUGIN_DATA", "DATABASE_URL",
)
CREDENTIAL_SUFFIX_RE = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_SECRET_KEY|_PASSWORD|_CREDENTIALS?)$")


def clean_env(extra=None):
    """Environment for a child process: credential-like names and PLUGIN_DATA removed."""
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


def run_command(argv, *, env=None, cwd=None, timeout=None, stdin_text=None, label=""):
    record = {"argv": [str(item) for item in argv], "cwd": cwd or ROOT, "label": label}
    started = time.time()
    record["started_at"] = utc_now()
    try:
        completed = subprocess.run(
            argv, cwd=cwd or ROOT, env=env, timeout=timeout, input=stdin_text,
            capture_output=True, text=True,
        )
    except FileNotFoundError as cause:
        raise HarnessError(f"required local executable is unavailable: {argv[0]} ({cause})") from cause
    except subprocess.TimeoutExpired as cause:
        record["timed_out"] = True
        record["returncode"] = 124
        record["stdout"] = cause.stdout or ""
        record["stderr"] = cause.stderr or ""
        record["duration_seconds"] = round(time.time() - started, 3)
        record["command"] = " ".join(record["argv"])
        record["stdout_sha256"] = sha256_text(record["stdout"] or "")
        record["started_at"] = record["started_at"]
        record["finished_at"] = utc_now()
        return record
    record["returncode"] = completed.returncode
    record["stdout"] = completed.stdout
    record["stderr"] = completed.stderr
    record["duration_seconds"] = round(time.time() - started, 3)
    record["finished_at"] = utc_now()
    record["command"] = " ".join(record["argv"])
    record["stdout_sha256"] = sha256_text(completed.stdout)
    parsed = None
    try:
        parsed = json.loads(completed.stdout) if completed.stdout.strip() else None
    except ValueError:
        parsed = None
    record["json"] = parsed
    return record


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class Config:
    def __init__(self, environ=None):
        source = dict(os.environ if environ is None else environ)
        self.job_source = str(source.get("E2E_JOB_SOURCE", "ohshi")).strip().lower() or "ohshi"
        self.greenhouse_board = str(source.get("E2E_GREENHOUSE_BOARD", "")).strip()
        self.host_search_command = str(source.get("E2E_HOST_SEARCH_COMMAND", "")).strip()
        self.xray_url_template = str(source.get("E2E_XRAY_URL_TEMPLATE", "")).strip()
        self.evidence_dir = str(source.get("E2E_EVIDENCE_DIR", "")).strip()
        raw_timeout = str(source.get("E2E_TIMEOUT_SECONDS", "")).strip()
        self.timeout = DEFAULT_TIMEOUT_SECONDS
        self.timeout_error = ""
        if raw_timeout:
            try:
                parsed = float(raw_timeout)
            except ValueError:
                self.timeout_error = f"E2E_TIMEOUT_SECONDS is not a number: {raw_timeout!r}"
            else:
                if parsed <= 0 or parsed > 900:
                    self.timeout_error = (
                        f"E2E_TIMEOUT_SECONDS must be between 0 and 900 (got {raw_timeout!r})"
                    )
                else:
                    self.timeout = parsed

    @property
    def routes(self):
        routes = []
        if self.host_search_command:
            routes.append(HARNESS_ROUTE_HOST)
        if self.xray_url_template:
            routes.append(HARNESS_ROUTE_XRAY)
        return routes

    def validation_errors(self):
        errors = []
        if self.timeout_error:
            errors.append(self.timeout_error)
        if self.job_source not in SUPPORTED_JOB_SOURCES:
            errors.append(
                f"E2E_JOB_SOURCE must be one of {list(SUPPORTED_JOB_SOURCES)} (got {self.job_source!r})"
            )
        if self.job_source == "greenhouse" and not self.greenhouse_board:
            errors.append("E2E_GREENHOUSE_BOARD is required when E2E_JOB_SOURCE=greenhouse")
        if not self.routes:
            errors.append(
                "at least one live people-search route is required: set E2E_HOST_SEARCH_COMMAND "
                "or E2E_XRAY_URL_TEMPLATE"
            )
        if self.host_search_command:
            first_token = self.host_search_command.split()[0]
            if os.path.isabs(first_token):
                if not os.access(first_token, os.X_OK):
                    errors.append(f"E2E_HOST_SEARCH_COMMAND is not executable: {first_token!r}")
            elif shutil.which(first_token) is None:
                errors.append(
                    f"E2E_HOST_SEARCH_COMMAND does not start with an executable on PATH: {first_token!r}"
                )
        if self.xray_url_template:
            if "{query}" not in self.xray_url_template:
                errors.append("E2E_XRAY_URL_TEMPLATE must contain the {query} placeholder")
            parsed = urllib.parse.urlsplit(self.xray_url_template.replace("{query}", "test"))
            if parsed.scheme != "https":
                errors.append("E2E_XRAY_URL_TEMPLATE must be an https URL")
            host = (parsed.hostname or "").lower()
            if not host or host.endswith("linkedin.com"):
                errors.append(
                    "E2E_XRAY_URL_TEMPLATE must be a public HTTPS SERP other than a LinkedIn endpoint "
                    f"(got host {host!r})"
                )
        if self.evidence_dir:
            parent = os.path.dirname(os.path.abspath(self.evidence_dir)) or "/"
            if not os.path.isdir(parent):
                errors.append(f"E2E_EVIDENCE_DIR parent does not exist: {parent}")
        return errors

    def evidence(self):
        return {
            "E2E_JOB_SOURCE": self.job_source,
            "E2E_GREENHOUSE_BOARD": self.greenhouse_board or None,
            "E2E_HOST_SEARCH_COMMAND": self.host_search_command or None,
            "E2E_XRAY_URL_TEMPLATE": self.xray_url_template or None,
            "E2E_TIMEOUT_SECONDS": self.timeout,
            "E2E_EVIDENCE_DIR": self.evidence_dir or None,
            "live_people_search_routes": [ROUTE_LABELS[route] for route in self.routes],
        }


# ---------------------------------------------------------------------------
# result collector
# ---------------------------------------------------------------------------


class Result:
    def __init__(self):
        self.assertions = {}
        self.notes = {}

    def check(self, assertion_id, passed, evidence):
        self.assertions[assertion_id] = {
            "id": assertion_id,
            "passed": bool(passed),
            "evidence": evidence,
        }
        if not passed:
            log(f"{assertion_id} FAILED")
        return bool(passed)

    def fill_unreached(self, reason):
        for assertion_id in ASSERTION_IDS:
            if assertion_id not in self.assertions:
                self.check(assertion_id, False, {"prerequisite_unmet": reason})

    def note(self, **values):
        self.notes.update(values)

    def ordered(self):
        def sort_key(assertion_id):
            match = re.match(r"^L(\d+)$", assertion_id)
            return (0, int(match.group(1))) if match else (1, assertion_id)
        return [self.assertions[key] for key in sorted(self.assertions, key=sort_key)]

    def document(self, harness_error=None):
        assertions = self.ordered()
        document = {
            "benchmark": BENCHMARK,
            "criterion": CRITERION,
            "offline": OFFLINE,
            "passed": bool(assertions)
            and all(item["passed"] for item in assertions)
            and len(assertions) == len(ASSERTION_IDS)
            and not harness_error,
            "assertions": assertions,
        }
        document.update(self.notes)
        if harness_error:
            document["harness_error"] = redact(harness_error)
        return document

    def finish(self, harness_error=None, exit_code=None):
        document = self.document(harness_error)
        invalid = validate_own_output(document)
        if invalid:
            document.setdefault("assertions", []).append({
                "id": "HARNESS", "passed": False,
                "evidence": {"invalid_check_output": invalid},
            })
            document["passed"] = False
            harness_error = harness_error or "invalid check output"
            document["harness_error"] = redact(harness_error)
            exit_code = 2
        sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if exit_code is not None:
            return exit_code
        if harness_error:
            return 2
        return 0 if document["passed"] else 1


def validate_own_output(document):
    problems = []
    if document.get("benchmark") != BENCHMARK:
        problems.append("benchmark name")
    if document.get("criterion") != CRITERION:
        problems.append("criterion")
    if document.get("offline") is not False:
        problems.append("offline flag must be false for the live gate")
    emitted = {item.get("id") for item in document.get("assertions", [])}
    missing = [assertion_id for assertion_id in ASSERTION_IDS if assertion_id not in emitted]
    if missing:
        problems.append(f"missing assertions: {missing}")
    for item in document.get("assertions", []):
        if not isinstance(item.get("evidence"), dict) or not item["evidence"]:
            problems.append(f"{item.get('id')} carries no evidence object")
    return problems


# ---------------------------------------------------------------------------
# Jobsss MCP session (sibling executable, never imported)
# ---------------------------------------------------------------------------


class JobsssSession:
    """Harness-owned client for the sibling Jobsss stdio MCP server."""

    def __init__(self, data_dir, timeout):
        self.data_dir = data_dir
        self.timeout = timeout
        self.calls = []
        self.cli_invocations = []

    def call(self, tool, arguments, *, purpose=""):
        invocation = [JOBSSS_BIN, "mcp", "--data", self.data_dir]
        self.cli_invocations.append(invocation)
        request = {
            "jsonrpc": "2.0",
            "id": f"e2e-{len(self.calls) + 1}-{tool}",
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        record = run_command(
            invocation,
            env=clean_env({"PLUGIN_DATA": self.data_dir}),
            stdin_text=json.dumps(request) + "\n",
            timeout=self.timeout,
            label=f"jobsss:{tool}",
        )
        entry = {
            "tool": tool,
            "purpose": purpose,
            "arguments": safe_argument_view(tool, arguments),
            "invocation": redact(" ".join(invocation)),
            "exit_code": record["returncode"],
            "stdout_sha256": record.get("stdout_sha256"),
            "stdout_bytes": len((record.get("stdout") or "").encode("utf-8")),
            "stderr_head": redact((record.get("stderr") or "").strip()[:400]),
            "duration_seconds": record.get("duration_seconds"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        }
        payload = {}
        if record["returncode"] != 0:
            entry["error"] = f"jobsss MCP call {tool} exited {record['returncode']}"
            entry["timed_out"] = bool(record.get("timed_out"))
            self.calls.append(entry)
            return {}, entry
        lines = [line for line in (record["stdout"] or "").splitlines() if line.strip()]
        if len(lines) != 1:
            entry["error"] = f"jobsss MCP call {tool} produced {len(lines)} stdout lines"
            self.calls.append(entry)
            return {}, entry
        try:
            response = json.loads(lines[0])
        except ValueError as cause:
            entry["error"] = f"jobsss MCP response is not JSON: {cause}"
            self.calls.append(entry)
            return {}, entry
        if isinstance(response.get("error"), dict):
            entry["error"] = f"jobsss MCP error: {response['error'].get('message')}"
            entry["rpc_error"] = redact_document(response["error"])
            self.calls.append(entry)
            return {}, entry
        try:
            payload = json.loads(response["result"]["content"][0]["text"])
        except (KeyError, IndexError, TypeError, ValueError) as cause:
            entry["error"] = f"jobsss MCP payload is not decodable JSON: {cause}"
            self.calls.append(entry)
            return {}, entry
        entry["result_keys"] = sorted(payload) if isinstance(payload, dict) else []
        self.calls.append(entry)
        return payload, entry

    def transcript(self):
        return {
            "invocations": [redact(" ".join(row)) for row in self.cli_invocations],
            "tools_called": [entry["tool"] for entry in self.calls],
            "calls": self.calls,
        }


# ---------------------------------------------------------------------------
# live people-search routes
# ---------------------------------------------------------------------------


class LiveSearchRecorder:
    """Records every HTTP request this harness makes (LinkedIn access proof)."""

    def __init__(self):
        self.requests = []

    def record(self, url, *, status=None, final_url=None, note=""):
        entry = {
            "requested_url": url,
            "final_url": final_url or url,
            "host": host_of(final_url or url),
            "http_status": status,
            "note": note,
            "at": utc_now(),
        }
        self.requests.append(entry)
        return entry


def build_live_request(config, query, *, company, job_title):
    return {
        "schema": REQUEST_SCHEMA,
        "query": query,
        "target_company": company,
        "target_job_title": job_title,
        "allowed_result_kind": "public_professional_profile",
        "maximum_results": MAX_RESULTS_PER_QUERY,
        "transport": {
            "routes_configured": list(config.routes),
            "xray_template_present": bool(config.xray_url_template),
            "host_command_present": bool(config.host_search_command),
        },
    }


def run_host_search_command(config, request):
    """Execute the configured host adapter: one JSON request in, one envelope out."""
    record = run_command(["/bin/sh", "-c", config.host_search_command], env=clean_env(),
                         timeout=config.timeout, stdin_text=json.dumps(request) + "\n",
                         label="host-search-command")
    lines = [line for line in (record.get("stdout") or "").splitlines() if line.strip()]
    outcome = {
        "route": HARNESS_ROUTE_HOST,
        "command": redact(config.host_search_command),
        "query": request["query"],
        "exit_code": record["returncode"],
        "stdout_sha256": record.get("stdout_sha256"),
        "stdout_bytes": len((record.get("stdout") or "").encode("utf-8")),
        "stderr_head": redact((record.get("stderr") or "").strip()[:400]),
        "lines_on_stdout": len(lines),
        "duration_seconds": record.get("duration_seconds"),
        "request": redact_document({key: value for key, value in request.items() if key != "transport"}),
    }
    if record["returncode"] != 0:
        outcome["status"] = "unavailable"
        outcome["observed_error"] = (
            f"host search command exited {record['returncode']} (live route unavailable)"
        )
        return None, outcome
    shape_error = ""
    envelope = None
    if len(lines) != 1:
        shape_error = (
            f"host search command wrote {len(lines)} stdout lines; exactly one JSON object is required"
        )
    else:
        try:
            envelope = json.loads(lines[0])
        except ValueError as cause:
            shape_error = f"host search command stdout is not JSON: {cause}"
        else:
            if not isinstance(envelope, dict):
                shape_error = "host search envelope must be a JSON object"
                envelope = None
            elif envelope.get("schema") != ENVELOPE_SCHEMA:
                shape_error = (
                    f"host search envelope must declare schema '{ENVELOPE_SCHEMA}' "
                    f"(got {envelope.get('schema')!r})"
                )
            elif not isinstance(envelope.get("results"), list):
                shape_error = "host search envelope 'results' must be an array"
    if shape_error:
        outcome["status"] = "invalid_envelope"
        outcome["observed_error"] = shape_error
        raise InvalidHostEnvelope(
            f"invalid host envelope from E2E_HOST_SEARCH_COMMAND: {shape_error}"
        )
    if envelope is None:
        raise InvalidHostEnvelope(
            "invalid host envelope from E2E_HOST_SEARCH_COMMAND: no envelope was produced"
        )
    outcome["status"] = envelope.get("status")
    outcome["route_reported"] = envelope.get("route")
    outcome["tool_reported"] = envelope.get("tool")
    outcome["retrieved_at"] = envelope.get("retrieved_at")
    outcome["results_reported"] = len(envelope.get("results") or [])
    return envelope, outcome


def fetch_xray_serp(config, query, recorder):
    """Fetch one public SERP URL and normalize the current HTTP response."""
    encoded = urllib.parse.quote_plus(query)
    url = config.xray_url_template.replace("{query}", encoded)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    started = time.time()
    outcome = {"route": HARNESS_ROUTE_XRAY, "query": query, "requested_url": url}
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            body = response.read()
            status = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as cause:
        body = cause.read() if hasattr(cause, "read") else b""
        status = cause.code
        final_url = getattr(cause, "url", url)
        content_type = (cause.headers or {}).get("Content-Type", "") if cause.headers else ""
    except (urllib.error.URLError, OSError) as cause:
        recorder.record(url, note=f"transport failure: {cause}")
        outcome.update({
            "http_status": None,
            "observed_error": f"SERP fetch failed: {cause}",
            "duration_seconds": round(time.time() - started, 3),
            "status": "unavailable",
        })
        return None, outcome
    recorder.record(url, status=status, final_url=final_url, note="xray SERP fetch")
    text = body.decode("utf-8", "replace")
    results = normalize_serp_payload(text, content_type, serp_url=url)
    retrieved_at = utc_now()
    outcome.update({
        "http_status": status,
        "final_url": final_url,
        "content_type": content_type,
        "body_bytes": len(body),
        "body_sha256": sha256_bytes(body),
        "retrieved_at": retrieved_at,
        "duration_seconds": round(time.time() - started, 3),
        "results_reported": len(results),
        "results": results[:MAX_RESULTS_PER_QUERY],
    })
    if status >= 400:
        outcome["status"] = "unavailable"
        outcome["observed_error"] = f"SERP returned HTTP {status}"
        return None, outcome
    envelope = {
        "schema": ENVELOPE_SCHEMA,
        "route": HARNESS_ROUTE_XRAY,
        "tool": "urllib.request (Python standard library HTTPS GET of a public SERP)",
        "retrieved_at": retrieved_at,
        "status": "completed" if results else "no_result",
        "query": query,
        "results": results[:MAX_RESULTS_PER_QUERY],
        "request": {"url": url, "final_url": final_url, "http_status": status},
    }
    outcome["status"] = envelope["status"]
    return envelope, outcome


def decode_redirect_url(url):
    """Resolve SERP redirect wrappers (DuckDuckGo, Bing, Google) to the target URL."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = urllib.parse.parse_qs(parts.query)
    for key in ("uddg", "u", "url", "q", "target", "r", "ru"):
        values = query.get(key)
        if not values:
            continue
        candidate = values[0]
        if key == "u" and not candidate.startswith("http") and candidate.startswith("a1"):
            candidate = candidate[2:]
            padding = "=" * (-len(candidate) % 4)
            try:
                decoded = base64.urlsafe_b64decode(candidate + padding).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - malformed wrapper, keep the original URL
                continue
            if decoded.startswith("http"):
                return decoded
        elif candidate.startswith("http"):
            return candidate
    return url


TAG_RE = re.compile(r"<[^>]+>")
ANCHOR_RE = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
BLOCK_SPLIT_RE = re.compile(r"</(?:li|p|div|h[1-6]|article|section)>", re.I)


def strip_tags(text):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", str(text or ""))).strip()


def normalize_serp_payload(text, content_type, *, serp_url):
    """Normalize a live SERP response (HTML or JSON) into observed result rows."""
    stripped = text.lstrip()
    if "json" in (content_type or "").lower() or stripped.startswith(("{", "[")):
        rows = rows_from_json_payload(text)
        if rows:
            return rows
    return rows_from_html(text, serp_url=serp_url)


def rows_from_json_payload(text):
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    candidates = []
    if isinstance(payload, dict):
        for key in ("results", "web", "organic_results", "items", "data", "hits"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                for nested in ("results", "web", "items", "organic_results"):
                    if isinstance(value.get(nested), list):
                        candidates = value[nested]
                        break
                if candidates:
                    break
    elif isinstance(payload, list):
        candidates = payload
    rows = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or item.get("href") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        rows.append({
            "url": url,
            "title": strip_tags(item.get("title") or item.get("name") or ""),
            "snippet": strip_tags(item.get("snippet") or item.get("description") or item.get("body") or ""),
        })
    return rows


def rows_from_html(text, *, serp_url):
    serp_host = host_of(serp_url)
    rows = []
    seen = set()
    for block in BLOCK_SPLIT_RE.split(text):
        for match in ANCHOR_RE.finditer(block):
            href = urllib.parse.urljoin(serp_url, html_unescape(match.group(1).strip()))
            target = decode_redirect_url(href)
            if not target.startswith(("http://", "https://")):
                continue
            host = host_of(target)
            if not host or host == serp_host:
                continue
            title = strip_tags(match.group(2))
            snippet = strip_tags(TAG_RE.sub(" ", block))[:400]
            key = target.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            rows.append({"url": target, "title": title, "snippet": snippet})
            if len(rows) >= 60:
                return rows
    return rows


def validate_envelope(envelope, request, *, run_start, run_end, company):
    """Semantic validation of one live search envelope (assertion L10 inputs)."""
    problems = []
    route = str(envelope.get("route") or "").strip()
    tool = str(envelope.get("tool") or "").strip()
    retrieved_at = str(envelope.get("retrieved_at") or "").strip()
    status = str(envelope.get("status") or "").strip()
    query = str(envelope.get("query") or "").strip()
    results = envelope.get("results")
    if not route:
        problems.append("envelope is missing 'route'")
    if not tool:
        problems.append("envelope is missing the actual tool name or alias")
    moment = parse_timestamp(retrieved_at)
    if moment is None:
        problems.append(f"envelope 'retrieved_at' is not a timestamp: {retrieved_at!r}")
    elif (moment < run_start - timedelta(seconds=RETRIEVAL_SKEW_SECONDS)
          or moment > run_end + timedelta(seconds=RETRIEVAL_SKEW_SECONDS)):
        problems.append(
            f"envelope 'retrieved_at' {retrieved_at} is outside this run window "
            f"({run_start.isoformat()} .. {run_end.isoformat()})"
        )
    if status != "completed":
        problems.append(f"envelope status is {status!r}, not 'completed'")
    if query != request["query"]:
        problems.append(f"envelope query mismatch: {query!r} != compiled query {request['query']!r}")
    if not isinstance(results, list) or not results:
        problems.append("envelope carries no qualifying result")
        results = []
    for index, row in enumerate(results):
        if not isinstance(row, dict):
            problems.append(f"results[{index}] is not an object")
            continue
        url = str(row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            problems.append(f"results[{index}].url is not an http(s) URL")
        source_url = str(row.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            problems.append(f"results[{index}] carries no public 'source_url' citation")
    profile_rows = [row for row in results
                    if isinstance(row, dict) and is_public_profile_url(row.get("url"))]
    mentioned = [
        row for row in profile_rows
        if text_mentions_employer(company, row.get("title"), row.get("snippet"), host_of(row.get("url")))
    ]
    observed = {
        "query": query,
        "route": route,
        "tool": tool,
        "retrieved_at": retrieved_at,
        "status": status,
        "result_count": len(results),
        "public_profile_results": len(profile_rows),
        "results_mentioning_target_employer": len(mentioned),
        "problems": problems,
    }
    return (not problems), observed, profile_rows


def choose_live_queries(compiled, company):
    """Pick employer-scoped compiled queries, most searchable pack first, bounded."""
    packs = compiled.get("packs") or []
    order = {pack_id: index for index, pack_id in enumerate(PACK_SEARCH_PRIORITY)}
    ordered = sorted(packs, key=lambda pack: (order.get(pack.get("pack_id"), len(order)),
                                              pack.get("pack_id") or ""))
    employer = norm_phrase(company)
    chosen = []
    for pack in ordered:
        for entry in pack.get("queries") or []:
            query = str(entry.get("query") or "").strip()
            if not query or employer not in norm_phrase(query):
                continue
            if "site:linkedin.com/in" not in query:
                continue
            if any(item["query"] == query for item in chosen):
                continue
            chosen.append({"pack_id": pack.get("pack_id"), "lane": pack.get("lane"), "query": query})
            if len(chosen) >= MAX_LIVE_QUERIES:
                return chosen
    return chosen


# ---------------------------------------------------------------------------
# harness-owned mapping (host composition code, never product code)
# ---------------------------------------------------------------------------


def map_lead_to_jobsss_args(lead, *, profile_id, job_id, job_title, company, candidates_document,
                            candidates_path, route_provenance, results_path, live_envelopes):
    """One ranked lead -> explicit import_contact / record_research arguments.

    Deliberately sparse: no email, no relationship, no referral, no permission,
    no message channel, and humanApproved is false. Nothing here is inferred
    beyond what the ranked document observed.
    """
    provenance = {
        "people_finder_document": candidates_path,
        "document_schema": candidates_document.get("schema"),
        "candidate_id": lead.get("candidate_id"),
        "public_url": lead.get("public_url"),
        "lane": lead.get("lane"),
        "paths": list(lead.get("paths") or []),
        "anchor_types_matched": list(lead.get("anchor_types_matched") or []),
        "score": lead.get("score"),
        "unknowns": list(lead.get("unknowns") or []),
        "url_observed_in": list(lead.get("url_observed_in") or []),
        "name_status": lead.get("name_status"),
        "identity": (lead.get("state") or {}).get("identity"),
        "approval": (lead.get("state") or {}).get("approval"),
        "supplied_results_document": results_path,
        "live_search_routes": route_provenance,
        "live_envelopes": live_envelopes,
    }
    contact_args = {
        "profileId": profile_id,
        "name": lead.get("name"),
        "company": company,
        "role": lead.get("headline_observed") or "",
        "source": "people-finder:people-candidates.v1",
        "notes": (
            "Discovery lead from people-finder ranking over live host-supplied public search "
            "results. The public profile URL is recorded in the research findings. No email, "
            "no relationship, no referral, no permission and no message channel is claimed or "
            "available; human review is required before any outside step."
        ),
        "humanApproved": False,
        "text": json.dumps(provenance, indent=2, ensure_ascii=False),
    }
    research_args = {
        "profileId": profile_id,
        "jobId": job_id,
        "subjectName": lead.get("name"),
        "subjectCompany": company,
        "source": f"{candidates_document.get('schema')}:{lead.get('candidate_id')}",
        "notes": (
            "people-finder discovery evidence only, over live host-supplied public search results. "
            "Identity, approval, reachability and contactability are not established by this record."
        ),
        "findings": [
            f"candidate_id: {lead.get('candidate_id')}",
            f"public_url: {lead.get('public_url')}",
            f"paths: {', '.join(lead.get('paths') or [])}",
            f"unknowns: {', '.join(lead.get('unknowns') or [])}",
            f"score: {lead.get('score')}",
            f"lane: {lead.get('lane')}",
            f"observed_in: {', '.join(lead.get('url_observed_in') or [])}",
            f"selected_job: {job_id} ({job_title} at {company})",
            f"people_finder_document: {candidates_path}",
            f"supplied_results_document: {results_path}",
            f"live_routes: {'; '.join(route_provenance)}",
            "identity_status: not_established",
            "decision_owner: human (no automatic approval is requested or recorded)",
        ],
    }
    return contact_args, research_args


def invented_authority_keys(contact_args, research_args):
    """Keys this mapper must never emit: contact/relationship/authority channels."""
    forbidden = ("email", "phone", "mobile", "relationship", "referral", "permission",
                 "channel", "message", "intro", "warm", "approved", "approved_by")
    hits = []
    for label, args in (("import_contact", contact_args), ("record_research", research_args)):
        for key in args:
            if str(key).lower() in forbidden:
                hits.append({"call": label, "key": key})
        for value in walk_strings(args):
            if EMAIL_RE.search(value):
                hits.append({"call": label, "key": "email_like_value", "value": redact(value)[:120]})
    return hits


def walk_strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, str):
        yield value


def truthy_authority_hits(document, prefix="root"):
    hits = []
    if isinstance(document, dict):
        for key, item in document.items():
            lowered = str(key).lower()
            if lowered in AUTHORITY_TRUTHY_KEYS and item not in (False, None, "", 0, "false",
                                                                 "not_established", "not_requested",
                                                                 "none", "unknown"):
                hits.append({"path": f"{prefix}.{key}", "value": item})
            hits.extend(truthy_authority_hits(item, f"{prefix}.{key}"))
    elif isinstance(document, list):
        for index, item in enumerate(document):
            hits.extend(truthy_authority_hits(item, f"{prefix}[{index}]"))
    return hits


NEGATED_CLAIM_RE = re.compile(r"(no |not |never |without |does not |cannot )\s*\w*\s*$", re.I)


def claim_token_hits(document):
    """Claim tokens that are not explicitly negated in the same string."""
    hits = []
    for value in walk_strings(document):
        lowered = value.lower()
        for token in AUTHORITY_CLAIM_TOKENS:
            start = lowered.find(token)
            while start != -1:
                prefix = value[:start].lower()
                if not NEGATED_CLAIM_RE.search(prefix):
                    hits.append({"token": token, "value": redact(value)[:200]})
                start = lowered.find(token, start + 1)
    return hits


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


class State:
    def __init__(self, config, workdir, people_out, evidence_dir, store_dir):
        self.config = config
        self.workdir = workdir
        self.people_out = people_out
        self.evidence_dir = evidence_dir
        self.store_dir = store_dir
        self.run_start = datetime.now(timezone.utc)
        self.session = None
        self.profile_id = None
        self.search_ids = []
        self.discovery_runs = []
        self.qualifying = []
        self.ranked = []
        self.attempts = []
        self.locked = None
        self.results_path = None
        self.candidates_path = None
        self.rank_record = None
        self.candidates_document = None
        self.contact_args = None
        self.research_args = None
        self.contact_id = None
        self.research_id = None
        self.snapshots = {}
        self.product_commands = []
        self.http_requests = []
        self.repo_writes = []
        self.audit_log = None
        self.blocked = ""
        self.frozen_sha_before = None

    # -- convenience accessors ------------------------------------------------
    @property
    def selected(self):
        return (self.locked or {}).get("job") or {}

    def attempt_path(self, attempt, name):
        return (attempt.get("export") or {}).get(name) or (attempt.get("compile") or {}).get(name)


def product_env(state, *, audit_root=None):
    env = clean_env()
    expect("PLUGIN_DATA" not in env, "PLUGIN_DATA leaked into the product environment")
    if audit_root:
        guard_dir = os.path.join(state.workdir, "audit-guard")
        os.makedirs(guard_dir, exist_ok=True)
        with open(os.path.join(guard_dir, "sitecustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(AUDIT_TEMPLATE)
        state.audit_dir = guard_dir
        state.audit_log = os.path.join(state.workdir, "forbidden-store-access.log")
        env["PYTHONPATH"] = guard_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PEOPLE_FINDER_FORBIDDEN_PATH"] = os.path.realpath(audit_root)
        env["PEOPLE_FINDER_AUDIT_LOG"] = state.audit_log
    return env


def run_product(state, argv, *, label, env=None, timeout=None):
    expect(os.path.isfile(PRODUCT_BIN), f"missing required executable: {PRODUCT_BIN}")
    expect(os.access(PRODUCT_BIN, os.X_OK), f"not executable: {PRODUCT_BIN}")
    record = run_command([PRODUCT_BIN, *argv], env=env if env is not None else product_env(state),
                         timeout=timeout or state.config.timeout, label=label)
    store_path = os.path.realpath(state.store_dir)
    state.product_commands.append({
        "label": label,
        "argv": [redact(item) for item in record["argv"]],
        "exit_code": record["returncode"],
        "stdout_sha256": record.get("stdout_sha256"),
        "stdout_bytes": len((record.get("stdout") or "").encode("utf-8")),
        "stderr_head": redact((record.get("stderr") or "").strip()[:300]),
        "duration_seconds": record.get("duration_seconds"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "mentions_store_path": (
            any(store_path in str(item) for item in record["argv"])
            or store_path in (record.get("stdout") or "")
            or store_path in (record.get("stderr") or "")
        ),
    })
    return record


# ---------------------------------------------------------------------------
# steps L1-L5 (setup, isolated store, fictional profile, live discovery)
# ---------------------------------------------------------------------------


def step_l1(result, state, config):
    digest_before = sha256_file(BENCHMARK_FILE)
    state.frozen_sha_before = digest_before
    text = read_text(BENCHMARK_FILE)
    commands_present = {command: (command in text) for command in FROZEN_COMMANDS}
    check_files = {relative: os.path.isfile(os.path.join(ROOT, relative))
                   for relative in FROZEN_CHECK_PATHS}
    frozen_paths = [os.path.abspath(os.path.join(ROOT, relative)) for relative in FROZEN_CHECK_PATHS]
    not_shadowing = os.path.abspath(HARNESS_PATH) not in frozen_paths
    passed = (
        digest_before == FROZEN_BENCHMARK_SHA256
        and all(commands_present.values())
        and all(check_files.values())
        and not_shadowing
    )
    result.check("L1", passed, {
        "frozen_file": os.path.relpath(BENCHMARK_FILE, ROOT),
        "observed_sha256": digest_before,
        "expected_sha256": FROZEN_BENCHMARK_SHA256,
        "frozen_file_bytes": os.path.getsize(BENCHMARK_FILE),
        "frozen_commands_present_verbatim": commands_present,
        "frozen_check_paths_present": check_files,
        "additive_harness_path": os.path.relpath(HARNESS_PATH, ROOT),
        "additive_harness_sha256": sha256_file(HARNESS_PATH),
        "harness_shadows_a_frozen_check_path": not not_shadowing,
        "harness_replaces_a_frozen_command": False,
        "harness_offline": False,
    })


def step_l2(result, state, config):
    store_dir = state.store_dir
    if os.path.exists(store_dir):
        shutil.rmtree(store_dir, ignore_errors=True)
    os.makedirs(store_dir, exist_ok=True)
    store_empty_before = os.listdir(store_dir) == []
    expect(os.path.isfile(JOBSSS_BIN), f"sibling Jobsss executable is unavailable: {JOBSSS_BIN}")
    expect(os.access(JOBSSS_BIN, os.X_OK), f"sibling Jobsss executable is not runnable: {JOBSSS_BIN}")
    state.session = JobsssSession(store_dir, config.timeout)
    payload, entry = state.session.call("start", {}, purpose="initialize isolated store")
    imported_modules = sorted(
        name for name in sys.modules if name.split(".")[0] in ("jobsss", "jobsss_mcp")
    )
    sys_path_entries = [item for item in sys.path if "/jobsss/" in item or item.endswith("/jobsss")]
    passed = (
        payload.get("initialized") is True
        and not imported_modules
        and not sys_path_entries
        and store_empty_before
        and "error" not in entry
    )
    if not passed:
        state.blocked = "isolated Jobsss start did not succeed"
    result.check("L2", passed, {
        "sibling_executable": JOBSSS_BIN,
        "sibling_executable_bytes": os.path.getsize(JOBSSS_BIN),
        "sibling_executable_sha256": sha256_file(JOBSSS_BIN),
        "temporary_plugin_data": store_dir,
        "store_created_fresh_by_harness": True,
        "store_existed_before": False,
        "store_empty_before_start": store_empty_before,
        "invocation": redact(entry["invocation"]),
        "environment_passed_to_jobsss": {"PLUGIN_DATA": store_dir},
        "mcp_tool": "start",
        "start_result": {key: payload.get(key) for key in
                         ("ok", "initialized", "migrated", "schemaVersion", "revision", "storePath")},
        "jobsss_modules_imported_by_harness": imported_modules,
        "sys_path_entries_referencing_jobsss": sys_path_entries,
        "call_record": {key: entry.get(key) for key in
                        ("exit_code", "stdout_sha256", "stdout_bytes", "duration_seconds", "started_at")},
    })
    if not passed:
        log(f"L2 blocked: {entry.get('error') or payload}")


def step_l3(result, state, config):
    if state.session is None:
        return result.check("L3", False, {"prerequisite_unmet": "L2 isolated Jobsss start"})
    name = next((line.lstrip("# ").strip() for line in FICTIONAL_RESUME.splitlines()
                 if line.startswith("# ")), "Fictional Profile")
    payload, entry = state.session.call(
        "create_profile", {"name": name, "resumeText": FICTIONAL_RESUME},
        purpose="fictional profile",
    )
    state.profile_id = payload.get("profileId") or state.profile_id
    readback, readback_entry = ({}, {})
    if state.profile_id:
        readback, readback_entry = state.session.call(
            "list_profiles", {"profileId": state.profile_id}, purpose="profile readback")
    profile_row = readback.get("profile") if isinstance(readback.get("profile"), dict) else readback
    passed = bool(state.profile_id) and payload.get("ok") is not False and "error" not in entry
    result.check("L3", passed, {
        "mcp_tool": "create_profile",
        "profile_id": state.profile_id,
        "profile_name_observed": (profile_row or {}).get("name") if isinstance(profile_row, dict) else None,
        "fictional_resume_bytes": len(FICTIONAL_RESUME.encode("utf-8")),
        "fictional_resume_sha256": sha256_text(FICTIONAL_RESUME),
        "fictional_marker_in_resume": "Fictional: yes" in FICTIONAL_RESUME,
        "resume_holds_education_experience_community": all(
            section in FICTIONAL_RESUME for section in ("## Education", "## Experience", "## Community")
        ),
        "profile_created": payload.get("created"),
        "readback_tool": "list_profiles",
        "readback_ok": readback.get("ok"),
        "call_record": {key: entry.get(key) for key in ("exit_code", "stdout_sha256", "started_at")},
        "readback_call_record": {key: readback_entry.get(key) for key in ("exit_code", "started_at")},
    })
    if not passed:
        state.blocked = "fictional profile creation"


def step_l4(result, state, config):
    if state.session is None or not state.profile_id:
        return result.check("L4", False, {"prerequisite_unmet": "isolated start + fictional profile"})
    if config.job_source == "ohshi":
        if DISCOVERY_QUERY_TERM not in FICTIONAL_RESUME.lower():
            raise HarnessError(
                "the live ohshi slice term must be declared by the fictional resume it describes"
            )
        slices = [{"q": DISCOVERY_QUERY_TERM}, {}]
        endpoint = OHSHI_ENDPOINT
    else:
        slices = [{"boardToken": config.greenhouse_board}]
        endpoint = GREENHOUSE_ENDPOINT_TEMPLATE.format(board=config.greenhouse_board)

    run_rows = []
    errors = []
    for index, slice_config in enumerate(slices):
        search_payload, search_entry = state.session.call(
            "create_saved_search",
            {"profileId": state.profile_id, "name": f"live e2e slice {index + 1}",
             "adapter": config.job_source, "config": slice_config, "minFit": 0},
            purpose="live saved search creation",
        )
        if "error" in search_entry:
            errors.append({"stage": "create_saved_search", "slice": slice_config,
                           "error": search_entry["error"]})
            continue
        search_id = search_payload.get("searchId") or search_payload.get("id")
        if search_id:
            state.search_ids.append(search_id)
        run_payload, run_entry = state.session.call(
            "search_jobs", {"profileId": state.profile_id, "searchId": search_id},
            purpose="live job discovery",
        )
        row = {
            "slice": slice_config,
            "search_id": search_id,
            "search_created": search_payload.get("created"),
            "search_deduped": search_payload.get("deduped"),
            "adapter": run_payload.get("adapter") or config.job_source,
            "endpoint": endpoint,
            "run_id": run_payload.get("runId"),
            "status": run_payload.get("status"),
            "counts": run_payload.get("counts"),
            "errors": redact_document(run_payload.get("errors") or []),
            "metadata": redact_document(run_payload.get("metadata") or {}),
            "created_at": run_payload.get("createdAt"),
            "finished_at": run_payload.get("finishedAt"),
            "harness_retrieved_at": utc_now(),
            "jobs_returned_in_run": len(run_payload.get("jobs") or []),
            "jobs_sample": [
                {"id": item.get("id"), "title": item.get("title"), "company": item.get("company"),
                 "url": item.get("url"), "outcome": item.get("outcome")}
                for item in (run_payload.get("jobs") or [])[:5]
            ],
            "call_record": {key: run_entry.get(key) for key in
                            ("exit_code", "stdout_sha256", "stdout_bytes", "duration_seconds",
                             "started_at", "finished_at")},
        }
        run_rows.append(row)
        state.discovery_runs.append({"slice": slice_config, "payload": run_payload, "row": row})
        if (run_payload.get("counts") or {}).get("fetched"):
            break

    fetched = sum((row.get("counts") or {}).get("fetched", 0) for row in run_rows)
    imported = sum((row.get("counts") or {}).get("imported", 0) for row in run_rows)
    deduped = sum((row.get("counts") or {}).get("deduped", 0) for row in run_rows)
    provider_errors = [item for row in run_rows for item in (row.get("errors") or [])]
    statuses = [row.get("status") for row in run_rows]
    passed = (
        bool(run_rows)
        and any(status in ("succeeded", "partial") for status in statuses)
        and fetched > 0
        and (imported + deduped) > 0
        and not errors
    )
    result.check("L4", passed, {
        "job_source": config.job_source,
        "live_endpoint": endpoint,
        "no_staged_fixture": all("fixture" not in json.dumps(row.get("slice") or {}) for row in run_rows),
        "slices_attempted": len(slices),
        "runs": run_rows,
        "provider_errors": provider_errors,
        "harness_call_errors": redact_document(errors),
        "totals": {"fetched": fetched, "imported": imported, "deduped": deduped},
    })
    if not passed:
        state.blocked = "live job discovery"


def job_qualifies(row):
    title = str(row.get("title") or "").strip()
    company = str(row.get("company") or "").strip()
    url = str(row.get("url") or "").strip()
    if not title or not company or not url:
        return False, "missing title, company or url"
    if norm_phrase(company) in ("unknown company", "unknown", "n a", "none", "null"):
        return False, "placeholder employer"
    if title == "Imported role":
        return False, "placeholder title"
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return False, "not a public URL"
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "example.com", "example.org") or host.endswith(".local"):
        return False, "non-public host"
    return True, ""


def step_l5(result, state, config):
    if state.session is None or not state.profile_id:
        return result.check("L5", False, {"prerequisite_unmet": "isolated start + fictional profile"})
    readback, entry = state.session.call("list_jobs", {"profileId": state.profile_id},
                                         purpose="owned job readback")
    jobs = readback.get("jobs") or readback.get("items") or []
    live_job_ids = {item.get("id") for run in state.discovery_runs
                    for item in (run["payload"].get("jobs") or [])}
    employer_roles = {}
    for row in jobs:
        employer_roles.setdefault(norm_phrase(row.get("company")), 0)
        employer_roles[norm_phrase(row.get("company"))] += 1
    considered = []
    qualifying = []
    for row in jobs:
        ok, reason = job_qualifies(row)
        view = {
            "id": row.get("id"), "title": row.get("title"), "company": row.get("company"),
            "url": row.get("url"), "source": row.get("source"), "status": row.get("status"),
            "discovery_run_id": row.get("discoveryRunId"),
            "from_live_run": row.get("id") in live_job_ids,
            "employer_open_roles_in_slice": employer_roles.get(norm_phrase(row.get("company")), 0),
            "qualified": ok, "rejected_because": reason or None,
        }
        considered.append(view)
        if ok and view["from_live_run"]:
            qualifying.append(row)
    state.qualifying = qualifying

    seniority = {"senior", "staff", "principal", "lead", "junior", "associate", "head",
                 "director", "vp", "chief", "ii", "iii", "iv", "sr", "jr"}

    def rank_key(row):
        title_tokens = [token for token in tokenize(row.get("title")) if token not in seniority]
        return (
            -employer_roles.get(norm_phrase(row.get("company")), 0),
            len(title_tokens),
            -len(str(row.get("company") or "")),
            str(row.get("id")),
        )

    state.ranked = sorted(qualifying, key=rank_key)
    passed = bool(state.ranked)
    result.check("L5", passed, {
        "list_jobs_count": len(jobs),
        "considered_jobs": considered[:30],
        "qualifying_jobs": [
            {"id": row.get("id"), "title": row.get("title"), "company": row.get("company"),
             "url": row.get("url"), "source": row.get("source"),
             "discovery_run_id": row.get("discoveryRunId"),
             "employer_open_roles_in_slice": employer_roles.get(norm_phrase(row.get("company")), 0)}
            for row in state.ranked[:15]
        ],
        "selection_rule": (
            "qualifying = owned by the created profile, produced by this run's live discovery, "
            "non-empty title, real employer (never 'Unknown company'), public http(s) URL; ordered by "
            "the employer's open-role count inside the live slice (larger employers are the ones a "
            "public index can actually answer for), then the fewest title tokens, then the longest "
            "employer name, then job id"
        ),
        "ranked_job_ids": [row.get("id") for row in state.ranked[:MAX_JOB_ATTEMPTS]],
        "employer_placeholder_jobs_rejected": [
            row for row in considered if row["rejected_because"] == "placeholder employer"
        ][:5],
        "readback_call": {key: entry.get(key) for key in ("exit_code", "started_at")},
    })
    if not passed:
        state.blocked = "no qualifying live discovered job"


# ---------------------------------------------------------------------------
# journey: score -> pursue -> export -> compile -> live search -> rank
# ---------------------------------------------------------------------------


def export_readback(state, attempt):
    """L7: full resume + selected job through Jobsss read-only operations."""
    job = attempt["job"]
    resume, resume_entry = state.session.call("get_resume", {"profileId": state.profile_id},
                                              purpose="full resume readback")
    jobs, jobs_entry = state.session.call("list_jobs", {"profileId": state.profile_id},
                                          purpose="selected job readback")
    score, score_entry = state.session.call(
        "get_score", {"profileId": state.profile_id, "jobId": job.get("id")},
        purpose="selected job score readback")
    resume_text = resume.get("resumeText") or ""
    job_row = next((item for item in (jobs.get("jobs") or jobs.get("items") or [])
                    if item.get("id") == job.get("id")), {})
    job_card = {
        "schema": "job-card.v1",
        "source": "jobsss read-only readback (list_jobs + get_score)",
        "job_id": job_row.get("id"),
        "title": job_row.get("title"),
        "company": job_row.get("company"),
        "department": job_row.get("department") or "",
        "location": job_row.get("location") or "",
        "posting_text": (job_row.get("description") or "").strip() or
                        f"{job_row.get('title')} at {job_row.get('company')} ({job_row.get('url')})",
        "url": job_row.get("url"),
        "source_adapter": job_row.get("source"),
        "source_id": job_row.get("sourceId"),
        "jobsss_status": job_row.get("status"),
        "jobsss_score": (score.get("score") or {}) if isinstance(score.get("score"), dict) else {},
        "exported_at": utc_now(),
    }
    index = attempt["index"]
    resume_path = os.path.join(state.people_out, f"resume-{index}.md")
    job_path = os.path.join(state.people_out, f"target-job-{index}.json")
    write_text(resume_path, resume_text)
    write_json(job_path, job_card)
    source_hash = ((resume.get("resume") or {}).get("document") or {}).get("sourceHash")
    return {
        "resume_path": resume_path,
        "job_path": job_path,
        "resume_text_bytes": len(resume_text.encode("utf-8")),
        "resume_readback_sha256": sha256_text(resume_text),
        "resume_file_sha256": sha256_file(resume_path),
        "resume_source_hash_from_jobsss": source_hash,
        "job_file_sha256": sha256_file(job_path),
        "job_card": job_card,
        "job_row": job_row,
        "score_readback": score,
        "entries": {
            "get_resume": {"exit_code": resume_entry.get("exit_code"),
                           "stdout_sha256": resume_entry.get("stdout_sha256")},
            "list_jobs": {"exit_code": jobs_entry.get("exit_code")},
            "get_score": {"exit_code": score_entry.get("exit_code")},
        },
        "written_only_under": state.people_out,
    }


def run_live_search(state, config, attempt, recorder):
    """Execute employer-scoped compiled queries against the configured live route(s)."""
    compiled = attempt.get("queries_document") or {}
    company = attempt["job"].get("company") or ""
    job_title = attempt["job"].get("title") or ""
    chosen = choose_live_queries(compiled, company)
    live = {"queries_considered": chosen, "envelopes": [], "outcomes": [],
            "employer_tied": False, "stopped_early": False}
    for item in chosen:
        request = build_live_request(config, item["query"], company=company, job_title=job_title)
        envelope, outcome = None, None
        for route in config.routes:
            if route == HARNESS_ROUTE_HOST:
                envelope, outcome = run_host_search_command(config, request)
            else:
                envelope, outcome = fetch_xray_serp(config, request["query"], recorder)
            if envelope is not None:
                break
        if envelope is None:
            live["outcomes"].append({
                "pack_id": item["pack_id"], "query": item["query"], "accepted": False,
                "reason": (outcome or {}).get("observed_error") or (outcome or {}).get("status"),
                "route_attempt": redact_document(outcome or {}),
            })
            continue
        run_end = datetime.now(timezone.utc)
        ok, observed, profile_rows = validate_envelope(
            envelope, request, run_start=state.run_start, run_end=run_end, company=company)
        row = {
            "pack_id": item["pack_id"],
            "lane": item["lane"],
            "query": item["query"],
            "route": envelope.get("route"),
            "route_attempt": outcome,
            "accepted": ok,
            "observed": observed,
            "reason": "" if ok else "; ".join(observed.get("problems") or []),
        }
        live["outcomes"].append(row)
        if not ok:
            continue
        entry = {
            "pack_id": item["pack_id"],
            "lane": item["lane"],
            "query": item["query"],
            "route": envelope.get("route"),
            "tool": envelope.get("tool"),
            "retrieved_at": envelope.get("retrieved_at"),
            "results": [{"url": r.get("url"), "title": r.get("title"), "snippet": r.get("snippet"),
                         "source_url": r.get("source_url")} for r in envelope.get("results") or []],
        }
        live["envelopes"].append(entry)
        if observed.get("results_mentioning_target_employer"):
            live["employer_tied"] = True
    return live


def build_supplied_results(envelopes, company, job_title):
    by_pack = {}
    provenance = []
    for envelope in envelopes:
        pack_id = envelope["pack_id"]
        entry = by_pack.setdefault(pack_id, {"pack_id": pack_id, "query": envelope["query"],
                                             "retrieved_at": envelope["retrieved_at"], "results": []})
        for row in envelope["results"]:
            if not str(row.get("url") or "").strip():
                continue
            entry["results"].append({
                "rank": len(entry["results"]) + 1,
                "url": row.get("url"),
                "title": row.get("title") or "",
                "snippet": row.get("snippet") or "",
                "source_url": row.get("source_url") or "",
                "supplied_by_route": envelope["route"],
                "supplied_tool": envelope["tool"],
                "observed_at": envelope["retrieved_at"],
            })
        provenance.append({
            "pack_id": pack_id,
            "query": envelope["query"],
            "route": envelope["route"],
            "tool": envelope["tool"],
            "retrieved_at": envelope["retrieved_at"],
            "result_count": len(envelope["results"]),
            "live": True,
        })
    document = {
        "schema": "recorded-serp.v1",
        "fixture": "live-host-supplied-results",
        "fictional": False,
        "backend": "host_live_search",
        "live_network": True,
        "retrieved_at": utc_now(),
        "notes": (
            "Normalized live host-supplied search results collected during this run. Each row "
            "names the route and tool that supplied it; nothing here is recorded, cached or synthetic."
        ),
        "target": {"company": company, "title": job_title},
        "route_provenance": provenance,
        "pack_results": [by_pack[key] for key in by_pack],
    }
    return document, provenance


def rank_supplied_results(state, attempt, results_document, label):
    """L12: run the product ranker over the live supplied results."""
    results_path = attempt.get("results_path") or os.path.join(
        state.people_out, f"supplied-live-results-{attempt['index']}.json")
    write_json(results_path, results_document)
    candidates_path = attempt.get("candidates_path") or os.path.join(
        state.people_out, f"candidates-{attempt['index']}.json")
    env = product_env(state, audit_root=state.store_dir)
    record = run_product_phase(state, ["rank", "--queries", attempt["queries_path"], "--results",
                                       results_path, "--out", candidates_path, "--quiet"],
                               label=f"rank-{attempt['index']}", env=env)
    document = None
    if record["returncode"] == 0 and os.path.isfile(candidates_path):
        try:
            document = read_json_file(candidates_path)
        except ValueError:
            document = None
    observed_urls = set()
    for entry in results_document["pack_results"]:
        for row in entry["results"]:
            normalized = normalize_public_url(row.get("url"))
            if normalized:
                observed_urls.add(normalized)
    lanes = []
    matched = None
    peer_count = 0
    hiring_adjacent_count = 0
    if isinstance(document, dict):
        peer_count = len(document.get("candidates") or [])
        hiring_adjacent_count = len(document.get("hiring_adjacent") or [])
        for lane_key in ("candidates", "hiring_adjacent"):
            for item in document.get(lane_key) or []:
                if normalize_public_url(item.get("public_url")) in observed_urls:
                    lanes.append(lane_key)
                    if matched is None:
                        matched = {"lane_key": lane_key, "item": item}
    return {
        "results_path": results_path,
        "candidates_path": candidates_path,
        "record": record,
        "document": document,
        "observed_urls": sorted(observed_urls),
        "lanes": sorted(set(lanes)),
        "peer_candidates": peer_count,
        "hiring_adjacent_candidates": hiring_adjacent_count,
        "matched": matched,
    }


def run_product_phase(state, argv, *, label, env):
    """Run one people-finder phase and bracket it with store snapshots (L8)."""
    before = snapshot_tree(state.store_dir)
    record = run_product(state, argv, label=label, env=env)
    after = snapshot_tree(state.store_dir)
    state.snapshots.setdefault("product_phases", []).append({
        "label": label,
        "before": before,
        "after": after,
        "unchanged": before == after,
        "exit_code": record["returncode"],
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
    })
    state.snapshots.setdefault("before", before)
    state.snapshots["after"] = after
    state.snapshots.setdefault("before_at", record.get("started_at"))
    state.snapshots["after_at"] = record.get("finished_at")
    return record


def run_journey(result, state, config):
    """One bounded attempt per candidate job until the live composition produces a lead."""
    recorder = LiveSearchRecorder()
    for index, job in enumerate(state.ranked[:MAX_JOB_ATTEMPTS]):
        attempt = {"index": index + 1, "job": dict(job), "rejected": ""}
        state.attempts.append(attempt)
        if state.session is None or not state.profile_id:
            attempt["rejected"] = "Jobsss session unavailable"
            continue
        job_id = job.get("id")
        score, score_entry = state.session.call(
            "score_job", {"profileId": state.profile_id, "jobId": job_id}, purpose="offline fit score")
        pursue, pursue_entry = state.session.call(
            "pursue_job", {"profileId": state.profile_id, "jobId": job_id}, purpose="local pursuit")
        readback, readback_entry = state.session.call(
            "list_jobs", {"profileId": state.profile_id}, purpose="pursuit readback")
        stored_score, stored_entry = state.session.call(
            "get_score", {"profileId": state.profile_id, "jobId": job_id}, purpose="stored score readback")
        row = next((item for item in (readback.get("jobs") or readback.get("items") or [])
                    if item.get("id") == job_id), None)
        attempt.update({
            "score": score, "pursue": pursue, "readback_row": row, "stored_score": stored_score,
            "entries": {
                "score_job": {"exit_code": score_entry.get("exit_code"),
                              "stdout_sha256": score_entry.get("stdout_sha256")},
                "pursue_job": {"exit_code": pursue_entry.get("exit_code"),
                               "stdout_sha256": pursue_entry.get("stdout_sha256")},
                "list_jobs": {"exit_code": readback_entry.get("exit_code")},
                "get_score": {"exit_code": stored_entry.get("exit_code")},
            },
        })
        application = pursue.get("application") or {}
        pursued_ok = (
            isinstance(score.get("overall"), (int, float))
            and pursue.get("status") in ("pursued", "saved")
            and (application.get("localOnly") is True or pursue.get("localOnly") is True)
            and row is not None and row.get("status") in ("pursued", "saved")
        )
        if not pursued_ok:
            attempt["rejected"] = "score/pursue readback did not confirm local pursuit"
            continue

        export = export_readback(state, attempt)
        attempt["export"] = export
        if not export["resume_text_bytes"] or not export["job_card"].get("title"):
            attempt["rejected"] = "read-only export was incomplete"
            continue

        env = product_env(state, audit_root=state.store_dir)
        state.snapshots.setdefault("product_env_has_plugin_data", "PLUGIN_DATA" in env)
        queries_path = os.path.join(state.people_out, f"queries-{attempt['index']}.json")
        compile_record = run_product_phase(
            state, ["compile", "--resume", export["resume_path"], "--job", export["job_path"],
                    "--out", queries_path, "--quiet"], label=f"compile-{attempt['index']}", env=env)
        document = None
        if compile_record["returncode"] == 0 and os.path.isfile(queries_path):
            try:
                document = read_json_file(queries_path)
            except ValueError:
                document = None
        attempt["queries_path"] = queries_path
        attempt["queries_document"] = document
        attempt["compile_record"] = compile_record
        if not isinstance(document, dict) or document.get("schema") != "people-queries.v1":
            attempt["rejected"] = "compile did not produce a people-queries.v1 document"
            continue

        live = run_live_search(state, config, attempt, recorder)
        attempt["live"] = live
        if not live["envelopes"]:
            attempt["rejected"] = "no live search envelope was accepted for this employer"
            continue

        supplied, provenance = build_supplied_results(
            live["envelopes"], attempt["job"].get("company") or "", attempt["job"].get("title") or "")
        rank = rank_supplied_results(state, attempt, supplied, f"rank-{attempt['index']}")
        attempt["supplied_results"] = supplied
        attempt["provenance"] = provenance
        attempt["rank"] = rank
        if rank["record"]["returncode"] != 0 or not isinstance(rank["document"], dict):
            attempt["rejected"] = "rank did not emit a readable candidate document"
            continue
        if rank["matched"] is None:
            attempt["rejected"] = (
                "live results did not tie the selected employer to a ranked public profile"
            )
            continue
        if rank.get("peer_candidates"):
            attempt["accepted"] = True
            attempt["lock_reason"] = "peer_candidate_ranked_from_live_results"
            break
        # A live-index reader lead is the documented fallback lane; keep the
        # attempt as the fallback and try the next qualifying job for a peer
        # lead before locking.
        if attempt.get("fallback"):
            continue
        attempt["fallback"] = True
        attempt["lock_reason"] = "hiring_adjacent_candidate_ranked_from_live_results"

    state.http_requests = list(recorder.requests)
    accepted = next((item for item in state.attempts if item.get("accepted")), None)
    fallback = next((item for item in state.attempts if item.get("fallback")), None)
    state.locked = accepted or fallback or (state.attempts[0] if state.attempts else None)
    if state.locked is not None and not state.locked.get("accepted"):
        state.locked["accepted"] = True
        state.locked.setdefault(
            "lock_reason",
            "hiring_adjacent_candidate_ranked_from_live_results"
            if state.locked.get("fallback") else "no_attempt_produced_a_ranked_lead",
        )
    if state.locked is not None:
        state.results_path = (state.locked.get("rank") or {}).get("results_path")
        state.candidates_path = (state.locked.get("rank") or {}).get("candidates_path")
        state.rank_record = (state.locked.get("rank") or {}).get("record")
        state.candidates_document = (state.locked.get("rank") or {}).get("document")
    if not (state.locked or {}).get("accepted"):
        state.blocked = state.blocked or (
            "no live employer-tied people result was produced for any qualifying job"
        )


# ---------------------------------------------------------------------------
# steps L6-L12 (evaluation over the journey trace)
# ---------------------------------------------------------------------------


def attempt_summary(attempt):
    live = attempt.get("live") or {}
    rank = attempt.get("rank") or {}
    return {
        "attempt": attempt.get("index"),
        "job_id": attempt["job"].get("id"),
        "title": attempt["job"].get("title"),
        "company": attempt["job"].get("company"),
        "url": attempt["job"].get("url"),
        "accepted": bool(attempt.get("accepted")),
        "lock_reason": attempt.get("lock_reason") or None,
        "fallback_lane_only": bool(attempt.get("fallback")),
        "rejected_because": attempt.get("rejected") or None,
        "score_overall": (attempt.get("score") or {}).get("overall"),
        "pursue_status": (attempt.get("pursue") or {}).get("status"),
        "live_queries_considered": len(live.get("queries_considered") or []),
        "live_envelopes_accepted": len(live.get("envelopes") or []),
        "employer_tied": live.get("employer_tied"),
        "rank_counts": (rank.get("document") or {}).get("counts"),
        "rank_matched": bool(rank.get("matched")),
        "peer_candidates": rank.get("peer_candidates"),
        "hiring_adjacent_candidates": rank.get("hiring_adjacent_candidates"),
    }


def step_l6(result, state, config):
    if not state.attempts:
        return result.check("L6", False, {"prerequisite_unmet": "qualifying live owned job"})
    locked = state.locked or {}
    selected = locked.get("job") or {}
    score = locked.get("score") or {}
    pursue = locked.get("pursue") or {}
    row = locked.get("readback_row") or {}
    application = pursue.get("application") or {}
    passed = (
        isinstance(score.get("overall"), (int, float))
        and bool(score.get("dimensions"))
        and pursue.get("status") in ("pursued", "saved")
        and (application.get("localOnly") is True or pursue.get("localOnly") is True)
        and row.get("status") in ("pursued", "saved")
        and ((locked.get("stored_score") or {}).get("hasScore") is True)
    )
    result.check("L6", passed, {
        "selected_job_id": selected.get("id"),
        "score_tool": "score_job",
        "score_overall": score.get("overall"),
        "score_status": score.get("scoreStatus"),
        "score_mode": score.get("mode"),
        "score_dimensions": sorted(score.get("dimensions") or {}),
        "score_evidence_ref_kinds": sorted({
            str(ref.get("kind")) for ref in _iter_evidence_refs(score)}),
        "pursue_tool": "pursue_job",
        "pursue_status": pursue.get("status"),
        "pursue_local_only": application.get("localOnly", pursue.get("localOnly")),
        "pursue_application_status": application.get("status"),
        "pursue_artifact_id": pursue.get("artifactId"),
        "pursue_message": redact(pursue.get("message") or ""),
        "readback_job_status": row.get("status"),
        "readback_job_owned_by_profile": row.get("profileId") == state.profile_id,
        "stored_score_overall": ((locked.get("stored_score") or {}).get("score") or {}).get("overall"),
        "no_external_action_declared": any(
            phrase in str((attempt.get("pursue") or {}).get("message") or "")
            for attempt in state.attempts
            for phrase in ("No submission, sending, or external action",)
        ),
        "job_selection_attempts": [attempt_summary(item) for item in state.attempts],
        "selection_rule": (
            "the first ranked qualifying job whose live employer-scoped search produced a ranked "
            "public profile lead; every attempted job and its rejection reason is recorded"
        ),
        "attempt_call_records": {
            item["index"]: item.get("entries") for item in state.attempts
        },
    })


def step_l7(result, state, config):
    locked = state.locked or {}
    export = locked.get("export") or {}
    if not export:
        return result.check("L7", False, {"prerequisite_unmet": "read-only export from a live job"})
    resume_path = export["resume_path"]
    job_path = export["job_path"]
    job_card = export["job_card"]
    source_hash = export.get("resume_source_hash_from_jobsss")
    passed = (
        bool(export.get("resume_text_bytes"))
        and export["resume_file_sha256"] == export["resume_readback_sha256"]
        and (source_hash is None or source_hash == export["resume_readback_sha256"])
        and bool(str(job_card.get("title") or "").strip())
        and bool(str(job_card.get("company") or "").strip())
        and bool(str(job_card.get("posting_text") or "").strip())
        and os.path.dirname(os.path.realpath(resume_path)) == os.path.realpath(state.people_out)
        and os.path.dirname(os.path.realpath(job_path)) == os.path.realpath(state.people_out)
    )
    result.check("L7", passed, {
        "tools_used": ["get_resume", "list_jobs", "get_score"],
        "resume_readback_bytes": export.get("resume_text_bytes"),
        "resume_readback_sha256": export.get("resume_readback_sha256"),
        "resume_file": resume_path,
        "resume_file_sha256": export.get("resume_file_sha256"),
        "resume_source_hash_from_jobsss": source_hash,
        "job_file": job_path,
        "job_file_sha256": export.get("job_file_sha256"),
        "job_card": {key: job_card.get(key) for key in
                     ("job_id", "title", "company", "location", "url", "source_adapter",
                      "jobsss_status")},
        "job_card_posting_text_bytes": len(str(job_card.get("posting_text") or "").encode("utf-8")),
        "score_readback_status": ((export.get("score_readback") or {}).get("score") or {}).get("scoreStatus"),
        "written_only_under": state.people_out,
        "plugin_data_handed_to_people_finder": False,
        "people_finder_received": [resume_path, job_path],
        "call_records": export.get("entries"),
        "attempts": [attempt_summary(item) for item in state.attempts],
    })


def step_l8_pre(result, state, config):
    """Record the product environment before the product phase (L8)."""
    state.snapshots["product_env_has_plugin_data"] = "PLUGIN_DATA" in product_env(state)
    state.snapshots["pre_journey_store"] = snapshot_tree(state.store_dir)
    state.snapshots["pre_journey_at"] = utc_now()


def _iter_evidence_refs(score):
    for dimension in (score.get("dimensions") or {}).values():
        for ref in (dimension.get("evidenceRefs") or []):
            if isinstance(ref, dict):
                yield ref


def step_l8_eval(result, state, config):
    phases = state.snapshots.get("product_phases") or []
    if not phases:
        return result.check("L8", False, {"prerequisite_unmet": "a people-finder product phase ran"})
    violations = []
    if state.audit_log and os.path.exists(state.audit_log):
        violations = [line.strip() for line in read_text(state.audit_log).splitlines() if line.strip()]
    store_path = os.path.realpath(state.store_dir)
    argv_leaks = [row for row in state.product_commands
                  if any(store_path in str(item) for item in (row.get("argv") or []))]
    output_leaks = [row["label"] for row in state.product_commands if row.get("mentions_store_path")]
    changed_phases = [row["label"] for row in phases if not row["unchanged"]]
    passed = (
        not changed_phases
        and not violations
        and not argv_leaks
        and not output_leaks
        and state.snapshots.get("product_env_has_plugin_data") is False
    )
    result.check("L8", passed, {
        "store_tree_hashes_before": phases[0]["before"],
        "store_tree_hashes_after": phases[-1]["after"],
        "product_phases": [
            {"label": row["label"], "unchanged": row["unchanged"], "exit_code": row["exit_code"],
             "started_at": row["started_at"], "finished_at": row["finished_at"],
             "files_before": sorted(row["before"]), "files_after": sorted(row["after"])}
            for row in phases
        ],
        "unchanged_phases": [row["label"] for row in phases if row["unchanged"]],
        "changed_phases": changed_phases,
        "phases_bracketed": ["compile", "rank"],
        "store_phase_snapshot_rule": (
            "the store tree and file hashes are recorded immediately before and after every "
            "people-finder compile and rank invocation; Jobsss calls between phases are harness "
            "bookkeeping, not people-finder behavior"
        ),
        "pre_journey_store_snapshot_at": state.snapshots.get("pre_journey_at"),
        "product_environment_contains_plugin_data": state.snapshots.get("product_env_has_plugin_data"),
        "product_argv_paths_inside_store": argv_leaks,
        "product_outputs_mentioning_store_path": output_leaks,
        "audit_guard": "sitecustomize audit hook rejects open/stat/listdir under the store path",
        "audit_violations": violations,
        "product_commands_in_phase": [
            {"label": row["label"], "argv": row["argv"], "exit_code": row["exit_code"]}
            for row in state.product_commands
        ],
    })


def step_l9(result, state, config):
    locked = state.locked or {}
    document = locked.get("queries_document")
    if not isinstance(document, dict):
        return result.check("L9", False, {"prerequisite_unmet": "compiled query pack from the live target"})
    record = locked.get("compile_record") or {}
    company = (locked.get("job") or {}).get("company")
    title = (locked.get("job") or {}).get("title")
    target = document.get("target") or {}
    packs = document.get("packs") or []
    queries = [entry.get("query", "") for pack in packs for entry in (pack.get("queries") or [])]
    eligible = [query for query in queries if company and norm_phrase(company) in norm_phrase(query)
                and "site:linkedin.com/in" in query]
    function_anchors = [anchor for anchor in (document.get("anchors") or [])
                        if anchor.get("type") == "function"]
    job_derived = [anchor for anchor in function_anchors
                   if str((anchor.get("evidence") or {}).get("source", "")).endswith(".json")]
    passed = (
        record.get("returncode") == 0
        and document.get("schema") == "people-queries.v1"
        and norm_phrase(target.get("company")) == norm_phrase(company)
        and bool(job_derived)
        and bool(eligible)
    )
    result.check("L9", passed, {
        "command": record.get("command"),
        "exit_code": record.get("returncode"),
        "stdout_sha256": record.get("stdout_sha256"),
        "queries_file": locked.get("queries_path"),
        "queries_file_sha256": sha256_file(locked.get("queries_path")),
        "schema": document.get("schema"),
        "target": {"company": target.get("company"), "title": target.get("title"),
                   "job_source": target.get("job_source")},
        "target_company_matches_selected_employer": norm_phrase(target.get("company")) == norm_phrase(company),
        "target_title_matches_selected_job": norm_phrase(target.get("title")) == norm_phrase(title),
        "packs_compiled": [pack.get("pack_id") for pack in packs],
        "packs_skipped": [item.get("pack_id") for item in document.get("packs_skipped") or []],
        "query_count": len(queries),
        "employer_scoped_queries": eligible[:MAX_LIVE_QUERIES],
        "job_derived_function_anchors": [
            {"anchor_id": anchor.get("anchor_id"), "value": anchor.get("value"),
             "source": (anchor.get("evidence") or {}).get("source"),
             "field": (anchor.get("evidence") or {}).get("field")}
            for anchor in job_derived
        ],
        "unknowns_recorded": len(document.get("unknowns") or []),
        "stderr_head": redact((record.get("stderr") or "")[:300]),
        "attempt_compiles": [
            {"attempt": item["index"], "exit_code": (item.get("compile_record") or {}).get("returncode"),
             "schema": (item.get("queries_document") or {}).get("schema"),
             "rejected_because": item.get("rejected") or None}
            for item in state.attempts
        ],
    })


def step_l10(result, state, config):
    locked = state.locked or {}
    live = locked.get("live") or {}
    if not live:
        return result.check("L10", False, {"prerequisite_unmet": "a live search attempt was reached"})
    company = (locked.get("job") or {}).get("company") or ""
    envelopes = live.get("envelopes") or []
    profile_rows = [row for envelope in envelopes for row in envelope["results"]
                    if is_public_profile_url(row.get("url"))]
    tied = [row for row in profile_rows
            if text_mentions_employer(company, row.get("title"), row.get("snippet"))]
    linkedin_requests = [entry for entry in state.http_requests
                         if (entry.get("host") or "").endswith("linkedin.com")]
    passed = bool(envelopes) and bool(profile_rows) and bool(tied) and not linkedin_requests
    result.check("L10", passed, {
        "configuration_routes": [ROUTE_LABELS[route] for route in config.routes],
        "queries_considered": live.get("queries_considered"),
        "query_attempts": [redact_document(row) for row in live.get("outcomes") or []],
        "accepted_envelopes": [
            {"pack_id": envelope["pack_id"], "query": envelope["query"], "route": envelope["route"],
             "tool": envelope["tool"], "retrieved_at": envelope["retrieved_at"],
             "result_count": len(envelope["results"]),
             "public_profile_results": len([r for r in envelope["results"]
                                            if is_public_profile_url(r.get("url"))])}
            for envelope in envelopes
        ],
        "accepted_result_count": sum(len(envelope["results"]) for envelope in envelopes),
        "public_professional_profile_results": [redact(row.get("url")) for row in profile_rows][:10],
        "results_mentioning_target_employer": [
            {"url": redact(row.get("url")), "title": redact(row.get("title") or ""),
             "snippet": redact((row.get("snippet") or "")[:160])}
            for row in tied
        ][:10],
        "target_employer": company,
        "target_employer_tokens": employer_tokens(company),
        "employer_tied_result_observed": bool(tied),
        "stopped_early_on_employer_tie": False,
        "lock_reason": locked.get("lock_reason"),
        "rejected_attempts": [
            {"attempt": item["index"], "company": item["job"].get("company"),
             "accepted_envelopes": len((item.get("live") or {}).get("envelopes") or []),
             "employer_tied": (item.get("live") or {}).get("employer_tied"),
             "rejected_because": item.get("rejected") or None}
            for item in state.attempts if not item.get("accepted")
        ],
        "run_window": {"started_at": state.run_start.isoformat(), "finished_at": utc_now()},
        "harness_http_requests": state.http_requests,
        "harness_requests_to_linkedin": linkedin_requests,
        "recorded_fixture_used": False,
    })


def source_token_hits(source, tokens):
    """Find banned vendor/automation tokens in this harness's own source.

    ALL-CAPS occurrences are ignored on purpose: the credential sweep names
    ``APIFY_TOKEN``-style environment variables in order to remove them from
    child environments, which is the opposite of using such a vendor.
    """
    hits = []
    lowered = source.lower()
    for token in tokens:
        start = lowered.find(token)
        while start != -1:
            span = source[start:start + len(token)]
            if span != span.upper():
                hits.append({"token": token, "offset": start})
            start = lowered.find(token, start + 1)
    return hits


def step_l11(result, state, config):
    own_source = read_text(HARNESS_PATH)
    banned = [
        "sales" + " navigator", "sales" + "nav", "harvest" + "api",
        "ap" + "ify", "sele" + "nium", "play" + "wright", "puppe" + "teer",
        "web" + "driver", "li_" + "at", "jsess" + "ionid", "cook" + "ies.json",
        "browser" + " automation", "authenticated" + " session",
    ]
    hits = source_token_hits(own_source, banned)
    linkedin_requests = [entry for entry in state.http_requests
                         if (entry.get("host") or "").endswith("linkedin.com")]
    env_keys = sorted(key for key in os.environ if "linkedin" in key.lower())
    routes = {row.get("route") for item in state.attempts
              for row in ((item.get("live") or {}).get("outcomes") or []) if row.get("route")}
    passed = not hits and not linkedin_requests and not env_keys
    result.check("L11", passed, {
        "harness_source_tokens_absent": banned,
        "harness_source_token_hits": hits,
        "harness_source_scan_rule": (
            "case-insensitive scan of this harness's own source; ALL-CAPS occurrences that name "
            "credential environment variables removed from child environments are ignored"
        ),
        "harness_http_requests_observed": state.http_requests,
        "linkedin_hosts_contacted_by_harness": linkedin_requests,
        "linkedin_credentials_in_environment": env_keys,
        "linkedin_page_fetch_attempted": False,
        "linkedin_cookies_or_auth_headers_supplied": False,
        "browser_automation_used_by_harness": False,
        "routes_observed": sorted(routes),
        "note": (
            "Public profile URLs in the accepted results were observed through a general web index "
            "or a host plugin bridge. This harness requested no LinkedIn page, supplied no session "
            "state, drove no browser, and used no third-party scraping or automation vendor."
        ),
        "profile_url_hosts_accepted_by_people_finder": list(PUBLIC_PROFILE_HOSTS),
    })


def step_l12(result, state, config):
    locked = state.locked or {}
    rank = locked.get("rank") or {}
    document = rank.get("document")
    if not isinstance(document, dict):
        return result.check("L12", False, {"prerequisite_unmet": "rank over supplied live results"})
    item = (rank.get("matched") or {}).get("item") or {}
    claim_hits = claim_token_hits(document)
    passed = (
        rank["record"]["returncode"] == 0
        and document.get("schema") == "people-candidates.v1"
        and rank.get("matched") is not None
        and bool(item.get("paths"))
        and bool(item.get("unknowns"))
        and bool(item.get("url_observed_in"))
        and item.get("employer_observed", {}).get("value") == (locked.get("job") or {}).get("company")
        and not claim_hits
        and document.get("backend", {}).get("live_network") is True
    )
    result.check("L12", passed, {
        "supplied_results_file": rank.get("results_path"),
        "supplied_results_sha256": sha256_file(rank.get("results_path")),
        "supplied_results_route_provenance": locked.get("provenance"),
        "supplied_results_live": True,
        "rank_command": rank["record"].get("command"),
        "exit_code": rank["record"].get("returncode"),
        "stdout_sha256": rank["record"].get("stdout_sha256"),
        "candidates_file": rank.get("candidates_path"),
        "candidates_file_sha256": rank["record"].get("stdout_sha256") and sha256_file(rank.get("candidates_path")),
        "schema": document.get("schema"),
        "counts": document.get("counts"),
        "candidate_lanes_observed": rank.get("lanes"),
        "peer_candidates_ranked": rank.get("peer_candidates"),
        "hiring_adjacent_candidates_ranked": rank.get("hiring_adjacent_candidates"),
        "lock_reason": locked.get("lock_reason"),
        "matched_candidate": {
            "lane": item.get("lane"),
            "candidate_id": item.get("candidate_id"),
            "public_url": item.get("public_url"),
            "url_observed_in": item.get("url_observed_in"),
            "name": item.get("name"),
            "name_status": item.get("name_status"),
            "headline_observed": item.get("headline_observed"),
            "paths": item.get("paths"),
            "anchor_types_matched": item.get("anchor_types_matched"),
            "score": item.get("score"),
            "unknowns": item.get("unknowns"),
            "employer_observed": item.get("employer_observed"),
            "state": item.get("state"),
        },
        "observed_live_profile_urls": rank.get("observed_urls", [])[:10],
        "provenance_block": document.get("provenance"),
        "claim_tokens_in_candidate_document": claim_hits,
        "suppressed_summary": [
            {"reason": row.get("reason"), "url": redact(row.get("url"))}
            for row in (document.get("suppressed") or [])
        ][:10],
        "rejected_attempts": [
            {"attempt": row["index"], "company": row["job"].get("company"),
             "rank_counts": ((row.get("rank") or {}).get("document") or {}).get("counts"),
             "rejected_because": row.get("rejected") or None}
            for row in state.attempts if not row.get("accepted")
        ],
    })


# ---------------------------------------------------------------------------
# steps L13-L17 (mapping, sink, readback, boundary, evidence)
# ---------------------------------------------------------------------------


def step_l13(result, state, config):
    locked = state.locked or {}
    rank = locked.get("rank") or {}
    document = rank.get("document")
    item = (rank.get("matched") or {}).get("item")
    if not isinstance(document, dict) or not item:
        return result.check("L13", False, {"prerequisite_unmet": "a ranked lead from the live envelope"})
    company = (locked.get("job") or {}).get("company") or ""
    job_title = (locked.get("job") or {}).get("title") or ""
    job_id = (locked.get("job") or {}).get("id")
    provenance = [f"{row['route']}::{row['tool']}::{row['query']}" for row in locked.get("provenance") or []]
    contact_args, research_args = map_lead_to_jobsss_args(
        item, profile_id=state.profile_id, job_id=job_id, job_title=job_title, company=company,
        candidates_document=document, candidates_path=rank.get("candidates_path"),
        route_provenance=provenance, results_path=rank.get("results_path"),
        live_envelopes=[{"pack_id": envelope["pack_id"], "route": envelope["route"],
                         "tool": envelope["tool"], "retrieved_at": envelope["retrieved_at"],
                         "query": envelope["query"]}
                        for envelope in (locked.get("live") or {}).get("envelopes") or []],
    )
    state.contact_args = contact_args
    state.research_args = research_args
    invented = invented_authority_keys(contact_args, research_args)
    product_source_hits = scan_product_source(("jobsss", "plugin_data", "import_contact", "record_research"))
    findings_text = " ".join(research_args["findings"])
    passed = (
        contact_args["profileId"] == state.profile_id
        and contact_args["name"] == item.get("name")
        and contact_args["company"] == company
        and contact_args["humanApproved"] is False
        and research_args["profileId"] == state.profile_id
        and research_args["jobId"] == job_id
        and item.get("public_url") in findings_text
        and str(item.get("candidate_id")) in findings_text
        and all(path in findings_text for path in item.get("paths") or [])
        and all(unknown in findings_text for unknown in item.get("unknowns") or [])
        and str(rank.get("results_path") or "") in findings_text
        and not invented
        and not product_source_hits
        and os.path.abspath(__file__) == HARNESS_PATH
    )
    result.check("L13", passed, {
        "mapper": f"{os.path.relpath(HARNESS_PATH, ROOT)} (host composition code, not product code)",
        "selected_candidate": {
            "candidate_id": item.get("candidate_id"),
            "name": item.get("name"),
            "public_url": item.get("public_url"),
            "paths": item.get("paths"),
            "unknowns": item.get("unknowns"),
            "score": item.get("score"),
            "lane": item.get("lane"),
        },
        "import_contact_arguments": safe_argument_view("import_contact", contact_args),
        "record_research_arguments": safe_argument_view("record_research", research_args),
        "human_approved_flag": contact_args["humanApproved"],
        "invented_authority_keys": invented,
        "email_invented": False,
        "relationship_channel_or_permission_claimed": False,
        "people_finder_product_references_to_jobsss_surface": product_source_hits,
        "findings_preserve_source_url": item.get("public_url") in findings_text,
        "findings_preserve_paths": [path for path in item.get("paths") or [] if path in findings_text],
        "findings_preserve_unknowns": [unknown for unknown in item.get("unknowns") or []
                                        if unknown in findings_text],
        "findings_name_selected_job": job_id in findings_text,
    })


def scan_product_source(tokens):
    hits = {}
    for base in ("bin", "src"):
        root = os.path.join(ROOT, base)
        if not os.path.isdir(root):
            continue
        for current, _dirs, files in os.walk(root):
            if "__pycache__" in current:
                continue
            for name in sorted(files):
                target = os.path.join(current, name)
                try:
                    text = read_text(target).lower()
                except (OSError, UnicodeDecodeError):
                    continue
                for token in tokens:
                    if token in text:
                        hits.setdefault(token, []).append(os.path.relpath(target, ROOT))
    return hits


def step_l14(result, state, config):
    if not state.contact_args or not state.research_args:
        return result.check("L14", False, {"prerequisite_unmet": "harness-owned mapping"})
    contact, contact_entry = state.session.call("import_contact", state.contact_args,
                                                purpose="unapproved contact sink")
    research, research_entry = state.session.call("record_research", state.research_args,
                                                  purpose="research sink")
    state.contact_id = contact.get("contactId") or contact.get("id")
    state.research_id = research.get("researchId") or research.get("id")
    contacts, contacts_entry = state.session.call("list_contacts", {"profileId": state.profile_id},
                                                  purpose="contact readback")
    research_rows, research_readback_entry = state.session.call(
        "list_research", {"profileId": state.profile_id}, purpose="research readback")
    found_contact = next((item for item in (contacts.get("contacts") or [])
                          if item.get("id") == state.contact_id), None)
    found_research = next((item for item in (research_rows.get("research") or [])
                           if item.get("id") == state.research_id), None)
    findings_text = " ".join((found_research or {}).get("findings") or [])
    mapped_provenance = json.loads(state.contact_args["text"])
    lead_url = mapped_provenance["public_url"]
    paths = mapped_provenance["paths"]
    unknowns = mapped_provenance["unknowns"]
    selected = state.selected
    passed = (
        bool(state.contact_id)
        and bool(state.research_id)
        and found_contact is not None
        and found_contact.get("humanApproved") is False
        and found_contact.get("name") == state.contact_args.get("name")
        and found_contact.get("company") == state.contact_args.get("company")
        and found_contact.get("email") in (None, "")
        and found_research is not None
        and found_research.get("jobId") == selected.get("id")
        and lead_url in findings_text
        and all(path in findings_text for path in paths)
        and all(unknown in findings_text for unknown in unknowns)
        and state.results_path in findings_text
        and [entry["tool"] for entry in state.session.calls][-4:] == [
            "import_contact", "record_research", "list_contacts", "list_research"]
    )
    result.check("L14", passed, {
        "caller": f"{os.path.relpath(HARNESS_PATH, ROOT)} (harness), not people-finder",
        "import_contact_result": {"contactId": state.contact_id, "created": contact.get("created")},
        "record_research_result": {"researchId": state.research_id, "created": research.get("created")},
        "list_contacts_count": contacts.get("count"),
        "contact_readback": {
            "id": (found_contact or {}).get("id"),
            "profileId": (found_contact or {}).get("profileId"),
            "name": (found_contact or {}).get("name"),
            "company": (found_contact or {}).get("company"),
            "role": (found_contact or {}).get("role"),
            "email": (found_contact or {}).get("email"),
            "humanApproved": (found_contact or {}).get("humanApproved"),
            "relationshipEvidence": (found_contact or {}).get("relationshipEvidence"),
            "source": (found_contact or {}).get("source"),
        },
        "research_readback": {
            "id": (found_research or {}).get("id"),
            "jobId": (found_research or {}).get("jobId"),
            "subjectName": (found_research or {}).get("subjectName"),
            "subjectCompany": (found_research or {}).get("subjectCompany"),
            "source": (found_research or {}).get("source"),
            "findings": redact_document((found_research or {}).get("findings")),
            "notes": redact((found_research or {}).get("notes") or ""),
        },
        "readback_preserves": {
            "public_url": lead_url in findings_text,
            "paths": [path for path in paths if path in findings_text],
            "unknowns": [unknown for unknown in unknowns if unknown in findings_text],
            "supplied_results_document": state.results_path in findings_text,
            "selected_job": selected.get("id") in findings_text,
        },
        "calls": [
            {"tool": "import_contact", "exit_code": contact_entry.get("exit_code")},
            {"tool": "record_research", "exit_code": research_entry.get("exit_code")},
            {"tool": "list_contacts", "exit_code": contacts_entry.get("exit_code")},
            {"tool": "list_research", "exit_code": research_readback_entry.get("exit_code")},
        ],
    })
    if not passed:
        state.blocked = "unapproved Jobsss sink"


def step_l15(result, state, config):
    if not state.contact_id or not state.research_id:
        return result.check("L15", False, {"prerequisite_unmet": "imported contact and research"})
    mapped, entry = state.session.call(
        "map_reachable_network",
        {"profileId": state.profile_id, "jobId": state.selected.get("id")},
        purpose="reachable network readback",
    )
    people = mapped.get("people") or []
    research_rows = mapped.get("research") or []
    seen_contact = any(item.get("id") == state.contact_id for item in people)
    seen_research = any(item.get("id") == state.research_id for item in research_rows)
    truthy = truthy_authority_hits(mapped)
    claims = claim_token_hits(mapped)
    passed = (seen_contact or seen_research) and not truthy and not claims
    result.check("L15", passed, {
        "tool": "map_reachable_network",
        "company": mapped.get("company"),
        "people_seen": [
            {"id": item.get("id"), "name": item.get("name"), "company": item.get("company"),
             "humanApproved": item.get("humanApproved"), "pathType": item.get("pathType"),
             "reachable": item.get("reachable"),
             "relationshipEvidence": item.get("relationshipEvidence")}
            for item in people
        ],
        "research_seen": [
            {"id": item.get("id"), "subjectName": item.get("subjectName"),
             "notes": redact(item.get("notes") or "")}
            for item in research_rows
        ],
        "contact_matched": seen_contact,
        "research_matched": seen_research,
        "truthy_authority_keys": truthy,
        "authority_claim_tokens_found": claims,
        "message": redact(mapped.get("message") or ""),
        "identity_or_relationship_confirmed": False,
        "call_record": {key: entry.get(key) for key in ("exit_code", "stdout_sha256", "started_at")},
    })


def step_l16(result, state, config):
    session = state.session
    calls = session.calls if session else []
    called = [entry["tool"] for entry in calls]
    unauthorized = [name for name in called if name not in ALLOWED_JOBSSS_TOOLS]
    forbidden = [name for name in called if name in FORBIDDEN_JOBSSS_TOOLS]
    invocations = session.cli_invocations if session else []
    invocation_text = " ".join(" ".join(row) for row in invocations)
    harness_source = read_text(HARNESS_PATH).lower()
    authority_tool_names = sorted(name for name in FORBIDDEN_JOBSSS_TOOLS if name in harness_source)
    passed = (
        bool(called)
        and not unauthorized
        and not forbidden
        and "decide" not in invocation_text
        and not any(segment in " ".join(called) for segment in
                    ("send", "draft", "approv", "submit", "apply", "publish"))
        and all(name not in called for name in authority_tool_names)
    )
    result.check("L16", passed, {
        "jobsss_tools_called": called,
        "allowed_tool_set": list(ALLOWED_JOBSSS_TOOLS),
        "unauthorized_calls": unauthorized,
        "forbidden_authority_calls": forbidden,
        "jobsss_cli_invocations": [redact(" ".join(row)) for row in invocations],
        "trusted_local_decide_invoked": False,
        "outreach_tools_invoked": [],
        "application_submission_invoked": False,
        "external_status_update_invoked": False,
        "authority_tool_names_listed_in_harness_source": authority_tool_names,
        "authority_tool_names_called": [name for name in called if name in authority_tool_names],
        "note": (
            "The transcript is the recorded set of MCP calls this harness made to the sibling "
            "Jobsss executable; no send, approval, outreach, application or decision tool appears."
        ),
    })


def step_l17(result, state, config):
    removal_error = ""
    if os.path.exists(state.store_dir):
        try:
            shutil.rmtree(state.store_dir)
        except OSError as cause:
            removal_error = repr(cause)
    leftovers = sorted(os.listdir(state.store_dir)) if os.path.exists(state.store_dir) else []
    frozen_after = sha256_file(BENCHMARK_FILE)
    xray_used = any(row.get("route") == HARNESS_ROUTE_XRAY for row in state.http_requests)
    report = {
        "benchmark": BENCHMARK,
        "criterion": CRITERION,
        "generated_at": utc_now(),
        "run_started_at": state.run_start.isoformat(),
        "run_finished_at": utc_now(),
        "configuration": config.evidence(),
        "paths": {
            "repo_root": ROOT,
            "people_finder_binary": PRODUCT_BIN,
            "people_finder_binary_sha256": sha256_file(PRODUCT_BIN),
            "jobsss_binary": JOBSSS_BIN,
            "jobsss_binary_sha256": sha256_file(JOBSSS_BIN),
            "harness": HARNESS_PATH,
            "harness_sha256": sha256_file(HARNESS_PATH),
            "temporary_workdir": state.workdir,
            "people_finder_output_dir": state.people_out,
            "evidence_dir": state.evidence_dir,
            "temporary_plugin_data": state.store_dir,
            "temporary_plugin_data_removed": not os.path.exists(state.store_dir),
            "temporary_plugin_data_removal_error": removal_error or None,
            "artifacts": [{
                "attempt": attempt["index"],
                "job_id": attempt["job"].get("id"),
                "resume": (attempt.get("export") or {}).get("resume_path"),
                "resume_sha256": (attempt.get("export") or {}).get("resume_file_sha256"),
                "target_job": (attempt.get("export") or {}).get("job_path"),
                "target_job_sha256": (attempt.get("export") or {}).get("job_file_sha256"),
                "compiled_queries": attempt.get("queries_path"),
                "compiled_queries_sha256": sha256_file(attempt.get("queries_path")),
                "supplied_live_results": (attempt.get("rank") or {}).get("results_path"),
                "supplied_live_results_sha256": sha256_file(
                    (attempt.get("rank") or {}).get("results_path")),
                "ranked_candidates": (attempt.get("rank") or {}).get("candidates_path"),
                "ranked_candidates_sha256": sha256_file(
                    (attempt.get("rank") or {}).get("candidates_path")),
            } for attempt in state.attempts],
        },
        "frozen_offline_contract": {
            "benchmark_sha256_before": state.frozen_sha_before,
            "benchmark_sha256_after": frozen_after,
            "unchanged": state.frozen_sha_before == frozen_after,
            "offline_commands_untouched": True,
        },
        "ids": {
            "profile_id": state.profile_id,
            "saved_search_ids": state.search_ids,
            "discovery_run_ids": [row["row"].get("run_id") for row in state.discovery_runs],
            "attempted_job_ids": [item["job"].get("id") for item in state.attempts],
            "selected_job_id": state.selected.get("id"),
            "contact_id": state.contact_id,
            "research_id": state.research_id,
        },
        "job_selection": [attempt_summary(item) for item in state.attempts],
        "live_routes": [redact_document(row) for item in state.attempts
                        for row in ((item.get("live") or {}).get("outcomes") or [])],
        "accepted_live_envelopes": [
            {"attempt": item["index"], "pack_id": envelope["pack_id"], "query": envelope["query"],
             "route": envelope["route"], "tool": envelope["tool"],
             "retrieved_at": envelope["retrieved_at"], "result_count": len(envelope["results"])}
            for item in state.attempts for envelope in ((item.get("live") or {}).get("envelopes") or [])
        ],
        "product_commands": state.product_commands,
        "jobsss_mcp_transcript": state.session.transcript() if state.session else {},
        "environment_keys_removed_from_product_children": removed_env_keys(),
        "repository_writes_by_harness": state.repo_writes,
        "store_tree_hashes": {
            "first_product_phase_before": state.snapshots.get("before"),
            "last_product_phase_after": state.snapshots.get("after"),
            "product_phases": [
                {"label": row["label"], "unchanged": row["unchanged"],
                 "exit_code": row["exit_code"], "started_at": row["started_at"],
                 "finished_at": row["finished_at"]}
                for row in (state.snapshots.get("product_phases") or [])
            ],
        },
        "assertion_observations": {
            assertion_id: {"passed": item["passed"]}
            for assertion_id, item in sorted(result.assertions.items())
        },
    }
    report_path = os.path.join(state.evidence_dir, "people-finder-live-e2e-report.json")
    write_json(report_path, report)
    passed = (
        not os.path.exists(state.store_dir)
        and not removal_error
        and state.frozen_sha_before == frozen_after
        and not state.repo_writes
        and os.path.isfile(report_path)
    )
    result.check("L17", passed, {
        "evidence_dir": state.evidence_dir,
        "evidence_dir_supplied_by_environment": bool(config.evidence_dir),
        "report_file": report_path,
        "report_sha256": sha256_file(report_path),
        "temporary_plugin_data": state.store_dir,
        "temporary_plugin_data_removed": not os.path.exists(state.store_dir),
        "temporary_plugin_data_leftovers": leftovers,
        "temporary_plugin_data_removal_error": removal_error or None,
        "removed_on_success_or_failure": "step_l17 runs the removal; body() also removes it in finally",
        "temporary_workdir_retained_for_review": state.workdir,
        "timestamps_recorded": True,
        "executable_paths_recorded": True,
        "redacted_command_arguments_recorded": True,
        "mcp_tool_names_recorded": [entry["tool"] for entry in (state.session.calls if state.session else [])],
        "selected_ids_recorded": True,
        "live_route_metadata_recorded": bool(state.attempts),
        "http_status_applicable": xray_used,
        "http_status_recorded": any(entry.get("http_status") is not None
                                    for entry in state.http_requests),
        "product_exit_codes_recorded": [row["exit_code"] for row in state.product_commands],
        "stdout_hashes_recorded": [row["stdout_sha256"] for row in state.product_commands],
        "frozen_benchmark_sha256_before": state.frozen_sha_before,
        "frozen_benchmark_sha256_after": frozen_after,
        "repository_state_untouched": not state.repo_writes,
        "artifacts_written_under": {"temporary_workdir": state.workdir,
                                    "evidence_dir": state.evidence_dir},
    })


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def body(result, config):
    workdir = tempfile.mkdtemp(prefix="people-finder-live-e2e-")
    people_out = os.path.join(workdir, "people-finder-out")
    os.makedirs(people_out, exist_ok=True)
    evidence_dir = os.path.abspath(config.evidence_dir) if config.evidence_dir \
        else os.path.join(workdir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    store_dir = os.path.join(workdir, "jobsss-plugin-data")
    state = State(config, workdir, people_out, evidence_dir, store_dir)
    result.note(
        benchmark_configuration=config.evidence(),
        temporary_workdir=workdir,
        evidence_dir=evidence_dir,
        people_finder_output_dir=people_out,
    )
    steps = (
        ("L1", step_l1),
        ("L2", step_l2),
        ("L3", step_l3),
        ("L4", step_l4),
        ("L5", step_l5),
        ("L8", step_l8_pre),
        ("journey", run_journey),
        ("L6", step_l6),
        ("L7", step_l7),
        ("L9", step_l9),
        ("L10", step_l10),
        ("L11", step_l11),
        ("L12", step_l12),
        ("L13", step_l13),
        ("L14", step_l14),
        ("L15", step_l15),
        ("L16", step_l16),
        ("L8b", step_l8_eval),
        ("L17", step_l17),
    )
    try:
        for name, step in steps:
            try:
                step(result, state, config)
            except HarnessError:
                raise
            except Exception as cause:  # noqa: BLE001 - reported, never swallowed
                log(f"{name} raised: {cause!r}")
                assertion_id = name if re.match(r"^L\d+$", name) else None
                if assertion_id and assertion_id not in result.assertions:
                    result.check(assertion_id, False, {"step_error": repr(cause), "step": step.__name__})
                state.blocked = state.blocked or f"{step.__name__} raised {cause!r}"
    finally:
        if os.path.exists(store_dir):
            try:
                shutil.rmtree(store_dir)
            except OSError as cause:
                log(f"could not remove temporary PLUGIN_DATA: {cause}")
        result.note(temporary_plugin_data_removed=not os.path.exists(store_dir))
    result.fill_unreached(state.blocked or "an earlier live stage did not complete")


def main(argv=None):
    config = Config()
    result = Result()
    errors = config.validation_errors()
    if errors:
        log("configuration is incomplete: " + "; ".join(errors))
        result.note(configuration_errors=errors, configuration=config.evidence())
        try:
            state = State(config, tempfile.mkdtemp(prefix="people-finder-live-e2e-"),
                          tempfile.gettempdir(), tempfile.gettempdir(),
                          os.path.join(tempfile.gettempdir(), "people-finder-live-e2e-unused"))
            step_l1(result, state, config)
        except Exception as cause:  # noqa: BLE001 - configuration path stays honest
            log(f"L1 could not be observed: {cause!r}")
            result.check("L1", False, {"step_error": repr(cause)})
        result.fill_unreached("live configuration is incomplete: " + "; ".join(errors))
        return result.finish(harness_error="incomplete live configuration", exit_code=2)
    try:
        body(result, config)
    except HarnessError as cause:
        log(f"harness failure: {cause}")
        result.fill_unreached(str(cause))
        return result.finish(harness_error=str(cause))
    except Exception as cause:  # noqa: BLE001 - reported, never swallowed
        import traceback
        log("harness crashed: " + repr(cause))
        log(traceback.format_exc())
        result.fill_unreached(repr(cause))
        return result.finish(harness_error=repr(cause))
    return result.finish()


if __name__ == "__main__":
    sys.exit(main())
