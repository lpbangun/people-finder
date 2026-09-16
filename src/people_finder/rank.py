"""Sparse typed-anchor ranking.

Score = linear combination of typed anchor overlaps over the supplied results,
with an IDF-style downweight on tokens that appear everywhere, a cap per path so
a crowd of generic title matches cannot bury two rare stamps, and explicit
`unknowns` for everything that was not observed.

No person, employer, school, title or URL is ever emitted unless it was present
verbatim in the supplied inputs.
"""

import hashlib
import math
from datetime import datetime, timezone

from . import SCHEMA_CANDIDATES, SCHEMA_QUERIES, SCHEMA_SERP
from .anchors import RESTRICTED_ANCHOR_TYPES
from .packs import HIRING_ADJACENT_TERM_WEIGHT, PER_PATH_TOTAL_CAP, PER_TYPE_PER_PATH_CAP
from .results import ResultError
from .textutil import (all_terms_present, find_span, is_public_profile_url,
                       normalize_public_url, norm_phrase, phrase_present,
                       profile_slug_tokens, round_score, split_title, squeeze)

STAMP_TYPES = ("rare_community", "school", "program", "prior_employer")

LANE_PEER = "peer"
LANE_HIRING_ADJACENT = "hiring_adjacent"

BASE_UNKNOWNS = (
    "identity_not_established",
    "linkedin_edge_not_observed",
    "contactability_not_established",
    "email_not_sought",
)

CANDIDATE_NOTE = (
    "Public /in/ URL observed in the supplied result. This is a discovery lead: "
    "not a verified identity, not a contact record and not a delivery route."
)

HIRING_ADJACENT_NOTE = (
    "Hiring-adjacent lane: a talent-acquisition or hiring-manager surface at the target "
    "employer. This result is ordered in its own lane and never displaces peer candidates."
)

SHARED_STAMP_NOTE = (
    "Public-stamp proxy: two of the seeker's public stamps were observed in the "
    "same supplied result. No member-graph edge is observed or claimed."
)


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidate_id(url_key):
    digest = hashlib.sha1(url_key.encode("utf-8")).hexdigest()[:12]
    return f"cand_{digest}"


def _match_anchor(anchor, title, snippet, slug_text):
    """Return the observed fields for one anchor, or [] when unobserved.

    A department-derived function anchor is only counted when a result title
    carries it: a short department phrase inside a snippet is not role evidence.
    """
    scope = anchor.get("attributes", {}).get("match_scope", "title_or_snippet")
    observed = []
    if scope in ("title", "title_or_snippet") and phrase_present(title, anchor["value"]):
        observed.append("title")
    if scope in ("snippet", "title_or_snippet") and phrase_present(snippet, anchor["value"]):
        observed.append("snippet")
    if scope != "title" and slug_text and phrase_present(slug_text, anchor["value"]):
        observed.append("url")
    if observed:
        return observed
    if anchor["type"] == "function":
        terms = anchor.get("attributes", {}).get("terms") or []
        if not terms:
            return []
        # The all-terms fallback must be satisfied inside one observed field.
        if scope in ("title", "title_or_snippet") and all_terms_present(title, terms):
            return ["title:function_terms"]
        if scope in ("snippet", "title_or_snippet") and all_terms_present(snippet, terms):
            return ["snippet:function_terms"]
    return []


def _match_lexicon_terms(pack, text):
    return [term for term in pack.get("lexicon", []) if phrase_present(text, term)]


def _target_observed(anchor_value, title, snippet, url_key, slug_text):
    if phrase_present(title, anchor_value):
        return "title"
    if phrase_present(snippet, anchor_value):
        return "snippet"
    if slug_text and phrase_present(slug_text, anchor_value):
        return "url"
    slug_company = norm_phrase(anchor_value).replace(" ", "-")
    if slug_company and slug_company in url_key:
        return "url"
    return ""


