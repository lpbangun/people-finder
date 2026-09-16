"""Query-pack compilation.

Several packs are compiled for one seeker + one target job and every pack is
lane-tagged, so a result set can never be reduced to school x employer. Packs
whose anchors are absent are reported as skipped with the missing fact — they
are never filled with invented values.
"""

from datetime import datetime, timezone

from . import SCHEMA_QUERIES
from .anchors import (AnchorError, GENERIC_SCHOOL_TOKENS, HIRING_ADJACENT_TERMS,
                      WEIGHTS, extract_anchors, load_job_card, read_text_file, tokens)
from .textutil import norm_phrase, squeeze

USER_AGENT_PACKS = 3          # queries emitted per pack
PER_PATH_TOTAL_CAP = 12.0
PER_TYPE_PER_PATH_CAP = 2
HIRING_ADJACENT_TERM_WEIGHT = 0.5   # per matched hiring-adjacent lexicon term

PACK_SPECS = (
    {
        "pack_id": "alumni_at_target",
        "lane": "peer",
        "intent": "school or program stamp observed together with the target employer",
        "match_anchor_types": ("school", "program"),
        "requires_target_employer": True,
        "requires_stamp_count": 0,
        "expected_signal": "shared education stamp at the target company",
    },
    {
        "pack_id": "prior_employer_at_target",
        "lane": "peer",
        "intent": "a company the seeker actually worked at, observed at the target employer",
        "match_anchor_types": ("prior_employer",),
        "requires_target_employer": True,
        "requires_stamp_count": 0,
        "expected_signal": "someone who already walked the seeker's career path into the target",
    },
    {
        "pack_id": "function_at_target",
        "lane": "peer",
        "intent": "job-derived role family or department keyword observed at the target employer",
        "match_anchor_types": ("function",),
        "requires_target_employer": True,
        "requires_stamp_count": 0,
        "expected_signal": "same function, weak identity signal on its own",
    },
    {
        "pack_id": "community_at_target",
        "lane": "peer",
        "intent": "rare community stamp (lab, OSS org, conference, paper) observed at the target employer",
        "match_anchor_types": ("rare_community",),
        "requires_target_employer": True,
        "requires_stamp_count": 0,
        "expected_signal": "shared public mark, highest-weight peer signal",
    },
    {
        "pack_id": "shared_stamp",
        "lane": "peer",
        "intent": "two public stamps observed in one supplied result, without requiring the target employer",
        "match_anchor_types": ("school", "program", "prior_employer", "rare_community"),
        "requires_target_employer": False,
        "requires_stamp_count": 2,
        "skipped_when_stamp_path_already_fired": True,
        "skip_reason": (
            "the same two stamps were already counted by a target-anchored stamp path; the "
            "proxy must not double-count evidence"
        ),
        "expected_signal": "public-stamp proxy for approximate warmth",
        "proxy_label": "public_stamp_proxy",
        "graph_edge_claimed": False,
        "not_claimed": ["linkedin_member_graph_edge"],
        "disclaimer": (
            "Two public stamps observed in the same supplied result. This is a "
            "public-stamp proxy only: the seeker and the lead both left the same "
            "mark on the public web. No member-graph edge is observed or claimed."
        ),
    },
    {
        "pack_id": "hiring_adjacent",
        "lane": "hiring_adjacent",
        "intent": "talent-acquisition, recruiter or hiring-manager surface at the target employer",
        "match_anchor_types": ("hiring_adjacent",),
        "requires_target_employer": True,
        "requires_stamp_count": 0,
        "expected_signal": "a process contact, not a peer; kept in its own lane",
        "lexicon": list(HIRING_ADJACENT_TERMS),
        "mixed_with_peer_lane": False,
        "separate_lane_reason": "hiring-adjacent results must never displace or reorder peer candidates",
    },
)

