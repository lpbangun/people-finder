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
import re
from datetime import datetime, timezone

from . import SCHEMA_CANDIDATES, SCHEMA_QUERIES, SCHEMA_SERP
from .anchors import (FUNCTION_STOPWORDS, HIRING_ADJACENT_TERMS, RESTRICTED_ANCHOR_TYPES)
from .packs import HIRING_ADJACENT_TERM_WEIGHT, PER_PATH_TOTAL_CAP, PER_TYPE_PER_PATH_CAP
from .results import ResultError
from .textutil import (all_terms_present, find_span, is_public_profile_url,
                       normalize_public_url, norm_phrase, phrase_present,
                       profile_slug_tokens, round_score, split_title, squeeze, tokens)

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


# Eligibility is deliberately evaluated before affinity scoring. A shared school or
# prior employer cannot rescue a result that lacks positional target-company evidence
# or a matching function/level surface.
C_SUITE_TERMS = (
    "chief executive officer", "chief technology officer", "chief operating officer",
    "chief financial officer", "chief product officer", "chief people officer",
    "chief human resources officer", "chief revenue officer", "chief marketing officer",
    "chief information officer", "chief", "ceo", "cto", "coo", "cfo", "cpo", "chro", "cro", "cmo",
    "cio", "co-founder", "cofounder", "founder & ceo", "founder and ceo",
)
LEVEL_TERMS = {
    "head", "director", "vp", "vice president", "manager", "lead", "principal", "staff",
    "senior", "sr", "junior", "jr", "associate", "coordinator", "specialist", "analyst",
    "engineer", "developer", "scientist", "researcher", "designer", "recruiter",
    "representative", "rep", "sdr", "bdr",
}
LEVEL_STOPWORDS = {"and", "of", "the", "for", "a", "an", "to", "in", "with"}
FUNCTION_FAMILIES = {
    "engineering": {"engineer", "engineering", "developer", "development", "software", "backend",
                     "frontend", "platform", "infrastructure", "systems", "devops", "sre", "technical"},
    "data": {"data", "analytics", "analytic", "scientist", "science", "research", "researcher",
             "quantitative", "insights", "machine", "learning", "ml", "fraud"},
    "product": {"product", "pm", "roadmap", "productmanager"},
    "design": {"design", "designer", "ux", "ui", "creative"},
    "go_to_market": {"gtm", "sales", "revenue", "account", "accounts", "business", "development",
                      "partnerships", "partnership", "growth", "marketing", "demand", "brand",
                      "representative", "rep", "sdr", "bdr"},
    "people": {"people", "hr", "human", "resources", "talent", "recruiting", "recruitment",
               "learning", "organizational", "organization", "workplace", "employee"},
    "operations": {"operations", "operation", "ops", "program", "strategy", "workplace"},
    "finance": {"finance", "financial", "accounting", "accountant", "fp", "treasury"},
    "legal": {"legal", "counsel", "compliance", "privacy"},
    "support": {"support", "success", "customer", "services", "service"},
}
STRICT_FUNCTION_FAMILIES = {
    "engineering": {"backend", "devops", "developer", "development", "engineering", "engineer", "frontend", "infrastructure", "platform", "software", "systems", "technical"},
    "data": {"analytics", "analytic", "data", "fraud", "insights", "learning", "machine", "ml", "quantitative", "research", "researcher", "science", "scientist"},
    "design": {"creative", "design", "designer", "ui", "ux"},
    "go_to_market": {"account", "business", "brand", "development", "demand", "growth", "marketing", "partnership", "revenue", "sales", "sdr", "representative"},
    "people": {"employee", "hr", "human", "learning", "people", "recruiting", "recruitment", "resources", "talent", "workplace"},
    "operations": {"operation", "operations", "ops", "program", "strategy", "workplace"},
    "product": {"pm", "product", "roadmap"},
    "support": {"customer", "service", "services", "success", "support"},
}

HIRING_SURFACE_TERMS = tuple(dict.fromkeys((*HIRING_ADJACENT_TERMS, "recruiting", "recruitment", "sourcer")))


_PROFILE_MARKER_RE = re.compile(r"linkedin", re.IGNORECASE)
_PROFILE_VIEW_RE = re.compile(
    r"\bview\s+[^.!?\n]{0,120}?\s+profile\s+on\s+linkedin\b",
    re.IGNORECASE,
)
_PROFILE_LABEL_RE = re.compile(
    r"\b(?:experience|education|employment|work\s+experience|professional\s+experience)\s*:",
    re.IGNORECASE,
)
_PROFILE_EXPERIENCE_RE = re.compile(r"\bexperience\s*:", re.IGNORECASE)


