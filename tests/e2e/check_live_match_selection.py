#!/usr/bin/env python3
"""Offline regression for the live-match selection invariant behind G3/L12.

Command: python3 tests/e2e/check_live_match_selection.py

A live journey match must be (1) a ranked peer -- preferred -- or hiring-adjacent item,
(2) whose normalized public URL was observed in the supplied live results, and (3) whose
``paths`` were derived by the unchanged product ranker. Frozen L12 requires
``bool(item["paths"])``, so a selection that only checked the observed URL could lock an
attempt that its own frozen gate rejects.

Each case drives the real ``harness.rank_supplied_results()`` offline with the product ranker
subprocess stubbed; no product code, network call, fixture or live provider is involved.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_profile_jobs_people as harness  # noqa: E402 - path is set above

FIELDS = ("selected_lane", "selected_id", "selected_paths_nonempty", "peer_candidates",
          "hiring_adjacent_candidates", "lanes", "diagnostic_reason", "diagnostic_id")


def url(slug): return f"https://www.linkedin.com/in/{slug}"


def candidate(candidate_id, public_url, paths):
    return {"candidate_id": candidate_id, "public_url": public_url, "paths": list(paths),
            "unknowns": ["relationship path not fired"], "lane": None,
            "employer_observed": {"value": "TetraScience"}}


def document(candidates, hiring_adjacent):
    return {"schema": "people-candidates.v1", "candidates": candidates, "hiring_adjacent": hiring_adjacent}


def supplied_results(observed_urls):
    """The supplied-results shape ``rank_supplied_results()`` reads for observed URLs."""
    return {"schema": "people-live-results.v1", "live_network": True,
            "pack_results": [{"pack_id": "function_at_target", "route": "public_xray_serp",
                              "tool": "host_command", "query": "platform engineer TetraScience",
                              "results": [{"url": item, "title": "Lead",
                                           "snippet": "TetraScience"} for item in observed_urls]}]}


def run_rank(ranked, observed_urls, workdir):
    """Run the real selection path with the product ranker stubbed out offline."""
    state = types.SimpleNamespace(people_out=workdir, store_dir=os.path.join(workdir, "store"))
    attempt = {"index": 1, "queries_path": os.path.join(workdir, "q.json")}
    real_phase, real_env = harness.run_product_phase, harness.product_env

    def stub_phase(_state, argv, *, label, env):
        with open(argv[argv.index("--out") + 1], "w", encoding="utf-8") as handle:
            json.dump(ranked, handle)
        return {"returncode": 0, "argv": list(argv), "label": label, "stdout": "",
                "stdout_sha256": "offline-stub"}

    harness.run_product_phase, harness.product_env = stub_phase, (lambda _s, *, audit_root=None: {})
    try:
        return harness.rank_supplied_results(state, attempt, supplied_results(observed_urls),
                                             "rank-1")
    finally:
        harness.run_product_phase, harness.product_env = real_phase, real_env


def selection_view(selection):
    matched, diagnostic = selection.get("matched") or {}, selection.get("match_diagnostic") or {}
    item = matched.get("item") or {}
    return dict(zip(FIELDS, (
        matched.get("lane_key"), item.get("candidate_id"), bool(item.get("paths")),
        selection.get("peer_candidates"), selection.get("hiring_adjacent_candidates"),
        selection.get("lanes"), diagnostic.get("reason"),
        (diagnostic.get("item") or {}).get("candidate_id"))))


def expect(*values): return dict(zip(FIELDS, values))


# (case name, ranked document, observed live URLs, expected selection view)
CASES = (
    # Case 1: first observed candidate is pathless, a later one carries paths.
    ("first_observed_pathless_then_pathful_selects_pathful",
     document([candidate("pathless-peer", url("first"), []),
               candidate("pathful-peer", url("second"), ["alumni_at_target"])],
              [candidate("pathful-adjacent", url("adjacent"), ["hiring_adjacent"])]),
     [url("first"), url("second"), url("adjacent")],
     expect("candidates", "pathful-peer", True, 2, 1, ["candidates", "hiring_adjacent"],
            "observed_url_with_empty_paths", "pathless-peer")),
    # Case 2: every observed candidate is pathless, so nothing may be accepted.
    ("only_pathless_observed_yields_no_match",
     document([candidate("pathless-a", url("a"), []), candidate("pathless-b", url("b"), [])], []),
     [url("a"), url("b")],
     expect(None, None, False, 2, 0, ["candidates"], "observed_url_with_empty_paths",
            "pathless-a")),
    # Case 3: pathful candidates whose URLs were never supplied live are not matches.
    ("pathful_but_unobserved_yields_no_match",
     document([candidate("pathful-unobserved", url("unseen"), ["alumni_at_target"])],
              [candidate("adjacent-unobserved", url("other"), ["hiring_adjacent"])]),
     [url("supplied-only")],
     expect(None, None, False, 1, 1, [], None, None)),
    # Case 4: with both lanes qualifying, the peer lane wins.
    ("peer_lane_preferred_when_both_qualify",
     document([candidate("peer", url("peer"), ["prior_employer_at_target"])],
              [candidate("adjacent", url("adjacent"), ["hiring_adjacent"])]),
     [url("peer"), url("adjacent")],
     expect("candidates", "peer", True, 1, 1, ["candidates", "hiring_adjacent"], None, None)),
    # Case 5: the documented fallback lane still locks when no peer qualifies.
    ("hiring_adjacent_fallback_lane_intact",
     document([], [candidate("adjacent", url("adjacent"), ["hiring_adjacent"])]),
     [url("adjacent")],
     expect("hiring_adjacent", "adjacent", True, 0, 1, ["hiring_adjacent"], None, None)),
)


def main():
    workdir = tempfile.mkdtemp(prefix="live-match-selection-")
    rows = []
    for name, ranked, observed_urls, expected in CASES:
        try:
            view = selection_view(run_rank(ranked, observed_urls, workdir))
            passed, detail = view == expected, {"expected": expected, "observed": view}
        except Exception as cause:  # noqa: BLE001 - reported, never swallowed
            passed, detail = False, {"error": repr(cause)}
        rows.append({"case": name, "passed": bool(passed), "detail": detail})
    report = {
        "check": "live-match-selection",
        "command": "python3 tests/e2e/check_live_match_selection.py",
        "offline": True,
        "invariant": "a locked live match is a ranked peer/hiring-adjacent item observed live"
                     " with nonempty evidence-derived `paths`",
        "passed": all(row["passed"] for row in rows),
        "cases": rows,
        "evidence_class": {"offline": True, "fixture": True, "live": False,
                           "note": "ranker stubbed; no live discovery claimed"},
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