STAMP_PATHS = ("alumni_at_target", "prior_employer_at_target", "community_at_target")


def _fire_paths(hit, packs):
    """Evidence-derived path firing: a path fires only on observed facts.

    shared_stamp is a lower-tier proxy, so it fires only when no target-anchored
    stamp path already counted the same stamps in this result.
    """
    fired = []
    text = f"{hit['title']} {hit['snippet']}"
    for pack_id, pack in packs.items():
        if pack_id == "shared_stamp":
            continue
        if pack_id == "hiring_adjacent":
            terms = _match_lexicon_terms(pack, text)
            if terms and hit["target_observed_in"]:
                fired.append((pack_id, terms))
            continue
        match_types = set(pack["match_anchor_types"])
        matched = [anchor_id for anchor_id, anchor in hit["anchors"].items()
                   if anchor["type"] in match_types]
        if matched and (hit["target_observed_in"] or not pack["requires_target_employer"]):
            fired.append((pack_id, matched))

    spec = packs.get("shared_stamp")
    if spec is not None and not any(path in STAMP_PATHS for path, _ in fired):
        allowed = set(spec["match_anchor_types"])
        stamps = [anchor_id for anchor_id, anchor in hit["anchors"].items()
                  if anchor["type"] in allowed]
        if len(stamps) >= int(spec["requires_stamp_count"]):
            fired.append(("shared_stamp", stamps))
    return fired


def _score_candidate(record, packs, anchors_by_id, idf):
    """Per-path contributions, capped per path and per anchor type."""
    contributions = []
    per_path_totals = {}
    for path in sorted(record["paths"]):
        pack = packs.get(path)
        if pack is None:
            continue
        allowed = set(pack["match_anchor_types"])
        seen = {}
        terms = set()
        for hit in record["hits"]:
            fired = {fired_path for fired_path, _ in hit["paths"]}
            if path not in fired:
                continue
            for anchor_id, info in hit["anchors"].items():
                anchor = anchors_by_id.get(anchor_id)
                if anchor and anchor["type"] in allowed:
                    seen.setdefault(anchor_id, set()).update(info["fields"])
            if path == "hiring_adjacent":
                terms.update(_match_lexicon_terms(pack, f"{hit['title']} {hit['snippet']}"))

        path_total = 0.0
        type_counts = {}
        for anchor_id in sorted(seen):
            anchor = anchors_by_id.get(anchor_id)
            if not anchor or not anchor["used_in_ranking"] or anchor["weight"] <= 0:
                continue
            type_counts[anchor["type"]] = type_counts.get(anchor["type"], 0) + 1
            if type_counts[anchor["type"]] > PER_TYPE_PER_PATH_CAP:
                continue
            contribution = round_score(anchor["weight"] * idf.get(anchor_id, 1.0))
            capped = min(contribution, round_score(PER_PATH_TOTAL_CAP - path_total))
            if capped <= 0:
                break
            path_total = round_score(path_total + capped)
            contributions.append({
                "path": path,
                "anchor_id": anchor_id,
                "anchor_type": anchor["type"],
                "value": anchor["value"],
                "weight": anchor["weight"],
                "idf": idf.get(anchor_id, 1.0),
                "contribution": capped,
                "observed_in": sorted(seen[anchor_id]),
                "capped": capped < contribution,
            })
        for term in sorted(terms):
            capped = min(HIRING_ADJACENT_TERM_WEIGHT,
                         round_score(PER_PATH_TOTAL_CAP - path_total))
            if capped <= 0:
                break
            path_total = round_score(path_total + capped)
            contributions.append({
                "path": path,
                "anchor_id": None,
                "anchor_type": "hiring_adjacent_term",
                "value": term,
                "weight": HIRING_ADJACENT_TERM_WEIGHT,
                "idf": 1.0,
                "contribution": capped,
                "observed_in": ["title", "snippet"],
                "capped": False,
            })
        per_path_totals[path] = path_total
    total = round_score(sum(item["contribution"] for item in contributions))
    return total, contributions, per_path_totals