def _profile_boundary_matches(text):
    """Find generic provider profile boundaries in a concatenated title.

    Search providers sometimes flatten several result cards into one title, for
    example ``Name - Company | LinkedInOther Name - Company | LinkedIn``.  The
    marker is deliberately structural rather than provider- or company-specific:
    it must be preceded by a title separator and followed by another profile
    name.  A trailing `| LinkedIn` is therefore retained as an ordinary title
    suffix.
    """
    text = str(text or "")
    matches = []
    for match in _PROFILE_MARKER_RE.finditer(text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip(" |·")
        if not before or before[-1] not in "|·-–—":
            continue
        if after and re.match(r"[^\W\d_]", after, re.UNICODE):
            matches.append(match)
    return matches


def _clean_profile_title_segment(text):
    text = squeeze(text).strip(" |·")
    text = re.sub(r"(?:\s*[|·]\s*)?linkedin\s*$", "", text, flags=re.IGNORECASE)
    return squeeze(text).strip(" |·")


def _profile_title_segments(title):
    """Return title cards in order, preserving the first card as the row lead."""
    text = squeeze(title)
    if not text:
        return []
    segments = []
    cursor = 0
    for match in _profile_boundary_matches(text):
        segment = _clean_profile_title_segment(text[cursor:match.start()])
        if segment:
            segments.append(segment)
        cursor = match.end()
    tail = _clean_profile_title_segment(text[cursor:])
    if tail:
        segments.append(tail)
    return segments or [_clean_profile_title_segment(text)]


def _profile_name(title_segment):
    name, _remainder = split_title(title_segment)
    name = squeeze(name).strip(" |·")
    if " at " in name.casefold():
        name = re.split(r"\s+at\s+", name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    elif re.search(r"\s+@\s*", name):
        name = re.split(r"\s+@\s*", name, maxsplit=1)[0].strip()
    if not name or re.search(r"linkedin", name, re.IGNORECASE) or "|" in name:
        return ""
    return name


def _leading_profile_body_segment(snippet, *, title_segments, candidate_name):
    """Scope a body to the first profile block associated with the row URL.

    The row URL and first title card identify the candidate.  The body is kept
    useful, but stops at generic evidence boundaries used by public indexes:
    another title-card name, a profile-view marker, or a subsequent structured
    experience/education record.  This avoids attaching a neighboring profile's
    positional claims while retaining the first profile's rich narrative.
    """
    text = squeeze(snippet)
    if not text:
        return ""

    boundaries = []
    folded = text.casefold()
    for other_name in title_segments[1:]:
        name = _profile_name(other_name)
        if not name or name.casefold() == candidate_name.casefold():
            continue
        position = folded.find(name.casefold())
        if position > 0:
            boundaries.append(position)

    view_matches = list(_PROFILE_VIEW_RE.finditer(text))
    if view_matches:
        # A profile-view marker is a stable boundary in provider snippets: the
        # lead's preceding narrative remains useful, while text after it may
        # be the next flattened result card even when it repeats the lead name.
        boundaries.append(view_matches[0].start())

    label_positions = [match.start() for match in _PROFILE_LABEL_RE.finditer(text)]
    experience_positions = [match.start() for match in _PROFILE_EXPERIENCE_RE.finditer(text)]
    if len(experience_positions) >= 2:
        # One Experience block may include the lead's Education/Location
        # metadata. The next Experience block is the first unambiguous
        # structured boundary between flattened profile records.
        boundaries.append(experience_positions[1])
    elif len(experience_positions) == 0 and len(label_positions) >= 2:
        prefix = text[:label_positions[0]].strip(" |·")
        boundaries.append(label_positions[1] if not prefix else label_positions[0])

    if not boundaries:
        return text
    return squeeze(text[:min(boundaries)]).strip(" |·")


def _scope_profile_hit(hit):
    """Bind raw title/snippet text to the profile represented by `hit['url_key']`."""
    title_segments = _profile_title_segments(hit.get("title", ""))
    title_segment = title_segments[0] if title_segments else squeeze(hit.get("title", ""))
    candidate_name = _profile_name(title_segment)
    identity_safe = bool(candidate_name)
    raw_snippet = squeeze(hit.get("snippet", ""))
    snippet_segment = _leading_profile_body_segment(
        raw_snippet, title_segments=title_segments, candidate_name=candidate_name,
    ) if identity_safe else ""
    snippet_positional_safe = (
        len(_PROFILE_VIEW_RE.findall(raw_snippet)) <= 1
        and len(_PROFILE_EXPERIENCE_RE.findall(raw_snippet)) <= 1
    )
    return {
        "scoped_title": title_segment,
        "scoped_snippet": snippet_segment,
        "profile_name": candidate_name,
        "profile_identity_safe": identity_safe,
        "profile_title_count": len(title_segments),
        "snippet_positional_safe": snippet_positional_safe,
    }


def _hit_text(hit, *, positional=False):
    snippet = hit.get("scoped_snippet", hit.get("snippet", ""))
    if positional and not hit.get("snippet_positional_safe", True):
        snippet = ""
    return f"{hit.get('scoped_title', hit.get('title', ''))} {snippet}"


def _text_fields(record, *, positional=False):
    fields = []
    for hit in record.get("hits", []):
        if not hit.get("profile_identity_safe", True):
            continue
        title = hit.get("scoped_title", hit.get("title", ""))
        snippet = hit.get("scoped_snippet", hit.get("snippet", ""))
        if title:
            fields.append(("title", title, hit))
        if snippet and (not positional or hit.get("snippet_positional_safe", True)):
            fields.append(("snippet", snippet, hit))
    return fields



def _contains_phrase(text, phrases):
    return [phrase for phrase in phrases if phrase_present(text, phrase)]


def _prior_target_occurrence(normalized, start, company_norm):
    """Identify an employer occurrence explicitly marked as former/past/ex."""
    before = normalized[:start]
    after = normalized[start + len(company_norm):]
    prior_before = re.search(
        r"(?:^|\s)(?:former|formerly|previously|past|ex)"
        r"(?:\s+at)?(?:\s+[a-z0-9]+){0,5}\s*$", before
    )
    prior_after = re.match(r"\s+(?:former|formerly|previously|past)\b", after)
    return bool(prior_before or prior_after)


def _target_evidence(company, record):
    """Return positional current-employer evidence, never URL/name-only matches."""
    evidence = []
    rejected_name_only = False
    company_norm = norm_phrase(company)
    if not company_norm:
        return evidence, rejected_name_only
    target_pattern = re.compile(
        r"(?<![a-z0-9])" + re.escape(company_norm) + r"(?![a-z0-9])"
    )
    for field, text, hit in _text_fields(record, positional=True):
        normalized = norm_phrase(text)
        positions = [match.start() for match in target_pattern.finditer(normalized)]
        if not positions or not phrase_present(text, company):
            continue
        if all(_prior_target_occurrence(normalized, start, company_norm) for start in positions):
            continue
        leading, remainder = split_title(text)
        positional_at_marker = bool(re.search(
            r"(?:\bat\s+|@\s*)" + re.escape(company) + r"(?=$|[^\w])",
            text,
            re.IGNORECASE,
        ))
        in_leading_name = (
            field == "title"
            and phrase_present(leading, company)
            and not phrase_present(remainder, company)
            and phrase_present(hit.get("profile_name", leading), company)
            and not positional_at_marker
        )
        if in_leading_name:
            rejected_name_only = True
            continue
        structured = field == "title" and (leading != text or positional_at_marker)
        if field == "snippet":
            structured = (
                leading != text
                or positional_at_marker
                or phrase_present(text, " works ")
                or phrase_present(text, " employee")
                or phrase_present(text, " current ")
                or phrase_present(text, " team")
                or phrase_present(text, " joined ")
                or phrase_present(text, " employed ")
            )
        if not structured:
            continue
        quote = find_span(text, company)
        if not quote:
            continue
        evidence.append({
            "field": field,
            "quote": quote,
            "source": hit.get("source", ""),
            "source_url": hit.get("source_url", ""),
            "observed_at": hit.get("retrieved_at", "") or hit.get("observed_at", ""),
            "position": hit.get("position", ""),
        })
    unique = []
    seen = set()
    for row in evidence:
        key = (row["field"], row["quote"], row["position"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique, rejected_name_only

_AGGREGATE_PROFILE_RE = re.compile(
    r"(?:\.\.\.|…)\s*[A-ZÀ-ÖØ-öø-ÿ][^\n]{0,100}(?:[-–—]|\s+at\s+|\s+@\s+)",
    re.IGNORECASE,
)


def _candidate_name_present(text, name):
    """Return whether the candidate's meaningful name tokens co-locate."""
    wanted = {
        token.casefold()
        for token in re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", str(name or ""))
        if len(token) > 1
    }
    observed = {
        token.casefold()
        for token in re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", str(text or ""))
    }
    return bool(wanted) and wanted.issubset(observed)


def _target_company_marker_present(text, company):
    """Check for a positional employer marker in one result segment."""
    company = squeeze(company)
    if not company:
        return False
    folded = str(text or "")
    company_match = re.search(
        r"(?<![\w])" + re.escape(company) + r"(?![\w])", folded, re.IGNORECASE
    )
    if not company_match:
        return False
    after = folded[company_match.end():]
    if re.match(
        r"\s+(?:AI|AI-powered|Corp(?:oration)?|Engineering|Group|Inc|Labs?|LLC|Media|Networks?|Security|Solutions?|Systems?|Technolog(?:y|ies)|TV)\b",
        after,
        re.IGNORECASE,
    ):
        return False
    before = folded[:company_match.start()]
    if re.search(r"\b(?:former|formerly|past|previously|ex)\s*$", before, re.IGNORECASE):
        return False
    if re.search(
        r"(?:\bat\s+|@\s*|experience:\s*|experiencia:\s*|currently\s+|joined\s+|employed\s+at\s+|[-–—|]\s*)$",
        before,
        re.IGNORECASE,
    ):
        return True
    return bool(re.search(r"\b(?:employee|employed|staff|team)\s*[:\-]?\s*$", before, re.IGNORECASE))


def _strict_company_marker(text, company, candidate):
    """Return an exact current-employer quote from one textual segment."""
    company = squeeze(company)
    if not company:
        return ""
    candidate_norm = " ".join(
        token.casefold()
        for token in re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", str(candidate or ""))
        if len(token) > 1
    )
    pattern = re.compile(
        r"(?<![\w])" + re.escape(company) + r"(?![\w])", re.IGNORECASE
    )
    continuation_words = {
        "ai", "corp", "corporation", "engineering", "group", "inc", "labs", "llc",
        "media", "networks", "security", "solutions", "systems", "technology",
        "technologies", "tv",
    }
    for match in pattern.finditer(str(text or "")):
        before = str(text or "")[:match.start()]
        after = str(text or "")[match.end():]
        continuation = re.match(r"[A-Za-z][\w-]*", after.lstrip())
        if continuation and (
            continuation.group(0).casefold() in continuation_words
            or continuation.group(0)[:1].isupper()
        ):
            continue
        if candidate_norm and " ".join(
            token.casefold()
            for token in re.findall(r"[\wÀ-ÖØ-öø-ÿ]+", before)
            if len(token) > 1
        ) == candidate_norm:
            continue
        prefix = before[-100:]
        if re.search(
            r"(?:\bat\s*|@\s*|experience\s*:\s*|current(?:ly)?\s+(?:at\s+)?|joined\s+|employed\s+at\s+|[-–—|]\s*)$",
            prefix,
            re.IGNORECASE,
        ):
            if re.search(r"\b(?:former|formerly|previously|past|ex)\b", prefix[-50:], re.IGNORECASE):
                continue
            return match.group(0)
        if re.search(r"\b(?:employee|team|staff)\b", after[:50], re.IGNORECASE):
            return match.group(0)
    return ""
def _strict_live_segments(hit):
    """Return separate title/snippet segments; never join independent fields."""
    segments = []
    title = squeeze(hit.get("title", hit.get("scoped_title", "")))
    snippet = squeeze(hit.get("snippet", hit.get("scoped_snippet", "")))
    for field, text in (("title", title), ("snippet", snippet)):
        if not text:
            continue
        if field == "title":
            parts = [_clean_profile_title_segment(part) for part in re.split(r"(?:\.\.\.|…|\s*\|\s*LinkedIn)", text, flags=re.IGNORECASE) if _clean_profile_title_segment(part)]
        else:
            parts = re.split(
                r"(?:\.\.\.|…|\s*\|\s*LinkedIn|\s*[·•]\s*|(?<=[.!?])\s+|(?<=[.!?])(?=[A-ZÀ-ÖØ-öø-ÿ]))",
                text,
                flags=re.IGNORECASE,
            )
        for part in parts:
            part = squeeze(part).strip(" |·•")
            if part:
                segments.append({"field": field, "text": part})
    return segments
def _strict_segment_function_evidence(target, text, hit):
    """Return target-title family/level evidence from this one segment."""
    title = squeeze(target.get("title", ""))
    stopwords = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
    raw_target = set(tokens(title)) - stopwords
    functional_target = raw_target - LEVEL_TERMS
    target_families = {family for family, vocabulary in STRICT_FUNCTION_FAMILIES.items() if set(tokens(title)).intersection(vocabulary)}
    segment_tokens = set(tokens(text))
    segment_families = {family for family, vocabulary in STRICT_FUNCTION_FAMILIES.items() if segment_tokens.intersection(vocabulary)}
    role_overlap = sorted(functional_target.intersection(segment_tokens))
    family_overlap = sorted(target_families.intersection(segment_families))
    level_terms = sorted(segment_tokens.intersection({
        "associate", "analyst", "bdr", "ceo", "chief", "coordinator", "cto", "cfo", "cmo", "coo", "cpo", "cro",
        "designer", "developer", "director", "engineer", "head", "intern", "jr", "junior", "lead", "manager",
        "principal", "recruiter", "researcher", "representative", "rep", "scientist", "senior", "specialist", "sdr",
        "staff", "vp", "vice", "president",
    }))
    if not (role_overlap or family_overlap) or not level_terms:
        return None
    normalized = norm_phrase(text)
    if any(phrase_present(normalized, term) for term in C_SUITE_TERMS):
        return None
    if any(phrase_present(normalized, term) for term in HIRING_SURFACE_TERMS):
        return None
    level = _level_info(text)
    return {
        "field": "",
        "quote": text,
        "source": hit.get("source", ""),
        "source_url": hit.get("source_url", ""),
        "observed_at": hit.get("retrieved_at", "") or hit.get("observed_at", ""),
        "position": hit.get("position", ""),
        "match_kind": "function_head" if level["class"] == "function_head" else "function_peer",
        "level": level["class"],
        "role_terms": role_overlap,
        "function_families": family_overlap,
        "level_terms": level_terms,
    }


def _strict_peer_colocation(target, hit):
    """Require identity, current target, and function/level in one segment."""
    broad_target_evidence, name_only = _target_evidence(
        target.get("company", ""), {"hits": [hit]}
    )
    profile_name = hit.get("profile_name") or _profile_name(
        hit.get("scoped_title", hit.get("title", ""))
    )
    segments = _strict_live_segments(hit)
    if not profile_name:
        return {
            "passed": False,
            "name_only_target_match": name_only,
            "target_company_evidence": broad_target_evidence,
            "function_evidence": [],
            "segments": [item["text"] for item in segments],
        }
    for item in segments:
        text = item["text"]
        if not _candidate_name_present(text, profile_name):
            continue
        company_quote = _strict_company_marker(text, target.get("company", ""), profile_name)
        if not company_quote:
            continue
        function_row = _strict_segment_function_evidence(target, text, hit)
        if function_row is None:
            continue
        function_row["field"] = item["field"]
        target_row = {
            "field": item["field"],
            "quote": company_quote,
            "source": hit.get("source", ""),
            "source_url": hit.get("source_url", ""),
            "observed_at": hit.get("retrieved_at", "") or hit.get("observed_at", ""),
            "position": hit.get("position", ""),
        }
        return {
            "passed": True,
            "name_only_target_match": False,
            "target_company_evidence": [target_row],
            "function_evidence": [function_row],
            "segments": [text],
        }
    return {
        "passed": False,
        "name_only_target_match": name_only,
        "target_company_evidence": broad_target_evidence,
        "function_evidence": [],
        "segments": [item["text"] for item in segments],
    }

def _function_families(text):
    words = set(tokens(text))
    families = {family for family, vocabulary in FUNCTION_FAMILIES.items() if words.intersection(vocabulary)}
    normalized = norm_phrase(text)
    if "go to market" in normalized or "go to market" in normalized.replace("-", " "):
        families.add("go_to_market")
    if "human resources" in normalized:
        families.add("people")
    if "business operations" in normalized:
        families.add("operations")
    return families


def _target_function_info(target):
    title = squeeze(target.get("title", ""))
    department = squeeze(target.get("department", ""))
    title_words = set(tokens(title)) - LEVEL_STOPWORDS - LEVEL_TERMS
    department_words = set(tokens(department)) - LEVEL_STOPWORDS - LEVEL_TERMS
    families = _function_families(f"{title} {department}")
    return {
        "title": title,
        "department": department,
        "title_words": title_words,
        "department_words": department_words,
        "families": families,
    }


def _level_info(text):
    normalized = norm_phrase(text)
    c_suite = any(phrase_present(normalized, term) for term in C_SUITE_TERMS)
    if c_suite:
        return {"class": "c_suite", "observed": _contains_phrase(text, C_SUITE_TERMS)}
    if any(phrase_present(normalized, term) for term in ("head", "director", "vp", "vice president", "manager")):
        level = "function_head"
    elif any(phrase_present(normalized, term) for term in ("senior", "staff", "principal", "lead", "associate", "intern")):
        level = "role_peer_with_level"
    else:
        level = "role_peer_level_unspecified"
    return {"class": level, "observed": _contains_phrase(text, tuple(sorted(LEVEL_TERMS)))}


def _function_level_evidence(target, record):
    info = _target_function_info(target)
    candidates = []
    for field, text, hit in _text_fields(record, positional=True):
        words = set(tokens(text))
        core_overlap = sorted(info["title_words"].intersection(words))
        department_overlap = sorted(info["department_words"].intersection(words))
        family_overlap = sorted(info["families"].intersection(_function_families(text)))
        role_overlap = sorted(set(core_overlap).union(department_overlap))
        level = _level_info(text)
        if not (family_overlap or len(role_overlap) >= 2):
            continue
        if not role_overlap and not family_overlap:
            continue
        # A snippet must carry an explicit role/level phrase; a bare department
        # mention is not enough to establish a function peer.
        if not level["observed"]:
            continue
        if level["class"] == "c_suite":
            continue
        is_head = level["class"] == "function_head"
        match_kind = "function_head" if is_head else "function_peer"
        candidates.append({
            "field": field,
            "quote": text,
            "source": hit.get("source", ""),
            "source_url": hit.get("source_url", ""),
            "observed_at": hit.get("retrieved_at", "") or hit.get("observed_at", ""),
            "position": hit.get("position", ""),
            "match_kind": match_kind,
            "level": level["class"],
            "role_terms": role_overlap,
            "function_families": family_overlap,
            "level_terms": level["observed"],
        })
    # A function head may be evidenced by department family alone; a peer needs
    # a role/function overlap, which prevents an alumnus in another department
    # from qualifying solely on a shared stamp.
    return candidates, info


def _record_eligibility(target, record):
    target_evidence, name_only = _target_evidence(target.get("company", ""), record)
    all_text = " ".join(text for _field, text, _hit in _text_fields(record, positional=True))
    levels = [_level_info(text) for _field, text, _hit in _text_fields(record, positional=True)]
    c_suite = any(item["class"] == "c_suite" for item in levels)

    peer_matches = []
    for hit in record.get("hits", []):
        if not hit.get("profile_identity_safe", True):
            continue
        match = _strict_peer_colocation(target, hit)
        if match["passed"]:
            peer_matches.append(match)

    # Evidence from a peer candidate is only taken from a hit that passed the
    # shared predicate. This prevents later URL merging from manufacturing a
    # target/function intersection across unrelated live rows.
    strict_target_evidence = [
        row
        for match in peer_matches
        for row in match["target_company_evidence"]
    ]
    strict_function_evidence = [
        row
        for match in peer_matches
        for row in match["function_evidence"]
    ]

    def unique_rows(rows):
        unique = []
        seen = set()
        for row in rows:
            key = (
                row.get("field", ""),
                row.get("quote", ""),
                row.get("position", ""),
                row.get("match_kind", ""),
            )
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    strict_target_evidence = unique_rows(strict_target_evidence)
    strict_function_evidence = unique_rows(strict_function_evidence)
    base = {
        "status": "ineligible",
        "lane": None,
        "reason_code": "",
        "target_company_evidence": target_evidence,
        "function_evidence": [],
        "level_evidence": [],
        "rationale": "",
        "c_suite": c_suite,
        "name_only_target_match": bool(name_only and not target_evidence),
        "selection_eligible": False,
        "co_location": {
            "status": "passed" if peer_matches else "failed",
            "live_result_count": len(peer_matches),
            "positions": sorted({
                row.get("position", "")
                for row in strict_target_evidence + strict_function_evidence
                if row.get("position", "")
            }),
        },
    }
    if not target_evidence:
        base["reason_code"] = "name_only_target_match" if name_only else "target_company_evidence_missing"
        base["rationale"] = "Excluded: positional current target-company evidence was not observed in supplied search text."
        return base
    if c_suite:
        base["reason_code"] = "c_suite_excluded"
        base["rationale"] = "Excluded: a C-suite level was observed; executive backfill is outside peer eligibility."
        base["level_evidence"] = [
            {"field": field, "quote": text, "level": "c_suite"}
            for field, text, _hit in _text_fields(record, positional=True)
            if _level_info(text)["class"] == "c_suite"
        ]
        return base

    hiring_terms = _contains_phrase(all_text, HIRING_SURFACE_TERMS)
    if hiring_terms:
        base["status"] = "eligible"
        base["lane"] = LANE_HIRING_ADJACENT
        base["reason_code"] = "hiring_adjacent"
        base["selection_eligible"] = True
        base["name_only_target_match"] = False
        base["function_evidence"] = [
            {
                "field": field, "quote": text, "surface_terms": hiring_terms,
                "source": _hit.get("source", ""), "source_url": _hit.get("source_url", ""),
                "observed_at": _hit.get("retrieved_at", "") or _hit.get("observed_at", ""),
            }
            for field, text, _hit in _text_fields(record, positional=True)
            if _contains_phrase(text, HIRING_SURFACE_TERMS)
        ]
        base["level_evidence"] = [
            {
                "field": field, "quote": text, "level": _level_info(text)["class"],
                "source": _hit.get("source", ""), "source_url": _hit.get("source_url", ""),
                "observed_at": _hit.get("retrieved_at", "") or _hit.get("observed_at", ""),
            }
            for field, text, _hit in _text_fields(record, positional=True)
            if _contains_phrase(text, HIRING_SURFACE_TERMS)
        ]
        base["rationale"] = "Eligible in the hiring-adjacent lane: current target-company evidence and a hiring surface are both observed; it cannot reorder peer leads."
        return base

    if not peer_matches:
        base["reason_code"] = "wrong_function_or_level"
        base["rationale"] = (
            "Excluded: no single live result segment co-located the candidate name, "
            "current target-company marker, and same-function or level evidence."
        )
        return base

    base["status"] = "eligible"
    base["lane"] = LANE_PEER
    base["reason_code"] = strict_function_evidence[0]["match_kind"]
    base["target_company_evidence"] = strict_target_evidence
    base["function_evidence"] = strict_function_evidence
    base["level_evidence"] = [
        {"field": row["field"], "quote": row["quote"], "level": row["level"],
         "level_terms": row["level_terms"]}
        for row in strict_function_evidence
    ]
    base["selection_eligible"] = True
    base["name_only_target_match"] = False
    base["rationale"] = (
        "Eligible as a same-function peer or relevant function head: one live result "
        "segment co-locates the candidate name, current target-company attribution, "
        "function evidence, and observed role level."
    )
    return base


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _candidate_id(url_key):
    digest = hashlib.sha1(url_key.encode("utf-8")).hexdigest()[:12]
    return f"cand_{digest}"


def _match_anchor(anchor, title, snippet, slug_text):
    """Return the observed fields for one anchor, or [] when unobserved.

    A department-derived function anchor is only counted when a result title
    carries it: a short department phrase inside a snippet is not role evidence.
    Role-family query variants are aliases of the same supplied job-title
    anchor; they never add a new seeker fact.
    """
    attributes = anchor.get("attributes", {})
    scope = attributes.get("match_scope", "title_or_snippet")
    values = [anchor["value"]] + list(attributes.get("query_variants", []))
    observed = []
    seen_values = set()
    for value in values:
        normalized = norm_phrase(value)
        if not normalized or normalized in seen_values:
            continue
        seen_values.add(normalized)
        if scope in ("title", "title_or_snippet") and phrase_present(title, value):
            observed.append("title")
        if scope in ("snippet", "title_or_snippet") and phrase_present(snippet, value):
            observed.append("snippet")
        if scope != "title" and slug_text and phrase_present(slug_text, value):
            observed.append("url")
    if observed:
        return sorted(set(observed))
    if anchor["type"] == "function":
        term_sets = []
        canonical_terms = attributes.get("terms") or []
        if canonical_terms:
            term_sets.append(canonical_terms)
        for value in attributes.get("query_variants", []):
            variant_terms = [term for term in tokens(value) if term not in FUNCTION_STOPWORDS]
            if variant_terms:
                term_sets.append(variant_terms)
        for terms in term_sets:
            # The all-terms fallback must be satisfied inside one observed field.
            if scope in ("title", "title_or_snippet") and all_terms_present(title, terms):
                return ["title:function_terms"]
            if scope in ("snippet", "title_or_snippet") and all_terms_present(snippet, terms):
                return ["snippet:function_terms"]
    return []


def _match_lexicon_terms(pack, text):
    return [term for term in pack.get("lexicon", []) if phrase_present(text, term)]


def _target_observed(anchor_value, title, snippet, url_key, slug_text):
    """Compatibility helper returning only positional current-employer fields."""
    del url_key, slug_text
    evidence, _name_only = _target_evidence(anchor_value, {
        "hits": [{"title": title, "snippet": snippet}],
    })
    return evidence[0]["field"] if evidence else ""


STAMP_PATHS = ("alumni_at_target", "prior_employer_at_target", "community_at_target")


def _fire_paths(hit, packs):
    """Evidence-derived path firing: a path fires only on observed facts.

    shared_stamp is a lower-tier proxy, so it fires only when no target-anchored
    stamp path already counted the same stamps in this result.
    """
    fired = []
    text = _hit_text(hit, positional=True)
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
                terms.update(_match_lexicon_terms(pack, _hit_text(hit, positional=True)))

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
            hit = {
                "url_key": url_key,
                "url_observed": row["url"],
                "title": row["title"],
                "snippet": row["snippet"],
                "pack_id": pack_id,
                "position": row["position"],
                "rank_in_pack": row["rank_in_pack"],
                "source": results_source,
                "source_url": row.get("source_url", ""),
                "retrieved_at": (row.get("observed_at", "") or entry.get("retrieved_at", "")
                                  or results_doc.get("retrieved_at", "")),
                "slug_text": " ".join(profile_slug_tokens(url_key)),
            }
            hit.update(_scope_profile_hit(hit))
            hits.append(hit)

    # ---- phase 2: anchor observations + idf ---------------------------------
    total_hits = len(hits)
    for hit in hits:
        observed = {}
        for anchor in anchors:
            if not anchor["used_in_ranking"]:
                continue
            fields = (
                _match_anchor(
                    anchor,
                    hit["scoped_title"],
                    hit["scoped_snippet"] if hit["snippet_positional_safe"] else "",
                    hit["slug_text"],
                )
                if hit["profile_identity_safe"] else []
            )
            if fields:
                observed[anchor["anchor_id"]] = {"type": anchor["type"], "fields": fields}
        hit["anchors"] = observed
        target_evidence, name_only = _target_evidence(target_company, {"hits": [hit]})
        hit["target_evidence"] = target_evidence
        hit["target_name_only_match"] = name_only
        hit["target_observed_in"] = sorted({row["field"] for row in target_evidence})
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
        elif hit["target_observed_in"] or hit["target_name_only_match"] or hit["anchors"]:
            # Retain target-attributed, untyped, name-only, or anchor-bearing rows
            # long enough for the eligibility gate to emit a precise reason.
            lane = LANE_PEER
        else:
            suppressed.append({
                "url": hit["url_observed"],
                "observed_in": [hit["position"]],
                "packs_observed": [hit["pack_id"]],
                "reason": "target_employer_not_observed_in_supplied_result",
            })
            continue
        record = merged.setdefault(hit["url_key"], {
            "url_key": hit["url_key"],
            "lane": lane,
            "paths": set(),
            "anchors_matched": {},
            "hits": [],
            "target_observed_in": set(),
            "target_evidence": [],
            "retrieved_at": set(),
            "url_observed_in": [],
        })
        if lane == LANE_HIRING_ADJACENT:
            record["lane"] = LANE_HIRING_ADJACENT
        record["paths"].update(path for path, _ in hit["paths"])
        record["hits"].append(hit)
        record["url_observed_in"].append(f"{results_source}#{hit['position']}")
        if hit["target_observed_in"]:
            record["target_observed_in"].update(hit["target_observed_in"])
        record["target_evidence"].extend(hit.get("target_evidence", []))
        if hit.get("retrieved_at"):
            record["retrieved_at"].add(hit["retrieved_at"])
        for anchor_id, info in hit["anchors"].items():
            slot = record["anchors_matched"].setdefault(anchor_id, {"fields": set(), "packs": set()})
            slot["fields"].update(info["fields"])
            slot["packs"].add(hit["pack_id"])

    # ---- phase 4: eligibility before affinity scoring -----------------------
    candidates = []
    for url_key, record in merged.items():
        eligibility = _record_eligibility(target, record)
        if (eligibility["status"] != "eligible"
                or (eligibility["lane"] == LANE_PEER and eligibility["selection_eligible"] is not True)):
            observed_positions = sorted({hit["position"] for hit in record["hits"]})
            observed_packs = sorted({hit["pack_id"] for hit in record["hits"]})
            observed_url = record["hits"][0]["url_observed"] if record["hits"] else url_key
            suppressed.append({
                "url": observed_url,
                "observed_in": observed_positions,
                "packs_observed": observed_packs,
                "reason": eligibility["reason_code"],
                "eligibility_rationale": eligibility["rationale"],
                "target_company_evidence": eligibility["target_company_evidence"],
            })
            continue

        # Hiring surfaces are explicitly retained in their own lane even when a
        # result also happens to carry a peer anchor. This prevents a recruiter
        # or hiring surface from displacing a same-function peer.
        record["eligibility"] = eligibility
        record["lane"] = eligibility["lane"]
        if record["lane"] == LANE_HIRING_ADJACENT:
            record["paths"] = {path for path in record["paths"] if path == "hiring_adjacent"}
            if not record["paths"]:
                record["paths"] = {"hiring_adjacent"}
        else:
            record["paths"] = {path for path in record["paths"] if path != "hiring_adjacent"}

        matched_ids = sorted(record["anchors_matched"])
        score, breakdown, per_path = _score_candidate(record, packs, anchors_by_id, idf)

        names = []
        headlines = []
        for hit in record["hits"]:
            headline = hit.get("scoped_title") or hit.get("scoped_snippet")
            if headline and headline not in headlines:
                headlines.append(headline)
            name = hit.get("profile_name") or split_title(hit.get("scoped_title", ""))[0]
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

        target_evidence = []
        target_seen = set()
        for row in eligibility["target_company_evidence"]:
            key = (row.get("field", ""), row.get("quote", ""), row.get("position", ""))
            if key not in target_seen:
                target_seen.add(key)
                target_evidence.append(row)
        observed_at_target = {
            "status": "observed",
            "observed_in": sorted(record["target_observed_in"]),
            "retrieved_at": sorted(record["retrieved_at"]),
            "evidence": target_evidence,
        }

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
                "evidence": target_evidence,
            },
            "observed_at_target": observed_at_target,
            "selection_eligible": bool(eligibility["selection_eligible"]),
            "eligibility": eligibility,
            "function_level_rationale": eligibility["rationale"],
            "function_level": {
                "rationale": eligibility["rationale"],
                "function_evidence": eligibility["function_evidence"],
                "level_evidence": eligibility["level_evidence"],
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

    selection_shortfalls = []
    if not peer:
        selection_shortfalls.append({
            "code": "no_eligible_peer",
            "reason": (
                "No same-function peer or relevant function head met the current target-company "
                "and function-level evidence requirements."
            ),
        })
    execution = compiled.get("execution") if isinstance(compiled.get("execution"), dict) else {}
    observed_reason_counts = {}
    for item in suppressed:
        reason = item.get("reason", "unknown")
        observed_reason_counts[reason] = observed_reason_counts.get(reason, 0) + 1
    selected_count = len(peer) + len(hiring_adjacent)

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
        "manifest": {
            "role": target.get("title", ""),
            "inputs": {
                "resume": compiled.get("seeker", {}).get("source_resume", ""),
                "job": target.get("job_source", ""),
                "queries": queries_source,
                "results": results_source,
            },
            "route": {
                "backend": results_doc.get("backend", "recorded_fixture"),
                "network_calls": 0,
                "retry_count": 0,
                "terminal_route_failure": None,
            },
            "budgets": {
                "max_queries": execution.get("query_budget", 12),
                "queries_compiled": sum(len(pack.get("queries", [])) for pack in compiled.get("packs", [])),
                "queries_skipped": sum(len(pack.get("queries_skipped", [])) for pack in compiled.get("packs", [])),
                "query_skip_reason": execution.get("query_skip_reason"),
                "max_wall_clock_seconds": 900,
                "wall_clock_seconds": None,
                "within_wall_clock_budget": None,
            },
            "queries": {
                "available": sum(len(pack.get("queries", [])) for pack in compiled.get("packs", [])),
                "supplied_hits": total_hits,
            },
            "leads": {
                "discovery_qualified": selected_count,
                "peer": len(peer),
                "hiring_adjacent": len(hiring_adjacent),
            },
            # Zero is intentional: this discovery stage does not attribute or
            # guess addresses and has not run a mailbox check.
            "emails": 0,
            "contact_outcomes": {
                "attributed": 0,
                "mailbox": "not_checked",
                "brief_acceptance": "not_attempted",
                "guessed_addresses_excluded": True,
                "deliverability_claimed": False,
            },
            "blockers": selection_shortfalls,
            "persistence": {"writes": False, "state": "not_performed"},
        },
        "execution": {
            "query_budget": execution.get("query_budget", 12),
            "queries_available": sum(len(pack.get("queries", [])) for pack in compiled.get("packs", [])),
            "queries_skipped": sum(len(pack.get("queries_skipped", [])) for pack in compiled.get("packs", [])),
            "supplied_hits": total_hits,
            "network_calls": 0,
            "retry_count": 0,
            "terminal_route_failure": None,
            "within_query_budget": bool(execution.get("within_query_budget", True)),
        },
        "eligibility_policy": {
            "order": "current_target_company_then_function_and_level_then_affinity",
            "peer_requirement": "same-role/function peer or relevant function head",
            "c_suite": "excluded",
            "hiring_adjacent": "separate lane; cannot displace peers",
            "name_only_target_match": "excluded",
        },
        "selection": {
            "peer_count": len(peer),
            "hiring_adjacent_count": len(hiring_adjacent),
            "shortfalls": selection_shortfalls,
            "suppressed_reason_counts": dict(sorted(observed_reason_counts.items())),
        },
        "selection_shortfalls": selection_shortfalls,
        "outcome_counters": {
            "discovery_qualified_leads": selected_count,
            "attributed_email": 0,
            "mailbox_outcome": {"status": "not_checked", "checked": 0, "not_checked": selected_count},
            "brief_acceptance_status": {"status": "not_attempted", "attempted": 0, "not_attempted": selected_count},
            "guessed_addresses_excluded": True,
            "deliverability_claimed": False,
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
                "eligibility requires current target-company evidence plus same-function peer or "
                "relevant function-head evidence before affinity scoring; hiring-adjacent surfaces "
                "remain in their own lane and cannot displace peers"
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
