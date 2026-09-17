"""Loading and validation of supplied result documents.

Everything the ranker consumes is supplied by the caller: recorded SERP files,
or a recorded provider envelope. This module refuses malformed input with a
typed error so the CLI can exit 2 for a malformed fixture, and it never opens a
network endpoint.
"""

import json
import os
import re

from . import SCHEMA_SERP
from .textutil import is_public_profile_url, squeeze

DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)

EXA_STATUSES = ("completed", "no_result", "unavailable")

BANNED_ENVELOPE_KEYS = ("api_key", "apikey", "cookie", "cookies", "session_token",
                        "authorization", "bearer")


class ResultError(ValueError):
    """Malformed supplied input -> exit code 2."""


def _read_json(path, label):
    if not path or not os.path.isfile(path):
        raise ResultError(f"{label} not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as cause:
        raise ResultError(f"{label} is not readable JSON: {path} ({cause})") from cause


def load_supplied_results(source):
    """Load a recorded SERP document from a path or accept an in-memory object."""
    document = _read_json(source, "supplied results") if isinstance(source, str) else source
    if not isinstance(document, dict):
        raise ResultError("supplied results must be a JSON object")
    schema = document.get("schema")
    if schema != SCHEMA_SERP:
        raise ResultError(f"supplied results must declare schema '{SCHEMA_SERP}' (got {schema!r})")
    pack_results = document.get("pack_results")
    if not isinstance(pack_results, list) or not pack_results:
        raise ResultError("supplied results must contain a non-empty 'pack_results' array")
    cleaned = []
    for index, entry in enumerate(pack_results):
        if not isinstance(entry, dict):
            raise ResultError(f"pack_results[{index}] must be an object")
        pack_id = squeeze(entry.get("pack_id", ""))
        if not pack_id:
            raise ResultError(f"pack_results[{index}] is missing 'pack_id'")
        results = entry.get("results")
        if not isinstance(results, list):
            raise ResultError(f"pack_results[{index}].results must be an array")
        rows = []
        for row_index, row in enumerate(results):
            if not isinstance(row, dict):
                raise ResultError(f"pack_results[{index}].results[{row_index}] must be an object")
            url = squeeze(row.get("url", ""))
            if not url:
                raise ResultError(f"pack_results[{index}].results[{row_index}] is missing 'url'")
            rows.append({
                "url": url,
                "title": squeeze(row.get("title", "")),
                "snippet": squeeze(row.get("snippet", "")),
                "rank_in_pack": row.get("rank", row_index + 1),
                "position": f"pack_results[{index}].results[{row_index}]",
                # Optional host-supplied provenance is preserved; old fixtures
                # remain valid because these fields default to empty strings.
                "source_url": squeeze(row.get("source_url", "")),
                "observed_at": squeeze(row.get("observed_at", "") or row.get("fetched_at", "")),
            })
        cleaned.append({
            "pack_id": pack_id,
            "query": squeeze(entry.get("query", "")),
            "retrieved_at": squeeze(entry.get("retrieved_at", "")),
            "results": rows,
        })
    return {
        "schema": SCHEMA_SERP,
        "fixture": squeeze(document.get("fixture", "")),
        "backend": squeeze(document.get("backend", "recorded_fixture")),
        "retrieved_at": squeeze(document.get("retrieved_at", "")),
        "live_network": bool(document.get("live_network", False)),
        "pack_results": cleaned,
        "document": document,
    }


def load_exa_envelope(source):
    """Validate a recorded provider envelope (contact-brief style, lead variant).

    Accepts at most one people lead. Any contradiction between status and result
    data, a non-null lead without source attribution, more than one lead, or a
    credential-like key raises ResultError.
    """
    document = _read_json(source, "provider envelope") if isinstance(source, str) else source
    if not isinstance(document, dict):
        raise ResultError("provider envelope must be a JSON object")
    for key in BANNED_ENVELOPE_KEYS:
        if key in {str(k).lower() for k in document}:
            raise ResultError(f"provider envelope must not carry credential-like key '{key}'")

    route = squeeze(document.get("route", ""))
    tool = squeeze(document.get("tool", ""))
    retrieved_at = squeeze(document.get("retrieved_at", ""))
    status = squeeze(document.get("status", ""))
    subject = document.get("subject")

    if not route:
        raise ResultError("provider envelope is missing 'route'")
    if not tool:
        raise ResultError("provider envelope is missing the actual tool name or alias")
    if not DATETIME_RE.match(retrieved_at):
        raise ResultError(f"provider envelope 'retrieved_at' must be a timestamp (got {retrieved_at!r})")
    if status not in EXA_STATUSES:
        raise ResultError(f"provider envelope 'status' must be one of {EXA_STATUSES} (got {status!r})")
    if not isinstance(subject, dict):
        raise ResultError("provider envelope is missing 'subject'")
    for field in ("name", "company"):
        if not squeeze(subject.get(field, "")):
            raise ResultError(f"provider envelope subject is missing '{field}'")

    if "leads" in document:
        leads = document.get("leads")
        if not isinstance(leads, list):
            raise ResultError("provider envelope 'leads' must be an array when present")
        if len(leads) > 1:
            raise ResultError(
                f"provider envelope carries {len(leads)} leads; at most one people lead is accepted"
            )
        lead = leads[0] if leads else None
    else:
        lead = document.get("lead", None)

    if isinstance(lead, list):
        raise ResultError("provider envelope 'lead' must be a single object or null")

    if status == "completed":
        if lead is None:
            raise ResultError("provider envelope status 'completed' contradicts a null lead")
        if not isinstance(lead, dict):
            raise ResultError("provider envelope 'lead' must be an object or null")
        url = squeeze(lead.get("url", ""))
        if not is_public_profile_url(url):
            raise ResultError("provider envelope lead must carry a public profile URL")
        sources = lead.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ResultError("a non-null lead requires at least one cited source record")
        for index, source_row in enumerate(sources):
            if not isinstance(source_row, dict):
                raise ResultError(f"lead.sources[{index}] must be an object")
            for field in ("url", "fetched_at", "quote"):
                if not squeeze(source_row.get(field, "")):
                    raise ResultError(f"lead.sources[{index}] is missing '{field}'")
            if not squeeze(source_row.get("url", "")).startswith(("http://", "https://")):
                raise ResultError(f"lead.sources[{index}].url must be an http(s) URL")
            if not DATETIME_RE.match(squeeze(source_row.get("fetched_at", ""))):
                raise ResultError(f"lead.sources[{index}].fetched_at must be a timestamp")
    else:
        if lead is not None:
            raise ResultError(
                f"provider envelope status '{status}' contradicts a non-null lead"
            )

    return {
        "route": route,
        "tool": tool,
        "retrieved_at": retrieved_at,
        "status": status,
        "subject": {"name": squeeze(subject.get("name")), "company": squeeze(subject.get("company"))},
        "lead": lead,
        "lead_count": 1 if lead is not None else 0,
        "document": document,
    }


def exa_import_key(envelope):
    """Stable idempotency key for one recorded provider handoff."""
    lead = envelope.get("lead") or {}
    parts = [
        envelope["route"],
        envelope["tool"],
        envelope["retrieved_at"],
        envelope["status"],
        envelope["subject"]["name"],
        envelope["subject"]["company"],
        squeeze(lead.get("url", "")),
    ]
    return "|".join(parts)


def load_document(source, label, schema=None):
    """Generic loader used by the interfaces layer for in-memory CLI/MCP parity."""
    document = _read_json(source, label) if isinstance(source, str) else source
    if not isinstance(document, dict):
        raise ResultError(f"{label} must be a JSON object")
    if schema and document.get("schema") != schema:
        raise ResultError(f"{label} must declare schema '{schema}' (got {document.get('schema')!r})")
    return document


__all__ = [
    "ResultError",
    "load_supplied_results",
    "load_exa_envelope",
    "load_document",
    "exa_import_key",
    "EXA_STATUSES",
]