PACK_INDEX = {spec["pack_id"]: spec for spec in PACK_SPECS}


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query_for(value_a, value_b=""):
    clause = f'"{value_a}"'
    if value_b:
        clause = f'{clause} "{value_b}"'
    return f"{clause} site:linkedin.com/in"


def _compile_pack(spec, anchors, target):
    company = target["company"]
    queries = []
    used = []
    if spec["pack_id"] == "shared_stamp":
        stamps = [
            anchor for anchor in anchors
            if anchor["type"] in spec["match_anchor_types"] and anchor["used_in_ranking"]
        ]
        pairs = [
            (stamps[i], stamps[j])
            for i in range(len(stamps))
            for j in range(i + 1, len(stamps))
        ]
        for left, right in pairs[:USER_AGENT_PACKS]:
            queries.append({
                "query": _query_for(left["value"], right["value"]),
                "anchor_ids": [left["anchor_id"], right["anchor_id"]],
            })
            used.extend([left["anchor_id"], right["anchor_id"]])
        if len(stamps) < spec["requires_stamp_count"]:
            return None, {
                "pack_id": spec["pack_id"],
                "lane": spec["lane"],
                "reason": (
                    "fewer than two seeker public stamps were extracted; a shared_stamp "
                    "proxy cannot be formed without inventing one"
                ),
                "observed_stamp_count": len(stamps),
            }
    elif spec["pack_id"] == "hiring_adjacent":
        for term in spec["lexicon"][:USER_AGENT_PACKS]:
            queries.append({
                "query": _query_for(term, company),
                "anchor_ids": [],
                "lexicon_term": term,
            })
        used = []
    else:
        matched = [
            anchor for anchor in anchors
            if anchor["type"] in spec["match_anchor_types"] and anchor["used_in_ranking"]
        ]
        if not matched:
            return None, {
                "pack_id": spec["pack_id"],
                "lane": spec["lane"],
                "reason": "no anchor of the required types was extracted from the supplied inputs",
                "missing_anchor_types": list(spec["match_anchor_types"]),
            }
        for anchor in matched[:USER_AGENT_PACKS]:
            queries.append({
                "query": _query_for(anchor["value"], company) if spec["requires_target_employer"]
                else _query_for(anchor["value"]),
                "anchor_ids": [anchor["anchor_id"]],
            })
            used.append(anchor["anchor_id"])

    if not queries:
        return None, {
            "pack_id": spec["pack_id"],
            "lane": spec["lane"],
            "reason": "no query could be formed from the supplied inputs",
        }

    pack = {
        "pack_id": spec["pack_id"],
        "lane": spec["lane"],
        "intent": spec["intent"],
        "match_anchor_types": list(spec["match_anchor_types"]),
        "requires_target_employer": bool(spec["requires_target_employer"]),
        "requires_stamp_count": int(spec["requires_stamp_count"]),
        "expected_signal": spec["expected_signal"],
        "anchor_ids": sorted(set(used)),
        "query": queries[0]["query"],
        "queries": queries,
        "required_filters": (
            [f"target_employer:{company}"] if spec["requires_target_employer"] else []
        ),
        "scoring": {
            "per_path_total_cap": PER_PATH_TOTAL_CAP,
            "per_anchor_type_per_path_cap": PER_TYPE_PER_PATH_CAP,
        },
        "not_claimed": list(spec.get("not_claimed", [])),
    }
    for extra in ("proxy_label", "graph_edge_claimed", "disclaimer", "lexicon",
                  "mixed_with_peer_lane", "separate_lane_reason",
                  "skipped_when_stamp_path_already_fired", "skip_reason"):
        if extra in spec:
            pack[extra] = spec[extra]
    return pack, None


