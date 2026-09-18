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
MAX_QUERIES_PER_RUN = 12    # bounded host route budget per role
# Within the fixed budget, direct role-family retrieval gets the next available
# variant before the lower-tier public-stamp proxy. This is a general signal
# priority, not a role/company-specific query injection.
QUERY_ALLOCATION_PRIORITY = (
    "function_at_target",
    "alumni_at_target",
    "prior_employer_at_target",
    "community_at_target",
    "hiring_adjacent",
    "shared_stamp",
)
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


def _query_for(value_a, value_b="", *, positional=False):
    clause = f'"{value_a}"'
    if value_b:
        clause = f'{clause} "{value_b}"'
        if positional:
            clause = f'{clause} "at {value_b}"'
    return f"{clause} site:linkedin.com/in"


def _skip_pack(spec, reason, *, missing_anchor_types=(), observed_stamp_count=None):
    missing_ids = [f"anchor_{anchor_type}_absent" for anchor_type in missing_anchor_types]
    payload = {
        "pack_id": spec["pack_id"],
        "lane": spec["lane"],
        "reason": reason,
        "absence_reason": reason,
        "missing_anchor_types": list(missing_anchor_types),
        "missing_anchor_ids": missing_ids,
        "missing_anchor_id": missing_ids[0] if missing_ids else None,
    }
    if observed_stamp_count is not None:
        payload["observed_stamp_count"] = observed_stamp_count
    return None, payload


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
            return _skip_pack(
                spec,
                "fewer than two seeker public stamps were extracted; a shared_stamp "
                "proxy cannot be formed without inventing one",
                missing_anchor_types=("school", "prior_employer", "rare_community"),
                observed_stamp_count=len(stamps),
            )
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
            return _skip_pack(
                spec,
                "no anchor of the required types was extracted from the supplied inputs",
                missing_anchor_types=spec["match_anchor_types"],
            )
        function_pack = spec["pack_id"] == "function_at_target"
        # Function retrieval is the bounded fallback lane. It may contribute
        # more than the default three rows before the global cap is applied,
        # because a provider can return zero rows or raise on a useful-looking
        # variant. Other packs retain their three-query lane cap.
        matched = matched[:MAX_QUERIES_PER_RUN if function_pack else USER_AGENT_PACKS]
        for anchor_index, anchor in enumerate(matched):
            if function_pack:
                available = max(0, MAX_QUERIES_PER_RUN - len(queries))
            else:
                # Reserve one query for every remaining anchor so a title
                # variant cannot starve the next stamp anchor from this pack.
                remaining_anchor_count = len(matched) - anchor_index - 1
                available = max(1, USER_AGENT_PACKS - len(queries) - remaining_anchor_count)
            values = [anchor["value"]]
            if function_pack:
                variants = list(anchor.get("attributes", {}).get("query_variants", []))
                preferred = [
                    value for value in variants
                    if len(norm_phrase(value).split()) > 1
                    or not str(value).strip().isupper()
                ]
                abbreviated = [
                    value for value in variants
                    if value not in preferred
                ]
                # Multi-word role-family forms are useful first; the canonical
                # supplied role follows them, and a one-token initialism is a
                # bounded fallback rather than the first route.
                values = preferred + values + abbreviated
            deduped_values = []
            seen_values = set()
            for value in values:
                normalized = norm_phrase(value)
                if normalized and normalized not in seen_values:
                    seen_values.add(normalized)
                    deduped_values.append(value)
            for value_index, value in enumerate(deduped_values[:available]):
                row = {
                    "query": _query_for(value, company, positional=function_pack) if spec["requires_target_employer"]
                    else _query_for(value),
                    "anchor_ids": [anchor["anchor_id"]],
                }
                if function_pack:
                    row["query_kind"] = (
                        "role_family_canonical"
                        if norm_phrase(value) == norm_phrase(anchor["value"])
                        else "role_family_variant"
                    )
                    row["variant_rank"] = value_index
                queries.append(row)
                used.append(anchor["anchor_id"])
            if function_pack and len(queries) >= MAX_QUERIES_PER_RUN:
                break

    if not queries:
        return _skip_pack(
            spec,
            "no query could be formed from the supplied inputs",
            missing_anchor_types=spec["match_anchor_types"],
        )

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


def _query_text_key(query):
    """Case/whitespace-normalized query identity used before budget allocation."""
    return squeeze(query).casefold()


