"""Structural and semantic validation of emitted documents.

Every write path validates its own output before returning it, so a malformed
document can never be handed to a caller. The `validate` CLI command exposes the
same checks as machine-readable evidence.
"""

import json
import os
import re

from . import SCHEMA_CANDIDATES, SCHEMA_QUERIES, SCHEMA_SERP
from .packs import PACK_INDEX
from .textutil import is_public_profile_url, normalize_public_url, squeeze

# Phrases that would assert a relationship, a graph edge or an authority the
# product does not hold. They must never appear in emitted output.
CLAIM_TOKENS = (
    "connected",
    "connection",
    "second_degree",
    "second-degree",
    "second degree",
    "2nd_degree",
    "2nd-degree",
    "2nd degree",
    "1st-degree",
    "first-degree",
    "third-degree",
    "3rd-degree",
    "warm_intro",
)

# Search-result prose is evidence, not a product assertion. Public indexes
# routinely put ordinary words such as "connections", "reachable", or
# "contactable" in a title/snippet. Keep the claim scanner strict for
# generated fields while allowing strings that are explicitly carried as
# observed query/source evidence. Key validation remains unconditional below.
OBSERVED_EVIDENCE_PATH_PARTS = frozenset({
    "title", "snippet", "query", "source", "source_url", "url",
    "headline_observed", "headlines_observed", "quote", "observed_at",
    "retrieved_at", "fetched_at", "url_observed_in",
})

# Keys that must never be truthy on a candidate: the product holds no such fact.
FORBIDDEN_TRUTHY_KEYS = (
    "identity_confirmed",
    "identity_established",
    "approved",
    "human_approved",
    "humanapproved",
    "reachable",
    "contactable",
    "connected",
    "connection",
    "second_degree",
    "delivered",
    "sent",
)


def _iter_strings(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key, f"{path}.{key}")
            yield from _iter_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _iter_truthy_keys(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_TRUTHY_KEYS and item not in (False, None, "", 0, "false", "not_established", "not_requested"):
                yield f"{path}.{key}", item
            yield from _iter_truthy_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_truthy_keys(item, f"{path}[{index}]")


def claim_token_hits(document):
    """Find ungrounded relationship claims in emitted output.

    Tokens inside explicitly observed evidence are allowed because the source
    text is being quoted, not asserted by the product. Matching uses token
    boundaries so ordinary plurals such as ``connections`` do not become a
    false positive for the semantic claim ``connection``.
    """
    hits = []
    for path, text in _iter_strings(document):
        parts = {
            part.lower()
            for part in path.replace("[", ".").replace("]", "").split(".")
            if part
        }
        if parts.intersection(OBSERVED_EVIDENCE_PATH_PARTS):
            continue
        lowered = text.lower()
        for token in CLAIM_TOKENS:
            pattern = r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])"
            if re.search(pattern, lowered):
                hits.append({"path": path, "token": token, "value": text})
    return hits


def forbidden_truthy_hits(document):
    return [{"path": path, "value": value} for path, value in _iter_truthy_keys(document)]


def validate_candidates(document):
    errors = []
    if document.get("schema") != SCHEMA_CANDIDATES:
        errors.append(f"schema must be '{SCHEMA_CANDIDATES}'")
    for lane_key in ("candidates", "hiring_adjacent"):
        if not isinstance(document.get(lane_key), list):
            errors.append(f"'{lane_key}' must be an array")
    if errors:
        return errors

    seen_urls = set()
    for lane_key, expected_lane in (("candidates", "peer"), ("hiring_adjacent", "hiring_adjacent")):
        previous = None
        for index, item in enumerate(document[lane_key]):
            where = f"{lane_key}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{where} must be an object")
                continue
            url = squeeze(item.get("public_url", ""))
            if not is_public_profile_url(url):
                errors.append(f"{where}.public_url is not a public profile URL: {url!r}")
            elif url != normalize_public_url(url):
                errors.append(f"{where}.public_url is not normalized: {url!r}")
            if url in seen_urls:
                errors.append(f"{where}.public_url duplicates another candidate: {url}")
            seen_urls.add(url)
            if item.get("lane") != expected_lane:
                errors.append(f"{where}.lane must be '{expected_lane}'")
            for key in ("paths", "unknowns", "anchors_matched", "headline_observed",
                        "url_observed_in", "score_breakdown", "notes"):
                if key in ("headline_observed",):
                    if not isinstance(item.get(key), str):
                        errors.append(f"{where}.{key} must be a string")
                    continue
                if not isinstance(item.get(key), list):
                    errors.append(f"{where}.{key} must be an array")
            for path in item.get("paths", []):
                if path not in PACK_INDEX:
                    errors.append(f"{where}.paths contains an unknown pack id: {path!r}")
            if not item.get("unknowns"):
                errors.append(f"{where}.unknowns must be present and non-empty")
            if not isinstance(item.get("score"), (int, float)):
                errors.append(f"{where}.score must be numeric")
            state = item.get("state")
            if not isinstance(state, dict) or state.get("stage") != "discovery":
                errors.append(f"{where}.state.stage must be 'discovery'")
            employer = item.get("employer_observed")
            if not isinstance(employer, dict) or not squeeze(employer.get("value", "")):
                errors.append(f"{where}.employer_observed must name an observed employer")
            ordering = (-item.get("score", 0), item.get("candidate_id", ""))
            if previous is not None and ordering < previous:
                errors.append(f"{where} breaks the frozen lane ordering rule")
            previous = ordering

    for hit in claim_token_hits(document):
        errors.append(f"claim token '{hit['token']}' emitted at {hit['path']}")
    for hit in forbidden_truthy_hits(document):
        errors.append(f"forbidden truthy key emitted at {hit['path']}")
    return errors


