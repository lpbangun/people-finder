"""stdio JSON-RPC surface.

Advertises compile_people_queries, rank_people_candidates and
start_people_research. Both compile and rank accept supplied content directly,
need no credential and never dispatch to a network backend. stdout carries
JSON-RPC only; diagnostics go to stderr.
"""

import json
import sys

from . import PRODUCT, PRODUCT_VERSION, SCHEMA_QUERIES
from .anchors import AnchorError, load_job_card, read_text_file
from .packs import compile_packs
from .rank import rank_candidates
from .results import ResultError, load_document, load_supplied_results
from .textutil import squeeze

PROTOCOL_VERSION = "2024-11-05"

RESUME_PROPS = {
    "resume_text": {"type": "string", "description": "supplied resume content"},
    "resume_path": {"type": "string", "description": "local path to supplied resume content"},
}
JOB_PROPS = {
    "job": {"type": "object", "description": "supplied target job card"},
    "job_path": {"type": "string", "description": "local path to the supplied job card"},
}

TOOLS = [
    {
        "name": "compile_people_queries",
        "description": (
            "Compile typed-anchor query packs from supplied resume content and one target "
            "job card. Offline and credential-free."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **RESUME_PROPS,
                **JOB_PROPS,
                "resume_source": {"type": "string", "description": "label for supplied resume content"},
                "job_source": {"type": "string", "description": "label for the supplied job card"},
                "generated_at": {"type": "string", "description": "optional fixed retrieval timestamp"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "rank_people_candidates",
        "description": (
            "Rank supplied search results against compiled query packs and emit "
            "people-candidates.v1. Offline and credential-free; no search backend required."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "queries": {"type": "object", "description": "compiled people-queries.v1 document"},
                "queries_path": {"type": "string"},
                "results": {"type": "object", "description": "supplied recorded results document"},
                "results_path": {"type": "string"},
                "generated_at": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "start_people_research",
        "description": (
            "Describe the cross-plugin discovery intent for one target job and, when inputs "
            "are supplied, compile the packs it would use. Performs no network call and writes no state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **RESUME_PROPS,
                **JOB_PROPS,
                "resume_source": {"type": "string"},
                "job_source": {"type": "string"},
                "generated_at": {"type": "string"},
                "subject": {"type": "string", "description": "optional human-selected subject name"},
                "notes": {"type": "string"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]


class McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _require(args, *names):
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and squeeze(value):
            return value
    return None


def _resume_and_job(args):
    resume_text = args.get("resume_text")
    resume_path = args.get("resume_path")
    if not isinstance(resume_text, str) or not resume_text.strip():
        resume_text = read_text_file(resume_path or "", "resume")
    job = args.get("job")
    job_path = args.get("job_path")
    if not isinstance(job, dict):
        job = load_job_card(job_path or "")
    resume_label = args.get("resume_source") or resume_path or "<supplied resume>"
    job_label = args.get("job_source") or job_path or "<supplied job card>"
    return resume_text, job, resume_label, job_label


def call_tool(name, args):
    if not isinstance(args, dict):
        raise McpError(-32602, f"arguments for {name} must be an object")
    if name == "compile_people_queries":
        resume_text, job, resume_source, job_source = _resume_and_job(args)
        return compile_packs(
            resume_text, job, resume_source=resume_source, job_source=job_source,
            generated_at=args.get("generated_at"),
        )
    if name == "rank_people_candidates":
        queries = args.get("queries")
        if not isinstance(queries, dict):
            queries = load_document(args.get("queries_path") or "", "compiled queries", SCHEMA_QUERIES)
        results = args.get("results")
        if not isinstance(results, dict):
            results = load_supplied_results(args.get("results_path") or "")
        else:
            results = load_supplied_results(results)
        return rank_candidates(
            queries, results,
            queries_source=args.get("queries_path") or "<supplied queries>",
            results_source=args.get("results_path") or "<supplied results>",
            generated_at=args.get("generated_at"),
        )
    if name == "start_people_research":
        document = {
            "schema": "people-research-intent.v1",
            "intent": "start_people_research",
            "stage": "discovery",
            "target_job_required": True,
            "inputs_required": [
                "supplied resume content (text or local path)",
                "one target job card (object or local path)",
                "host-supplied or recorded search results for the compiled packs",
            ],
            "steps": [
                "compile_people_queries",
                "host supplies recorded or live search results",
                "rank_people_candidates",
                "human selects at most one lead",
                "host maps the selection forward to identity resolution",
            ],
            "performs_network": False,
            "requires_credentials": False,
            "writes_other_tool_state": False,
            "approval_required_before_any_outside_step": True,
            "handoff": "host-owned: this intent never writes or approves anything",
            "subject_hint": squeeze(args.get("subject", "")),
            "notes": squeeze(args.get("notes", "")),
            "boundary": {
                "stage": "discovery only",
                "note": (
                    "A public /in/ URL is a lead, not a contact, not a verified identity "
                    "and not a delivery route."
                ),
            },
        }
        has_resume = bool(_require(args, "resume_text", "resume_path"))
        has_job = isinstance(args.get("job"), dict) or bool(_require(args, "job_path"))
        if has_resume and has_job:
            try:
                resume_text, job, resume_source, job_source = _resume_and_job(args)
                compiled = compile_packs(
                    resume_text, job, resume_source=resume_source, job_source=job_source,
                    generated_at=args.get("generated_at"),
                )
                document["compiled_preview"] = {
                    "pack_ids": [pack["pack_id"] for pack in compiled["packs"]],
                    "packs_skipped": [item["pack_id"] for item in compiled["packs_skipped"]],
                    "anchor_count": len(compiled["anchors"]),
                    "anchor_unknown_count": len(compiled["unknowns"]),
                }
            except (AnchorError, ResultError) as cause:
                document["compiled_preview_error"] = str(cause)
        return document
    raise McpError(-32602, f"unknown tool: {name}")


def _respond(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _tool_result(value):
    return {
        "content": [{"type": "text", "text": json.dumps(value, indent=2)}],
        "isError": False,
    }


def _tool_failure(message):
    return {
        "content": [{"type": "text", "text": json.dumps({"ok": False, "error": message})}],
        "isError": True,
    }


def handle_message(message):
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    has_id = "id" in message
    identifier = message.get("id")
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32600, "message": "Invalid Request"}}
    if not has_id:
        # Notifications are never dispatched and never mutate anything.
        return None
    try:
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": identifier, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": PRODUCT, "version": PRODUCT_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            }}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": identifier, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": identifier, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                raise McpError(-32602, "tools/call requires a tool name")
            name = params["name"]
            if name not in {tool["name"] for tool in TOOLS}:
                raise McpError(-32602, f"unknown tool: {name}")
            value = call_tool(name, params.get("arguments", {}))
            return {"jsonrpc": "2.0", "id": identifier, "result": _tool_result(value)}
        return {"jsonrpc": "2.0", "id": identifier,
                "error": {"code": -32601, "message": "Method not found"}}
    except McpError as cause:
        return {"jsonrpc": "2.0", "id": identifier,
                "error": {"code": cause.code, "message": cause.message}}
    except (ResultError, AnchorError) as cause:
        return {"jsonrpc": "2.0", "id": identifier, "result": _tool_failure(str(cause))}
    except OSError as cause:
        return {"jsonrpc": "2.0", "id": identifier, "result": _tool_failure(str(cause))}


def _iter_messages(stream):
    while True:
        header = stream.readline()
        if not header:
            return
        if header.lower().startswith(b"content-length:"):
            try:
                length = int(header.split(b":", 1)[1].strip())
            except (IndexError, ValueError):
                return
            while True:
                blank = stream.readline()
                if not blank or blank in (b"\r\n", b"\n"):
                    break
            body = stream.read(length)
            if body:
                yield body
            continue
        line = header.strip()
        if line:
            yield line


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    for raw in _iter_messages(stdin):
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as cause:
            _respond({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": f"Parse error: {cause}"}})
            continue
        response = handle_message(message)
        if response is not None:
            _respond(response)
    return 0


def main(argv=None):
    if argv:
        print("mcp takes no arguments", file=sys.stderr)
        return 2
    return serve()


__all__ = ["TOOLS", "serve", "main", "handle_message", "call_tool", "PROTOCOL_VERSION"]
