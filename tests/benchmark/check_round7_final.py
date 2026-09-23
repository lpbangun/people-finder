#!/usr/bin/env python3
"""Round-7 adversarial, sparse-role, and retained-live replay coverage."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import product_command
RESUME = ROOT / "tests" / "fixtures" / "resumes" / "a-rich-stamps.md"
AT = "2026-09-18T00:00:00Z"
RETAINED = Path("/home/logani/projects/people-finder-worktrees/people-pipeline-critic-r6")

STOPWORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
LEVELS = {
    "associate", "analyst", "bdr", "ceo", "chief", "coordinator", "cto", "cfo", "cmo", "coo", "cpo", "cro",
    "designer", "developer", "director", "engineer", "head", "intern", "jr", "junior", "lead", "manager",
    "principal", "recruiter", "researcher", "representative", "rep", "scientist", "senior", "specialist", "sdr",
    "staff", "vp", "vice", "president",
}
FAMILIES = {
    "engineering": {"backend", "developer", "development", "engineering", "engineer", "frontend", "infrastructure", "platform", "software", "systems", "technical"},
    "data": {"analytics", "analytic", "data", "fraud", "insights", "learning", "machine", "ml", "quantitative", "research", "researcher", "science", "scientist"},
    "design": {"creative", "design", "designer", "ui", "ux"},
    "gtm": {"account", "business", "brand", "development", "demand", "growth", "marketing", "partnership", "revenue", "sales", "sdr", "representative"},
    "people": {"employee", "hr", "human", "learning", "people", "recruiting", "recruitment", "resources", "talent", "workplace"},
    "operations": {"operation", "operations", "ops", "program", "strategy", "workplace"},
    "product": {"pm", "product", "roadmap"},
    "support": {"customer", "service", "services", "success", "support"},
}
HIRING = ("talent acquisition", "technical recruiter", "recruiter", "hiring manager", "people partner", "talent partner")
C_SUITE = ("chief", "ceo", "cto", "coo", "cfo", "cpo", "cro", "cmo", "cio", "founder")


def clean_env():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(key, None)
        if upper in {"PLUGIN_DATA", "DATABASE_URL"}:
            env.pop(key, None)
    return env


def run(argv):
    return subprocess.run([str(value) for value in argv], cwd=ROOT, env=clean_env(), capture_output=True, text=True, timeout=90)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def tokens(value):
    return [item.casefold() for item in re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", str(value or ""))]


def squeeze(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def url_key(value):
    parsed = urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").casefold()
    path = (parsed.path or "").rstrip("/")
    if not host.endswith("linkedin.com") or not path.casefold().startswith("/in/"):
        return ""
    return f"https://www.linkedin.com{path}".casefold()


def title_cards(value):
    text = squeeze(value)
    return [squeeze(re.sub(r"\s*LinkedIn\s*$", "", part, flags=re.IGNORECASE))
            for part in re.split(r"(?:\.\.\.|…|\s*\|\s*LinkedIn)", text, flags=re.IGNORECASE)
            if squeeze(part)]


def inferred_name(title):
    card = (title_cards(title) or [squeeze(title)])[0]
    for separator in (" – ", " — ", " - ", " | ", " · ", "•"):
        if separator in card:
            return card.split(separator, 1)[0].strip()
    match = re.match(r"^(.+?)\s+(?:at|@)\s+.+$", card, flags=re.IGNORECASE)
    return match.group(1).strip() if match else card


def row_segments(row):
    result = []
    for field, value in (("title", row.get("title", "")), ("snippet", row.get("snippet", ""))):
        text = squeeze(value)
        if not text:
            continue
        if field == "title":
            parts = title_cards(text)
        else:
            parts = re.split(r"(?:\.\.\.|…|\s*\|\s*LinkedIn|\s*[·•]\s*|(?<=[.!?])\s+|(?<=[.!?])(?=[A-ZÀ-ÖØ-öø-ÿ]))", text, flags=re.IGNORECASE)
        result.extend({"field": field, "text": squeeze(part).strip(" |·•")}
                     for part in parts if squeeze(part).strip(" |·•"))
    return result


def exact_company_marker(segment, company, candidate):
    company = squeeze(company)
    candidate_norm = " ".join(item for item in tokens(candidate) if len(item) > 1)
    continuation_words = {"ai", "corp", "corporation", "engineering", "group", "inc", "labs", "llc", "media", "networks", "security", "solutions", "systems", "technology", "technologies", "tv"}
    pattern = re.compile(r"(?<![\w])" + re.escape(company) + r"(?![\w])", re.IGNORECASE)
    for match in pattern.finditer(segment):
        before = segment[:match.start()]
        after = segment[match.end():]
        continuation = re.match(r"[A-Za-z][\w-]*", after.lstrip())
        if continuation and (continuation.group(0).casefold() in continuation_words or continuation.group(0)[:1].isupper()):
            continue
        if candidate_norm and " ".join(item for item in tokens(before) if len(item) > 1) == candidate_norm:
            continue
        prefix = before[-100:]
        if re.search(r"(?:\bat\s*|@\s*|experience\s*:\s*|current(?:ly)?\s+(?:at\s+)?|joined\s+|employed\s+at\s+|[-–—|]\s*)$", prefix, re.IGNORECASE):
            if re.search(r"\b(?:former|formerly|previously|past|ex)\b", prefix[-50:], re.IGNORECASE):
                continue
            return True
        if re.search(r"\b(?:employee|team|staff)\b", after[:50], re.IGNORECASE):
            return True
    return False


def family_level_peer(segment, target_title):
    raw = set(tokens(target_title)) - STOPWORDS
    functional = raw - LEVELS
    target_families = {family for family, words in FAMILIES.items() if raw.intersection(words)}
    observed = set(tokens(segment))
    observed_families = {family for family, words in FAMILIES.items() if observed.intersection(words)}
    overlap = functional.intersection(observed)
    family_overlap = target_families.intersection(observed_families)
    level_terms = observed.intersection(LEVELS)
    normalized = " ".join(tokens(segment))
    if not (overlap or family_overlap) or not level_terms:
        return False
    if any(re.search(r"\b" + re.escape(term) + r"\b", normalized) for term in C_SUITE):
        return False
    if any(term in normalized for term in HIRING):
        return False
    return True


def independent_peer_urls(rows, company, target_title):
    peers = set()
    for row in rows:
        key = url_key(row.get("url"))
        candidate = inferred_name(row.get("title", ""))
        name_tokens = [item for item in tokens(candidate) if len(item) > 1]
        if not key or not name_tokens:
            continue
        for item in row_segments(row):
            text = item["text"]
            if not set(name_tokens).issubset(set(tokens(text))):
                continue
            if exact_company_marker(text, company, candidate) and family_level_peer(text, target_title):
                peers.add(key)
                break
    return peers


def rows_for_pack(pack_id, rows):
    if pack_id != "function_at_target":
        return []
    return [{"rank": index, "url": url, "title": title, "snippet": snippet,
             "source_url": "https://search.synthetic.example/round7", "observed_at": AT}
            for index, (url, title, snippet) in enumerate(rows, 1)]


def retained_artifact(role, suffix):
    manifest = json.loads((RETAINED / ".oprun" / "smoke-runs" / role / "manifest.json").read_text(encoding="utf-8"))
    relative = next(item for item in manifest["persistence"]["artifacts"] if str(item).endswith(suffix))
    path = Path(relative)
    return path if path.is_absolute() else RETAINED / path


def retained_rows(live):
    return [row for pack in live.get("pack_results", []) for row in pack.get("results", [])]


def main():
    assertions = []

    def check(identifier, passed, evidence):
        assertions.append({"id": identifier, "passed": bool(passed), "evidence": evidence})

    with tempfile.TemporaryDirectory(prefix="people-finder-round7-") as raw_dir:
        workdir = Path(raw_dir)
        job_path = workdir / "job.json"
        queries_path = workdir / "queries.json"
        results_path = workdir / "results.json"
        candidates_path = workdir / "candidates.json"
        write_json(job_path, {
            "schema": "job-card.v1", "fictional": True, "job_id": "synthetic-round7-sparse",
            "title": "Senior UX Researcher, Qualitative", "company": "Aurora Labs",
            "department": "Data and research", "location": "Remote",
            "posting_text": "Synthetic sparse-role replay coverage.",
        })
        compile_run = run(product_command("compile", "--resume", RESUME, "--job", job_path, "--at", AT, "--out", queries_path, "--quiet"))
        queries = json.loads(queries_path.read_text(encoding="utf-8")) if compile_run.returncode == 0 else {}
        selected = [row for pack in queries.get("packs", []) for row in pack.get("queries", [])]
        function_pack = next((pack for pack in queries.get("packs", []) if pack.get("pack_id") == "function_at_target"), {})
        function_queries = [row.get("query", "") for row in function_pack.get("queries", [])]
        normalized_queries = [" ".join(str(value).casefold().split()) for value in function_queries]
        check("R7-query-plan-keeps-reordered-sparse-role-and-positional-employer", (
            compile_run.returncode == 0 and len(selected) <= 12
            and any("qualitative ux researcher" in value for value in normalized_queries)
            and any('"at aurora labs"' in value for value in normalized_queries)
        ), {"compile_returncode": compile_run.returncode, "selected_count": len(selected), "function_queries": function_queries})

        synthetic_rows = [
            ("https://www.linkedin.com/in/avery-valid", "Avery Valid - Qualitative UX Researcher at Aurora Labs | LinkedIn", ""),
            ("https://www.linkedin.com/in/briar-cross", "Briar Cross - Aurora Labs | LinkedIn", "Briar Cross is a Qualitative UX Researcher."),
            ("https://www.linkedin.com/in/cleo-cross", "Cleo Cross - Aurora Labs | LinkedIn", "Cleo Cross profile. Another Person is a Qualitative UX Researcher at Aurora Labs."),
            ("https://www.linkedin.com/in/aurora-labs", "Aurora Labs - Qualitative UX Researcher | LinkedIn", "Aurora Labs has experience in research."),
            ("https://www.linkedin.com/in/drew-continuation", "Drew Continuation - Qualitative UX Researcher at Aurora Labs Engineering | LinkedIn", ""),
            ("https://www.linkedin.com/in/evan-former", "Evan Former - Qualitative UX Researcher | LinkedIn", "Evan Former was formerly at Aurora Labs. Qualitative UX Researcher."),
            ("https://www.linkedin.com/in/faye-chief", "Faye Chief - Senior UX Researcher at Aurora Labs, CEO | LinkedIn", ""),
            ("https://www.linkedin.com/in/gina-recruiter", "Gina Recruiter - UX Researcher recruiter at Aurora Labs | LinkedIn", ""),
            ("https://www.linkedin.com/in/hana-sparse", "Hana Sparse - Qualitative UX Researcher at Aurora Labs | LinkedIn", ""),
        ]
        pack_results = [{"pack_id": pack.get("pack_id"), "query": pack.get("query", ""), "retrieved_at": AT, "results": rows_for_pack(pack.get("pack_id"), synthetic_rows)} for pack in queries.get("packs", [])]
        write_json(results_path, {"schema": "recorded-serp.v1", "fixture": "synthetic-round7", "backend": "synthetic_public_index", "live_network": False, "retrieved_at": AT, "pack_results": pack_results})
        rank_run = run(product_command("rank", "--queries", queries_path, "--results", results_path, "--at", AT, "--out", candidates_path, "--quiet"))
        candidates = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.exists() else {}
        emitted_peer = {url_key(item.get("public_url")) for item in candidates.get("candidates", []) if url_key(item.get("public_url"))}
        oracle_peer = independent_peer_urls([row for pack in pack_results for row in pack["results"]], "Aurora Labs", "Senior UX Researcher, Qualitative")
        hiring_names = {item.get("name") for item in candidates.get("hiring_adjacent", [])}
        check("R7-adversarial-emitted-peer-flags-equal-independent-same-segment-oracle", rank_run.returncode == 0 and emitted_peer == oracle_peer, {
            "rank_returncode": rank_run.returncode, "emitted_peer_urls": sorted(emitted_peer), "oracle_peer_urls": sorted(oracle_peer), "stderr": rank_run.stderr[-500:],
        })
        names = {item.get("name") for item in candidates.get("candidates", [])}
        check("R7-adversarial-cross-segment-name-only-continuation-former-recruiter-csuite-rejected", (
            {"Avery Valid", "Hana Sparse"}.issubset(names)
            and not {"Briar Cross", "Cleo Cross", "Aurora Labs", "Drew Continuation", "Evan Former", "Faye Chief", "Gina Recruiter"}.intersection(names)
            and "Gina Recruiter" in hiring_names
        ), {"peer_names": sorted(name for name in names if name), "hiring_names": sorted(name for name in hiring_names if name), "suppressed": candidates.get("suppressed", [])})

        roles = ["role-01", "role-06", "role-09", "role-15", "role-17"]
        replay = []
        for role in roles:
            queries_in = retained_artifact(role, "people-queries.json")
            live_in = retained_artifact(role, "live-results.json")
            out = workdir / f"{role}-replay.json"
            replay_run = run(product_command("rank", "--queries", queries_in, "--results", live_in, "--at", AT, "--out", out, "--quiet"))
            ranked = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
            live = json.loads(live_in.read_text(encoding="utf-8"))
            metadata = json.loads((RETAINED / ".oprun" / "smoke-runs" / role / "manifest.json").read_text(encoding="utf-8"))["metadata"]
            oracle = independent_peer_urls(retained_rows(live), metadata.get("company", ""), metadata.get("title", ""))
            emitted = {url_key(item.get("public_url")) for item in ranked.get("candidates", []) if url_key(item.get("public_url"))}
            replay.append({"role": role, "returncode": replay_run.returncode, "emitted": emitted, "oracle": oracle})
        check("R7-retained-live-replay-emitted-flags-agree-with-independent-oracle", all(item["returncode"] == 0 and item["emitted"] == item["oracle"] for item in replay), {
            "roles": [{"role": item["role"], "returncode": item["returncode"], "emitted": sorted(item["emitted"]), "oracle": sorted(item["oracle"])} for item in replay], "retained_root": str(RETAINED),
        })

    payload = {"benchmark": "people-finder-v1", "criterion": "R7", "offline": True, "passed": all(item["passed"] for item in assertions), "assertions": assertions, "test_count": len(assertions)}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"check_round7_final harness error: {exc!r}\n")
        print(json.dumps({"benchmark": "people-finder-v1", "criterion": "R7", "offline": True, "passed": False, "assertions": [{"id": "HARNESS", "passed": False, "evidence": {"error": repr(exc)}}], "test_count": 1}, sort_keys=True))
        sys.exit(2)