def validate_queries(document):
    errors = []
    if document.get("schema") != SCHEMA_QUERIES:
        errors.append(f"schema must be '{SCHEMA_QUERIES}'")
    packs = document.get("packs")
    if not isinstance(packs, list) or not packs:
        errors.append("'packs' must be a non-empty array")
    lanes = set()
    for index, pack in enumerate(packs or []):
        where = f"packs[{index}]"
        if not isinstance(pack, dict):
            errors.append(f"{where} must be an object")
            continue
        pack_id = pack.get("pack_id")
        if pack_id not in PACK_INDEX:
            errors.append(f"{where}.pack_id is unknown: {pack_id!r}")
        lanes.add(pack.get("lane"))
        if pack.get("lane") == "hiring_adjacent":
            if pack.get("mixed_with_peer_lane") is not False:
                errors.append(f"{where} must declare mixed_with_peer_lane=false")
            if not pack.get("lexicon"):
                errors.append(f"{where} must carry its lane lexicon")
        if pack.get("pack_id") == "shared_stamp":
            if pack.get("proxy_label") != "public_stamp_proxy":
                errors.append(f"{where} must be labelled public_stamp_proxy")
            if pack.get("graph_edge_claimed") is not False:
                errors.append(f"{where} must declare graph_edge_claimed=false")
        if not isinstance(pack.get("queries"), list) or not pack.get("queries"):
            errors.append(f"{where}.queries must be a non-empty array")
    anchors = document.get("anchors")
    if not isinstance(anchors, list):
        errors.append("'anchors' must be an array")
    for index, anchor in enumerate(anchors or []):
        where = f"anchors[{index}]"
        if not isinstance(anchor, dict):
            errors.append(f"{where} must be an object")
            continue
        for field in ("anchor_id", "type", "value", "normalized", "weight"):
            if field not in anchor:
                errors.append(f"{where}.{field} is required")
        evidence = anchor.get("evidence")
        if not isinstance(evidence, dict) or not squeeze(evidence.get("quote", "")):
            errors.append(f"{where}.evidence.quote must carry the observed source text")
        if not squeeze(anchor.get("value", "")):
            errors.append(f"{where}.value must not be empty")
    if not isinstance(document.get("lane_policy"), dict):
        errors.append("'lane_policy' is required")
    for hit in claim_token_hits(document):
        errors.append(f"claim token '{hit['token']}' emitted at {hit['path']}")
    return errors


def validate_serp(document):
    errors = []
    if document.get("schema") != SCHEMA_SERP:
        errors.append(f"schema must be '{SCHEMA_SERP}'")
    if not isinstance(document.get("pack_results"), list):
        errors.append("'pack_results' must be an array")
    return errors


VALIDATORS = {
    SCHEMA_CANDIDATES: validate_candidates,
    SCHEMA_QUERIES: validate_queries,
    SCHEMA_SERP: validate_serp,
}


def validate_document(document):
    if not isinstance(document, dict):
        return ["document must be a JSON object"]
    schema = document.get("schema")
    validator = VALIDATORS.get(schema)
    if validator is None:
        return [f"unknown document schema: {schema!r} (expected one of {sorted(VALIDATORS)})"]
    return validator(document)


def validate_file(path):
    if not os.path.isfile(path):
        return [f"file not found: {path}"]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as cause:
        return [f"file is not readable JSON: {path} ({cause})"]
    return validate_document(document)


__all__ = [
    "CLAIM_TOKENS",
    "OBSERVED_EVIDENCE_PATH_PARTS",
    "FORBIDDEN_TRUTHY_KEYS",
    "claim_token_hits",
    "forbidden_truthy_hits",
    "validate_candidates",
    "validate_queries",
    "validate_serp",
    "validate_document",
    "validate_file",
]