def _apply_query_budget(packs):
    """Deduplicate and allocate the fixed run budget with a deterministic pack floor.

    Every non-empty compiled pack receives one query before spare capacity is
    spent by signal priority. Duplicate query text is retained in the explicit
    skip ledger, so the host can distinguish deduplication from cap pressure.
    """
    raw_by_pack = {pack["pack_id"]: list(pack.get("queries", [])) for pack in packs}
    unique_by_pack = {pack["pack_id"]: [] for pack in packs}
    duplicate_skips = {pack["pack_id"]: [] for pack in packs}
    seen_queries = {}

    for pack in packs:
        pack_id = pack["pack_id"]
        for row in raw_by_pack[pack_id]:
            key = _query_text_key(row.get("query", ""))
            if key and key in seen_queries:
                previous_pack_id, previous_row = seen_queries[key]
                duplicate_skips[pack_id].append({
                    **row,
                    "skip_reason": "duplicate query text after case/whitespace normalization",
                    "skip_reason_code": "duplicate_query_text",
                    "duplicate_of_pack_id": previous_pack_id,
                    "duplicate_of_query": previous_row.get("query", ""),
                })
                continue
            if key:
                seen_queries[key] = (pack_id, row)
            unique_by_pack[pack_id].append(row)

    raw_before = sum(len(rows) for rows in raw_by_pack.values())
    deduplicated_before = sum(len(rows) for rows in unique_by_pack.values())
    selected = {pack["pack_id"]: [] for pack in packs}

    if deduplicated_before <= MAX_QUERIES_PER_RUN:
        for pack in packs:
            pack_id = pack["pack_id"]
            selected[pack_id] = list(unique_by_pack[pack_id])
    else:
        # The floor is applied before priority extras. There are six declared
        # packs and the unchanged cap is twelve, so every compiled lane can
        # retain representation without changing the global route budget.
        for pack in packs:
            pack_id = pack["pack_id"]
            if unique_by_pack[pack_id]:
                selected[pack_id].append(unique_by_pack[pack_id][0])

        remaining = MAX_QUERIES_PER_RUN - sum(len(rows) for rows in selected.values())
        priority = {
            pack_id: index for index, pack_id in enumerate(QUERY_ALLOCATION_PRIORITY)
        }
        ordered_packs = sorted(
            packs,
            key=lambda pack: (priority.get(pack["pack_id"], len(priority)), pack["pack_id"]),
        )
        # Spend spare capacity in signal-priority order after the one-query
        # floor. This keeps role-family fallbacks useful without starving
        # shared-stamp, community, or hiring-adjacent packs.
        for pack in ordered_packs:
            pack_id = pack["pack_id"]
            rows = selected[pack_id]
            while remaining > 0 and len(rows) < len(unique_by_pack[pack_id]):
                rows.append(unique_by_pack[pack_id][len(rows)])
                remaining -= 1

    for pack in packs:
        pack_id = pack["pack_id"]
        rows = selected[pack_id]
        cap_skipped = unique_by_pack[pack_id][len(rows):]
        skipped = list(duplicate_skips[pack_id]) + [
            {
                **row,
                "skip_reason": "per-run query budget cap",
                "skip_reason_code": "query_budget_cap",
            }
            for row in cap_skipped
        ]
        pack["queries"] = rows
        pack["query"] = rows[0]["query"] if rows else ""
        pack["anchor_ids"] = sorted({
            anchor_id for row in rows for anchor_id in row.get("anchor_ids", [])
        })
        pack["queries_compiled_before_cap"] = len(raw_by_pack[pack_id])
        pack["queries_deduplicated_before_cap"] = len(unique_by_pack[pack_id])
        pack["queries_duplicate_skipped"] = len(duplicate_skips[pack_id])
        pack["queries_skipped"] = skipped
    return raw_before, sum(len(rows) for rows in selected.values())


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

    query_count_before_cap, query_count = _apply_query_budget(packs)
    priority = {
        pack_id: index for index, pack_id in enumerate(QUERY_ALLOCATION_PRIORITY)
    }
    # The host executes the compiled pack list in order. Put the same
    # high-signal lanes first so provider exceptions or a wall-clock stop do
    # not consume the run before role-family fallbacks are attempted.
    packs.sort(key=lambda pack: (
        priority.get(pack["pack_id"], len(priority)),
        pack["pack_id"],
    ))
    targets = [anchor for anchor in anchors if anchor["type"] == "target_employer"]
    company_observed = targets[0]["evidence"]["quote"] if targets else ""
    query_skip_reasons = sorted({
        row.get("skip_reason", "")
        for pack in packs
        for row in pack.get("queries_skipped", [])
        if row.get("skip_reason", "")
    })
    queries_deduplicated_before_cap = sum(
        int(pack.get("queries_deduplicated_before_cap", 0))
        for pack in packs
    )
    queries_duplicate_skipped = sum(
        int(pack.get("queries_duplicate_skipped", 0))
        for pack in packs
    )

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
        "execution": {
            "query_budget": MAX_QUERIES_PER_RUN,
            "queries_compiled_before_cap": query_count_before_cap,
            "queries_deduplicated_before_cap": queries_deduplicated_before_cap,
            "queries_compiled": query_count,
            "queries_skipped": sum(len(pack.get("queries_skipped", [])) for pack in packs),
            "queries_duplicate_skipped": queries_duplicate_skipped,
            "query_skip_reason": "; ".join(query_skip_reasons) if query_skip_reasons else None,
            "within_query_budget": query_count <= MAX_QUERIES_PER_RUN,
            "query_allocation": {
                "scope": "global_per_run",
                "priority": list(QUERY_ALLOCATION_PRIORITY),
                "fallback_lane": "function_at_target",
                "fallback_policy": (
                    "reserve one query for every non-empty compiled pack, then spend "
                    "spare capacity on role-family variants before lower-tier lanes; "
                    "normalized duplicates and cap-removed rows remain in queries_skipped"
                ),
            },
            "network_calls": 0,
            "retry_count": 0,
            "terminal_route_failure": None,
        },
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
                "queries_per_pack_policy": (
                    "default lane size before the global cap; normalized query text is "
                    "deduplicated and every non-empty pack receives a one-query floor"
                ),
                "query_cap_scope": "global_per_run",
                "queries_per_run": MAX_QUERIES_PER_RUN,
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
