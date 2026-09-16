"""Recorded provider-envelope import.

Consumes zero-or-one recorded provider results (a contact-brief- style normalized
envelope) for recall only. It never reads a provider key, never contacts a
provider, never merges provider output into identity, and never duplicates a
lead on a repeated import.
"""

from . import SCHEMA_CANDIDATES
from .results import ResultError, exa_import_key
from .schema import validate_candidates
from .textutil import normalize_public_url, squeeze

EXA_UNKNOWNS = (
    "identity_not_established",
    "linkedin_edge_not_observed",
    "contactability_not_established",
    "email_not_sought",
    "path_evidence_not_observed_by_compiled_packs",
    "provider_recall_not_identity",
)

EXA_NOTE = (
    "Provider recall only: the lead was returned by a recorded provider result, "
    "not by a compiled pack or typed-anchor match. Discovery evidence only."
)


def _candidate_id(url_key):
    import hashlib
    return "cand_" + hashlib.sha1(url_key.encode("utf-8")).hexdigest()[:12]


def _import_record(envelope, *, added_ids, merged_ids, envelope_source):
    lead = envelope.get("lead") or {}
    source_urls = []
    if isinstance(lead.get("sources"), list):
        source_urls = sorted({squeeze(row.get("url", "")) for row in lead["sources"] if squeeze(row.get("url", ""))})
    return {
        "import_key": exa_import_key(envelope),
        "route": envelope["route"],
        "tool": envelope["tool"],
        "retrieved_at": envelope["retrieved_at"],
        "status": envelope["status"],
        "subject": envelope["subject"],
        "lead_count": envelope["lead_count"],
        "cited_source_urls": source_urls,
        "envelope_source": envelope_source,
        "added_candidate_ids": sorted(added_ids),
        "merged_candidate_ids": sorted(merged_ids),
        "evidence_kind": "recorded_provider_result",
        "state": {
            "stage": "discovery",
            "identity": "not_established",
            "approval": "not_requested",
            "reachability": "not_established",
            "contactability": "not_established",
        },
        "note": (
            "Recall evidence only. Provider output is not identity, not approval and "
            "not a delivery route."
        ),
    }


def _new_candidate(envelope, envelope_source):
    lead = envelope["lead"]
    url_key = normalize_public_url(lead["url"])
    sources = [
        {
            "url": squeeze(row.get("url", "")),
            "fetched_at": squeeze(row.get("fetched_at", "")),
            "published_at": row.get("published_at"),
            "quote": squeeze(row.get("quote", "")),
            "kind": squeeze(row.get("kind", "")),
        }
        for row in lead.get("sources", [])
    ]
    name = squeeze(lead.get("name", "")) or None
    return {
        "candidate_id": _candidate_id(url_key),
        "public_url": url_key,
        "url_observed_in": [f"{envelope_source}#lead.url"],
        "name": name,
        "name_status": "provider_reported_not_verified" if name else "name_not_observed",
        "headline_observed": squeeze(lead.get("headline", "")),
        "headlines_observed": [squeeze(lead.get("headline", ""))] if squeeze(lead.get("headline", "")) else [],
        "employer_observed": {
            "value": envelope["subject"]["company"],
            "status": "provider_reported_subject_company",
            "observed_in": ["provider_envelope:subject.company"],
            "evidence": [{
                "field": "provider_envelope:subject.company",
                "quote": envelope["subject"]["company"],
            }],
        },
        "lane": "peer",
        "paths": [],
        "score": 0.0,
        "score_breakdown": [],
        "per_path_totals": {},
        "anchors_matched": [],
        "anchor_types_matched": [],
        "unknowns": sorted(set(EXA_UNKNOWNS) | (
            set() if name else {"name_not_observed"}
        )),
        "state": {
            "stage": "discovery",
            "identity": "not_established",
            "approval": "not_requested",
            "reachability": "not_established",
            "contactability": "not_established",
        },
        "recall_source": {
            "kind": "recorded_provider_result",
            "route": envelope["route"],
            "tool": envelope["tool"],
            "retrieved_at": envelope["retrieved_at"],
            "status": envelope["status"],
            "subject": envelope["subject"],
            "sources": sources,
            "provider_output": True,
            "recall_only": True,
        },
        "notes": [
            EXA_NOTE,
            "Public /in/ URL from a recorded provider result. This is a discovery lead: "
            "not a verified identity, not a contact record and not a delivery route.",
        ],
    }