def rank_candidates(compiled, results_doc, *, queries_source="<supplied queries>",
                    results_source="<supplied results>", generated_at=None):
    """Rank supplied results against compiled packs -> people-candidates.v1."""
    if not isinstance(compiled, dict) or compiled.get("schema") != SCHEMA_QUERIES:
        raise ResultError(f"compiled queries must be a {SCHEMA_QUERIES} document")
    if not isinstance(results_doc, dict) or results_doc.get("schema") != SCHEMA_SERP:
        raise ResultError(f"supplied results must be a {SCHEMA_SERP} document")

    packs = {pack["pack_id"]: pack for pack in compiled.get("packs", [])}
    anchors = compiled.get("anchors", [])
    anchors_by_id = {anchor["anchor_id"]: anchor for anchor in anchors}
    target = compiled.get("target", {})
    target_company = squeeze(target.get("company", ""))
    if not target_company:
        raise ResultError("compiled queries are missing the target employer")

    # ---- phase 1: normalize supplied hits -----------------------------------
    hits = []
    suppressed = []
    ignored_pack_ids = []
    for entry in results_doc["pack_results"]:
        pack_id = entry["pack_id"]
        if pack_id not in packs:
            if pack_id not in ignored_pack_ids:
                ignored_pack_ids.append(pack_id)
            for row in entry["results"]:
                suppressed.append({
                    "url": row["url"],
                    "observed_in": [row["position"]],
                    "packs_observed": [pack_id],
                    "reason": "pack_not_compiled",
                })
            continue
        for row in entry["results"]:
            if not is_public_profile_url(row["url"]):
                suppressed.append({
                    "url": row["url"],
                    "observed_in": [row["position"]],
                    "packs_observed": [pack_id],
                    "reason": "not_a_public_profile_url",
                })
                continue
            url_key = normalize_public_url(row["url"])
            hits.append({
                "url_key": url_key,
                "url_observed": row["url"],
                "title": row["title"],
                "snippet": row["snippet"],
                "pack_id": pack_id,
                "position": row["position"],
                "rank_in_pack": row["rank_in_pack"],
                "slug_text": " ".join(profile_slug_tokens(url_key)),
            })

    # ---- phase 2: anchor observations + idf ---------------------------------
    total_hits = len(hits)
    for hit in hits:
        observed = {}
        for anchor in anchors:
            if not anchor["used_in_ranking"]:
                continue
            fields = _match_anchor(anchor, hit["title"], hit["snippet"], hit["slug_text"])
            if fields:
                observed[anchor["anchor_id"]] = {"type": anchor["type"], "fields": fields}
        hit["anchors"] = observed
        hit["target_observed_in"] = _target_observed(
            target_company, hit["title"], hit["snippet"], hit["url_key"], hit["slug_text"]
        )
        hit["paths"] = _fire_paths(hit, packs)

    idf = {}
    for anchor in anchors:
        if not anchor["used_in_ranking"]:
            continue
        df = sum(1 for hit in hits if anchor["anchor_id"] in hit["anchors"])
        if df:
            idf[anchor["anchor_id"]] = round_score(math.log(1 + total_hits / df))

    # ---- phase 3: merge hits per public URL ---------------------------------
    merged = {}
    for hit in hits:
        peer_paths = [path for path, _ in hit["paths"] if path != "hiring_adjacent"]
        hire_paths = [path for path, _ in hit["paths"] if path == "hiring_adjacent"]
        stamp_paths = [path for path in peer_paths if path in (*STAMP_PATHS, "shared_stamp")]
        if hire_paths and not stamp_paths:
            lane = LANE_HIRING_ADJACENT
        elif hit["paths"]:
            lane = LANE_PEER
        else:
            reason = ("target_employer_not_observed_in_supplied_result"
                      if not hit["target_observed_in"] else "no_typed_anchor_matched")
            suppressed.append({
                "url": hit["url_observed"],
                "observed_in": [hit["position"]],
                "packs_observed": [hit["pack_id"]],
                "reason": reason,
            })
            continue
        record = merged.setdefault(hit["url_key"], {
            "url_key": hit["url_key"],
            "lane": lane,
            "paths": set(),
            "anchors_matched": {},
            "hits": [],
            "target_observed_in": set(),
            "url_observed_in": [],
        })
        if lane == LANE_HIRING_ADJACENT:
            record["lane"] = LANE_HIRING_ADJACENT
        record["paths"].update(path for path, _ in hit["paths"])
        record["hits"].append(hit)
        record["url_observed_in"].append(f"{results_source}#{hit['position']}")
        if hit["target_observed_in"]:
            record["target_observed_in"].add(hit["target_observed_in"])
        for anchor_id, info in hit["anchors"].items():
            slot = record["anchors_matched"].setdefault(anchor_id, {"fields": set(), "packs": set()})
            slot["fields"].update(info["fields"])
            slot["packs"].add(hit["pack_id"])

    # ---- phase 4: build candidates -----------------------------------------
    candidates = []
    for url_key, record in merged.items():
        matched_ids = sorted(record["anchors_matched"])
        score, breakdown, per_path = _score_candidate(record, packs, anchors_by_id, idf)

        names = []
        headlines = []
        for hit in record["hits"]:
            headline = hit["title"] or hit["snippet"]
            if headline and headline not in headlines:
                headlines.append(headline)
            name, _ = split_title(hit["title"])
            if name and name not in names:
                names.append(name)

        matched_types = {anchors_by_id[aid]["type"] for aid in matched_ids if aid in anchors_by_id}
        unknowns = set(BASE_UNKNOWNS)
        if not matched_types & {"school", "program"}:
            unknowns.add("school_not_observed")
        if "rare_community" not in matched_types:
            unknowns.add("community_not_observed")
        if "prior_employer" not in matched_types:
            unknowns.add("prior_employer_not_observed")
        if not record["target_observed_in"]:
            unknowns.add("target_employer_not_observed_in_supplied_result")
        if "shared_stamp" in record["paths"]:
            unknowns.add("public_stamp_proxy_only_not_member_graph_edge")
        if len(names) > 1:
            unknowns.add("name_ambiguous_across_observed_results")
        if not names:
            unknowns.add("name_not_observed")
        if matched_types == {"function"}:
            unknowns.add("function_terms_only")

        notes = [CANDIDATE_NOTE]
        if "shared_stamp" in record["paths"]:
            notes.append(SHARED_STAMP_NOTE)
        if record["lane"] == LANE_HIRING_ADJACENT:
            notes.append(HIRING_ADJACENT_NOTE)

        combined_text = " ".join(
            [hit["title"] for hit in record["hits"]] + [hit["snippet"] for hit in record["hits"]]
        )
        employer_evidence = [
            {
                "field": field,
                "quote": find_span(combined_text, target_company) or target_company,
            }
            for field in sorted(record["target_observed_in"])
        ]

        candidates.append({
            "candidate_id": _candidate_id(url_key),
            "public_url": url_key,
            "url_observed_in": sorted(set(record["url_observed_in"])),
            "name": names[0] if names else None,
            "name_status": "observed_not_verified",
            "headline_observed": headlines[0] if headlines else "",
            "headlines_observed": headlines,
            "employer_observed": {
                "value": target_company,
                "status": "observed_in_supplied_results",
                "observed_in": sorted(record["target_observed_in"]),
                "evidence": employer_evidence,
            },
            "lane": record["lane"],
            "paths": sorted(record["paths"]),
            "score": score,
            "score_breakdown": breakdown,
            "per_path_totals": per_path,
            "anchors_matched": matched_ids,
            "anchor_types_matched": sorted(matched_types),
            "unknowns": sorted(unknowns),
            "state": {
                "stage": "discovery",
                "identity": "not_established",
                "approval": "not_requested",
                "reachability": "not_established",
                "contactability": "not_established",
            },
            "notes": notes,
        })

    peer = sorted([c for c in candidates if c["lane"] == LANE_PEER],
                  key=lambda c: (-c["score"], c["candidate_id"]))
    hiring_adjacent = sorted([c for c in candidates if c["lane"] == LANE_HIRING_ADJACENT],
                             key=lambda c: (-c["score"], c["candidate_id"]))

    document = {
        "schema": SCHEMA_CANDIDATES,
        "generated_at": generated_at or _utc_now(),
        "excluded_for_determinism": ["generated_at", "provenance.retrieved_at"],
        "seeker": compiled.get("seeker", {}),
        "target": target,
        "backend": {
            "mode": results_doc.get("backend", "recorded_fixture"),
            "network": "none",
            "live_network": bool(results_doc.get("live_network", False)),
            "supplied_results_fixture": results_source,
        },
        "counts": {
            "supplied_hits_considered": total_hits,
            "peers": len(peer),
            "hiring_adjacent": len(hiring_adjacent),
            "suppressed_hits": len(suppressed),
        },
        "candidates": peer,
        "hiring_adjacent": hiring_adjacent,
        "suppressed": sorted(suppressed, key=lambda item: (item["url"], item["reason"])),
        "unknowns": sorted({
            *(f"{item['anchor_type']}:{item['status']}" for item in compiled.get("unknowns", [])),
            "identity_not_established_for_every_candidate",
            "contactability_not_established_for_every_candidate",
            "approval_not_requested_for_every_candidate",
        }),
        "ordering_policy": {
            "peer": ("score descending, then candidate_id ascending; hiring-adjacent results "
                     "are excluded from this lane"),
            "hiring_adjacent": ("ordered inside its own lane; scores are not comparable with "
                                "peer scores"),
            "cross_lane_ordering": "not_supported",
            "score_comparison": "within_lane_only",
            "lane_precedence": (
                "stamp evidence (school, program, prior employer, community, shared_stamp) "
                "takes precedence; a result whose only peer signal is a function term stays in "
                "the hiring-adjacent lane"
            ),
            "shared_stamp_label": "public_stamp_proxy",
        },
        "provenance": {
            "queries_source": queries_source,
            "results_source": results_source,
            "packs_compiled": sorted(packs),
            "packs_skipped": [item["pack_id"] for item in compiled.get("packs_skipped", [])],
            "ignored_pack_ids": sorted(ignored_pack_ids),
            "retrieved_at": results_doc.get("retrieved_at", ""),
            "deterministic_ops": "every deterministic operation is run twice per check",
            "network_used": False,
        },
        "boundary": {
            "stage": "discovery only",
            "performs_external_action": False,
            "writes_other_tool_state": False,
            "note": ("Ranking is local scoring over supplied results; nothing is transmitted, "
                     "approved or imported."),
        },
    }
    return document


def restricted_types_claim_check(document):
    """Diagnostic helper: which candidates claim a restricted anchor type."""
    summary = {}
    for item in document.get("candidates", []) + document.get("hiring_adjacent", []):
        for anchor_type in RESTRICTED_ANCHOR_TYPES:
            if anchor_type in item.get("anchor_types_matched", []):
                summary.setdefault(anchor_type, []).append(item["candidate_id"])
    return summary


__all__ = [
    "rank_candidates",
    "ResultError",
    "STAMP_PATHS",
    "HIRING_ADJACENT_NOTE",
    "LANE_PEER",
    "LANE_HIRING_ADJACENT",
    "STAMP_TYPES",
    "CANDIDATE_NOTE",
    "SHARED_STAMP_NOTE",
    "restricted_types_claim_check",
]