def compile_packs(resume_text, job, *, resume_source="<supplied resume>",
                  job_source="<supplied job card>", generated_at=None):
    """Compile the query packs document (people-queries.v1)."""
    extracted = extract_anchors(
        resume_text, job, resume_source=resume_source, job_source=job_source
    )
    anchors = extracted["anchors"]
    packs = []
    skipped = []
    for spec in PACK_SPECS:
        pack, skip = _compile_pack(spec, anchors, extracted["target"])
        if pack:
            packs.append(pack)
        elif skip:
            skipped.append(skip)

    targets = [anchor for anchor in anchors if anchor["type"] == "target_employer"]
    company_observed = targets[0]["evidence"]["quote"] if targets else ""

    document = {
        "schema": SCHEMA_QUERIES,
        "generated_at": generated_at or _utc_now(),
        "excluded_for_determinism": ["generated_at"],
        "seeker": extracted["seeker"],
        "target": extracted["target"],
        "anchors": anchors,
        "unknowns": extracted["unknowns"],
        "packs": packs,
        "packs_skipped": skipped,
        "lane_policy": {
            "peer": "peer candidates are ordered inside this lane only",
            "hiring_adjacent": (
                "separate lane for talent-acquisition and hiring-manager surfaces; never "
                "mixed into peer ordering"
            ),
            "cross_lane_ordering": "not_supported",
            "hiring_adjacent_lexicon": list(HIRING_ADJACENT_TERMS),
            "hiring_adjacent_lexicon_source": "pack lexicon, not a seeker anchor",
        },
        "ranking_plan": {
            "method": "sparse typed-anchor overlap with IDF-style token downweight",
            "uses_embeddings": False,
            "uses_opaque_semantic_similarity": False,
            "weights": {**WEIGHTS, "hiring_adjacent_term": HIRING_ADJACENT_TERM_WEIGHT},
            "idf": {
                "formula": "ln(1 + observed_hits / document_frequency)",
                "df_scope": "supplied recorded results of this run",
            },
            "caps": {
                "per_path_total": PER_PATH_TOTAL_CAP,
                "per_anchor_type_per_path": PER_TYPE_PER_PATH_CAP,
                "queries_per_pack": USER_AGENT_PACKS,
            },
            "required_filter": (
                "for peer packs the target employer must be observed in the supplied "
                "result before a path can fire"
            ),
            "match_scope": (
                "title-derived function anchors match title or snippet; department anchors "
                "match result titles only"
            ),
            "unknowns_policy": "unobserved facts are reported in unknowns; nothing is filled in",
            "anchor_type_target_employer_role": "required filter, zero score weight",
            "skill_anchors": "extracted but default off; never used for scoring",
            "deterministic": True,
        },
        "anchor_summary": {
            "counts_by_type": {
                anchor_type: len([a for a in anchors if a["type"] == anchor_type])
                for anchor_type in WEIGHTS
            },
            "restricted_types_present": sorted({
                a["type"] for a in anchors
                if a["type"] in ("rare_community", "school", "program", "prior_employer")
            }),
            "generic_school_values": sorted({
                a["value"] for a in anchors
                if a["type"] == "school" and a["generic"]
            }),
            "target_employer_observed": company_observed,
        },
        "boundary": {
            "stage": "discovery only",
            "network": "none — packs compile from supplied local inputs",
            "note": (
                "Compiled queries are retrieval instructions. A public /in/ URL is a lead, "
                "not a contact, not a verified identity and not a delivery route."
            ),
        },
    }
    return document


def compile_from_paths(resume_path, job_path, generated_at=None):
    resume_text = read_text_file(resume_path, "resume")
    job = load_job_card(job_path)
    return compile_packs(
        resume_text, job,
        resume_source=resume_path, job_source=job_path, generated_at=generated_at,
    )


def missing_stamp_warning_text():
    """Human-readable reminder used by --explain output (never machine-asserted)."""
    return (
        "shared_stamp is a public-stamp proxy: two public stamps in one supplied "
        "result. It never claims a member-graph edge."
    )


__all__ = [
    "PACK_SPECS",
    "PACK_INDEX",
    "compile_packs",
    "compile_from_paths",
    "AnchorError",
    "norm_phrase",
    "squeeze",
    "tokens",
    "GENERIC_SCHOOL_TOKENS",
]