def _merge_into_candidate(candidate, envelope, envelope_source):
    lead = envelope["lead"]
    sources = [
        {
            "url": squeeze(row.get("url", "")),
            "fetched_at": squeeze(row.get("fetched_at", "")),
            "published_at": row.get("published_at"),
            "quote": squeeze(row.get("quote", "")),
            "kind": squeeze(row.get("kind", "")),
        }
        for row in lead.get("sources", [])
    ]
    recall = list(candidate.get("exa_recall", []))
    recall.append({
        "kind": "recorded_provider_result",
        "route": envelope["route"],
        "tool": envelope["tool"],
        "retrieved_at": envelope["retrieved_at"],
        "status": envelope["status"],
        "subject": envelope["subject"],
        "sources": sources,
        "provider_output": True,
        "recall_only": True,
        "envelope_source": envelope_source,
    })
    candidate["exa_recall"] = recall
    unknowns = set(candidate.get("unknowns", []))
    unknowns.update({"provider_recall_not_identity", "identity_not_established"})
    candidate["unknowns"] = sorted(unknowns)
    notes = list(candidate.get("notes", []))
    if EXA_NOTE not in notes:
        notes.append(EXA_NOTE)
    candidate["notes"] = notes
    return candidate


def import_exa(candidates, envelope, *, candidates_source="<supplied candidates>"):
    """Merge one recorded provider envelope into a candidate document."""
    if not isinstance(candidates, dict) or candidates.get("schema") != SCHEMA_CANDIDATES:
        raise ResultError(f"candidate document must declare schema '{SCHEMA_CANDIDATES}'")
    document = _deep_copy(candidates)

    existing = document.get("exa_imports") or []
    keys = {item.get("import_key") for item in existing}
    key = exa_import_key(envelope)
    if key in keys:
        # Idempotent repeat: the same recorded handoff is never applied twice.
        document["exa_imports"] = sorted(existing, key=lambda item: item.get("import_key", ""))
        return document, {"applied": False, "reason": "already_imported", "import_key": key}

    added_ids = []
    merged_ids = []
    if envelope["status"] == "completed" and envelope["lead"] is not None:
        url_key = normalize_public_url(envelope["lead"]["url"])
        target = None
        for item in document.get("candidates", []):
            if item.get("public_url") == url_key:
                target = item
                break
        if target is None:
            new_candidate = _new_candidate(envelope, candidates_source)
            document.setdefault("candidates", []).append(new_candidate)
            document["candidates"].sort(key=lambda item: (-item.get("score", 0), item.get("candidate_id", "")))
            added_ids.append(new_candidate["candidate_id"])
        else:
            _merge_into_candidate(target, envelope, candidates_source)
            merged_ids.append(target["candidate_id"])

    document["exa_imports"] = sorted(
        list(existing) + [_import_record(envelope, added_ids=added_ids, merged_ids=merged_ids,
                                         envelope_source=candidates_source)],
        key=lambda item: item.get("import_key", ""),
    )

    counts = dict(document.get("counts") or {})
    counts["peers"] = len(document.get("candidates", []))
    counts["hiring_adjacent"] = len(document.get("hiring_adjacent", []))
    counts["exa_imports"] = len(document["exa_imports"])
    counts["exa_leads_added"] = sum(len(item["added_candidate_ids"]) for item in document["exa_imports"])
    document["counts"] = counts

    recall = dict(document.get("recall") or {})
    recall["provider_envelopes_consumed"] = len(document["exa_imports"])
    recall["provider_output_is_recall_only"] = True
    recall["requires_provider_key"] = False
    recall["live_provider_calls"] = 0
    document["recall"] = recall

    boundary = dict(document.get("boundary") or {})
    boundary["note"] = (
        "Ranking is local scoring over supplied results, and recorded provider recall "
        "adds leads only. Nothing is transmitted, approved or imported elsewhere."
    )
    document["boundary"] = boundary

    errors = validate_candidates(document)
    if errors:
        raise ResultError("import produced an invalid candidate document: " + "; ".join(errors))
    return document, {"applied": True, "reason": "imported", "import_key": key,
                      "added_candidate_ids": added_ids, "merged_candidate_ids": merged_ids}


def _deep_copy(value):
    import json
    return json.loads(json.dumps(value))


__all__ = ["import_exa", "EXA_UNKNOWNS", "EXA_NOTE"]
