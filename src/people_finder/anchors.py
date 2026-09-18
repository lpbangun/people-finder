"""Typed anchor extraction from resume text and a target job card.

The "vector" here is a sparse bag of typed anchors — never an embedding. Every
anchor carries the exact source fragment it was read from, so a reviewer can
trace it back into the supplied fixture text.
"""

import json
import os
import re

from .textutil import norm_phrase, phrase_present, squeeze, tokens

# ---------------------------------------------------------------------------
# Anchor weights (ARCHITECTURE.md "Ranking: sparse typed anchors, not embeddings")
# ---------------------------------------------------------------------------

WEIGHTS = {
    "rare_community": 3.0,   # lab / OSS org / conference / paper / niche program
    "prior_employer": 2.6,   # a company the seeker actually worked at
    "school": 2.2,           # rare school; generic schools are downweighted
    "program": 1.4,          # degree program, secondary to the school
    "function": 1.0,         # role family / department keywords from the job
    "target_employer": 0.0,  # required filter for peer packs, never a score
    "location": 0.3,         # optional, low signal, not used by compiled packs
    "skill": 0.0,            # default off: "Python" drowns the list
}

GENERIC_SCHOOL_WEIGHT = 0.5

SECTION_ALIASES = {
    "education": (
        "education", "academics", "academic", "academic background", "studies",
        "schooling", "education and learning", "learning and education", "education learning",
    ),
    "experience": (
        "experience", "work experience", "professional experience", "employment",
        "employment history", "work history", "career", "career history", "roles",
        "professional background",
    ),
    "community": ("community", "open source", "open-source", "oss", "public work",
                  "speaking", "talks", "conferences", "volunteering"),
    "skills": (
        "skills", "core skills", "technical skills", "competencies", "capabilities",
        "tools", "technologies", "tech stack", "stack",
    ),
    "projects": (
        "projects", "selected work", "selected projects", "papers", "publications",
        "research", "thesis work",
    ),
}

ORGANIZATION_SUFFIXES = (
    "University",
    "Universität",
    "Universite",
    "Institute",
    "Institut",
    "College",
    "Academy",
    "Polytechnic",
    "School",
)

_CONNECTORS = r"(?:of|the|and|for|de|du|di|von|at|in)"
_WORD = r"[A-Z][A-Za-z0-9'&.\-]*"
_SCHOOL_SUFFIX = r"(?:" + "|".join(re.escape(item) for item in ORGANIZATION_SUFFIXES) + r")"

# Education records appear in both common word orders. Keep these expressions
# bounded by punctuation so a school never absorbs a city, date or the next
# field on a resume line. The old suffix-only expression missed the common
# ``University of X`` form.
_SCHOOL_PREFIX_RE = re.compile(
    r"\b(University\s+of\s+" + _WORD
    + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*)",
)
_SCHOOL_SUFFIX_RE = re.compile(
    r"\b(" + _WORD
    + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*\s+"
    + _SCHOOL_SUFFIX
    + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*)",
)

# Backwards-compatible matcher name; it now covers both University-of-X and
# X-University organization orders while retaining the historical group(1) API.
SCHOOL_RE = re.compile(
    r"\b((?:University\s+of\s+" + _WORD
    + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*|"
    + _WORD + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*\s+"
    + _SCHOOL_SUFFIX
    + r"(?:\s+(?:" + _CONNECTORS + r"\s+)?" + _WORD + r")*))"
)

DEGREE_RE = re.compile(
    r"\b(Ph\.?\s?D|Doctorate|M\.?\s?S\.?c?|M\.?\s?Sc|M\.?\s?Eng|Master(?:'s)?|MBA|"
    r"B\.?\s?S\.?c?|B\.?\s?Sc|B\.?\s?Eng|Bachelor(?:'s)?)\b\.?\s*(?:of|in)?\s*"
    r"([A-Za-z][A-Za-z /&+\-]{2,60}?)(?=\s*,|\s*\(|\s*$)",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b|\bpresent\b|\bcurrent\b|\bongoing\b",
    re.IGNORECASE,
)

NON_EMPLOYER_MARKERS = (
    "self-employed",
    "self employed",
    "freelance",
    "freelancer",
    "independent",
    "contract",
    "contractor",
    "consulting (own practice)",
    "various",
    "unknown",
    # Common non-employer fragments that appear after achievement verbs or as
    # continuation headings in real resume exports.
    "learning materials", "implementation plans", "self service documentation",
    "job interviews", "soft skills training", "candidate screening",
    "skills assessment", "launch preparation", "stakeholder coordination",
    "vendor selection", "post training evaluation", "tools", "documentation",
)


EMPLOYMENT_SENTENCE_STARTS = (
    "assisted", "built", "conducted", "created", "delivered", "designed", "developed",
    "drove", "founded", "handled", "improved", "implemented", "launched", "led",
    "managed", "owned", "partnered", "supported", "translated", "worked", "served",
    "coordinated", "contributed", "oversaw", "organized", "published", "researched",
)

LOCATION_MARKERS = {
    "remote", "onsite", "on site", "hybrid", "new york", "new york city", "san francisco",
    "los angeles", "seattle", "boston", "chicago", "toronto", "london", "berlin",
    "lisbon", "jakarta", "medan", "bengaluru", "india", "indonesia", "canada", "usa",
    "united states", "united kingdom", "eu", "europe",
}

ROLE_TERMS = (
    "engineer", "engineering", "developer", "development", "scientist", "researcher",
    "research", "designer", "design", "manager", "director", "head", "lead", "chief",
    "officer", "founder", "intern", "associate", "analyst", "architect", "coordinator",
    "specialist", "recruiter", "operations", "people", "talent", "sales", "marketing",
    "product", "finance", "legal", "support", "consultant", "administrator",
)
SENIORITY_WORDS = {
    "senior", "staff", "principal", "lead", "junior", "associate", "mid", "entry",
    "intern", "internship", "chief", "vp", "vice", "president", "head", "director",
    "ii", "iii", "iv", "sr", "jr",
}

# These are query-language variants, not additional seeker facts. They let a
# job title such as ``People Ops Manager`` reach the same public role family as
# ``People Operations Manager`` without changing the evidence-backed anchor
# value. The reverse forms are useful because public profile headlines often
# abbreviate a long role name even when the posting does not.
FUNCTION_QUERY_EXPANSIONS = (
    ("rev ops", "revenue operations"),
    ("revops", "revenue operations"),
    ("gtm", "go to market"),
    ("ops", "operations"),
    ("hr", "human resources"),
)
FUNCTION_QUERY_COMPACTIONS = (
    ("human resources", "hr"),
    ("revenue operations", "revops"),
    ("go to market", "gtm"),
    ("operations", "ops"),
    ("representative", "rep"),
)
# Public profile headlines often use a short initialism for a longer function
# phrase. These are query-language aliases only: ranking still needs the
# observed company and positional function/level evidence before a lead is
# eligible. Keep the set deliberately small and deterministic so this is a
# bounded recall aid rather than an ontology or a guessed fact.
FUNCTION_INITIALISM_EXPANSIONS = (
    ("ux", "user experience"),
    ("ui", "user interface"),
    ("ml", "machine learning"),
    ("ai", "artificial intelligence"),
    ("it", "information technology"),
    ("gtm", "go to market"),
    ("ops", "operations"),
    ("hr", "human resources"),
    ("cs", "customer success"),
    ("cx", "customer experience"),
    ("sdr", "sales development representative"),
)
FUNCTION_PHRASE_REWRITES = (
    ("people ops", "people operations"),
    ("people operations", "human resources operations"),
    ("user research", "ux research"),
    ("user experience", "ux"),
)
# This is a per-anchor candidate limit before the global run budget is
# applied. It prevents an unusual title from producing an unbounded plan;
# MAX_QUERIES_PER_RUN in packs.py remains the only host execution cap.
FUNCTION_QUERY_VARIANT_LIMIT = 8
TITLE_LEVEL_WORDS = SENIORITY_WORDS | {
    "manager", "coordinator", "specialist", "analyst", "representative", "officer",
}

FUNCTION_STOPWORDS = {"and", "of", "the", "for", "a", "an", "to", "in", "with"}

GENERIC_SKILLS = {
    "python", "sql", "java", "javascript", "typescript", "git", "rest", "apis",
    "api", "excel", "communication", "leadership", "agile", "scrum", "linux",
    "docker", "kubernetes", "aws", "azure", "gcp", "node", "react", "html",
    "css", "go", "matlab", "tableau", "spark", "airflow", "dbt", "pandas",
    "numpy", "postgres", "postgresql", "mysql", "mongodb", "js", "bash",
}

# Generic school fragments that must not be treated as a rare stamp.
GENERIC_SCHOOL_TOKENS = {
    "university", "college", "institute", "school", "academy", "polytechnic",
    "state university", "national university", "technical university",
}

# Hiring-adjacent lane lexicon (defined by the pack, not by the seeker profile).
HIRING_ADJACENT_TERMS = (
    "talent acquisition",
    "technical recruiter",
    "recruiter",
    "hiring manager",
    "people partner",
    "talent partner",
)

RESTRICTED_ANCHOR_TYPES = ("rare_community", "school", "program", "prior_employer")


class AnchorError(ValueError):
    """Raised for malformed supplied input (maps to process exit code 2)."""


def _anchor_id(anchor_type, index):
    return f"anchor_{anchor_type}_{index:02d}"


def _make_anchor(anchor_type, value, evidence, *, weight=None, generic=False,
                 used_in_ranking=True, default_off=False, attributes=None, index=1):
    value = squeeze(value)
    anchor = {
        "anchor_id": _anchor_id(anchor_type, index),
        "type": anchor_type,
        "value": value,
        "normalized": norm_phrase(value),
        "weight": WEIGHTS[anchor_type] if weight is None else weight,
        "generic": bool(generic),
        "used_in_ranking": bool(used_in_ranking),
        "default_off": bool(default_off),
        "attributes": attributes or {},
        "evidence": evidence,
        "provenance": dict(evidence.get("provenance") or {}),
    }
    return anchor


def _evidence(source, field, line_number, quote, *, section="other", dialect="plain",
              source_line_offset=None):
    line = int(line_number or 0)
    offset = source_line_offset if source_line_offset is not None else max(line - 1, 0)
    provenance = {
        "section": squeeze(section) or "other",
        "line_offset": offset,
        "source_line_offset": offset,
        "dialect": squeeze(dialect) or "plain",
        "line": line,
    }
    return {
        "source": source,
        "field": field,
        "line": line,
        "line_offset": offset,
        "dialect": provenance["dialect"],
        "quote": squeeze(quote),
        "provenance": provenance,
    }


def _section_label(heading):
    """Return a section only for a standalone alias label."""
    normalized = norm_phrase(re.sub(r"[*_`]+", "", str(heading or "")).rstrip(" :"))
    for section, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return section

    return "other"


def _section_of(heading):
    normalized = norm_phrase(re.sub(r"[*_`]+", "", str(heading or "")).rstrip(" :"))
    for section, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return section
    return "other"


def _new_section(name, heading="", level=0, heading_alias=False):
    return {
        "name": name,
        "heading": heading,
        "heading_level": level,
        "heading_alias": bool(heading_alias),
        "dialect": "heading_alias" if heading_alias else "heading",
        "entry_kinds": [],
        "lines": [],
        "entries": [],
    }


def _append_entry(section, number, value, kind, *, heading_level=0):
    value = squeeze(value)
    if not value:
        return
    entry = {
        "line": number,
        "line_offset": max(number - 1, 0),
        "text": value,
        "kind": kind,
        "dialect": kind,
        "heading_level": heading_level,
    }
    section["lines"].append((number, value))
    section["entries"].append(entry)
    if kind not in section["entry_kinds"]:
        section["entry_kinds"].append(kind)
    if len(section["entry_kinds"]) > 1:
        section["dialect"] = "mixed"
    elif section.get("heading_alias"):
        section["dialect"] = "heading_alias"
    else:
        section["dialect"] = kind


def parse_resume(text):
    """Parse resume text into {name, labeled, sections:[{name, lines:[(n, text)]}]}."""
    name = None
    labeled = {}
    labeled_lines = {}
    sections = []
    current = _new_section("profile")
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            section_name = _section_of(heading)
            if section_name != "other":
                current = _new_section(
                    section_name, heading, level,
                    heading_alias=norm_phrase(heading) != section_name,
                )
                sections.append(current)
            elif name is None and not sections and level <= 1 and heading:
                name = heading
            else:
                _append_entry(current, number, heading, "heading", heading_level=level)
            continue
        bullet = stripped.lstrip("-*•").strip()
        content = re.sub(r"^(?:[*_])+|(?:[*_])+$", "", bullet).strip()
        section_name = _section_label(content)
        if section_name != "other":
            current = _new_section(section_name, content, 0, heading_alias=True)
            sections.append(current)
            continue
        label_match = re.match(r"^([A-Za-z][A-Za-z /]{2,24}):\s*(.+)$", content)
        if label_match and current["name"] in ("profile", "other"):
            label = label_match.group(1).strip().lower()
            labeled[label] = label_match.group(2).strip()
            labeled_lines[label] = number
        if name is None and current["name"] == "profile" and not label_match:
            name = squeeze(content) or None
        kind = "bullet" if stripped[:1] in "-*•" else "plain"
        _append_entry(current, number, content, kind)
    return {"name": name or "", "labeled": labeled, "labeled_lines": labeled_lines, "sections": sections}


def _iter_entries(section):
    entries = section.get("entries")
    if entries:
        return entries
    return [
        {"line": number, "line_offset": max(number - 1, 0), "text": line,
         "kind": "plain", "dialect": "plain", "heading_level": 0}
        for number, line in section.get("lines", [])
    ]


def _entry_evidence(source, field, section, entry):
    section_dialect = section.get("dialect", "plain")
    dialect = section_dialect if section.get("heading_alias") or section_dialect == "mixed" else entry.get("dialect", section_dialect)
    return _evidence(
        source, field, entry["line"], entry["text"],
        section=section.get("name", "other"),
        dialect=dialect,
        source_line_offset=entry.get("line_offset"),
    )


DEGREE_PREFIX_RE = re.compile(
    r"^(?:ph\.?\s*d|m\.?\s*(?:s|sc|eng)|b\.?\s*(?:a|s|sc|eng)|mba|master(?:'s)?|bachelor(?:'s)?)\.?\s+",
    re.IGNORECASE,
)


def _school_matches(line):
    candidates = []
    for expression in (_SCHOOL_PREFIX_RE, _SCHOOL_SUFFIX_RE):
        for match in expression.finditer(line):
            candidates.append((match.start(1), -len(match.group(1)), match.group(1)))
    selected = []
    occupied = []
    for start, _negative_length, value in sorted(candidates, key=lambda item: (item[0], item[1])):
        end = start + len(value)
        if any(start < right and end > left for left, right in occupied):
            continue
        value = squeeze(DEGREE_PREFIX_RE.sub("", value))
        if not value:
            continue
        selected.append(value)
        occupied.append((start, end))
    return selected


def _extract_school_anchors(section, source, anchors, counters):
    for entry in _iter_entries(section):
        line = entry["text"]
        for school in _school_matches(line):
            degree = ""
            program = ""
            degree_match = DEGREE_RE.search(line)
            if degree_match:
                degree = squeeze(degree_match.group(1))
                program = squeeze(degree_match.group(2)).strip(" ,")
            if program and norm_phrase(program) == norm_phrase(school):
                program = ""
            generic = norm_phrase(school) in GENERIC_SCHOOL_TOKENS
            counters["school"] += 1
            anchors.append(_make_anchor(
                "school", school, _entry_evidence(source, "resume:education", section, entry),
                weight=GENERIC_SCHOOL_WEIGHT if generic else None,
                generic=generic, index=counters["school"],
                attributes={"degree": degree, "program": program},
            ))
            if program:
                counters["program"] += 1
                anchors.append(_make_anchor(
                    "program", program, _entry_evidence(source, "resume:education", section, entry),
                    index=counters["program"],
                    attributes={"degree": degree, "school": school},
                ))


def _plain_markup(text):
    return squeeze(re.sub(r"[*_`]+", "", str(text or "")))


def _looks_like_role(text):
    normalized = norm_phrase(text)
    return any(phrase_present(normalized, term) for term in ROLE_TERMS)


def _looks_like_location(text):
    normalized = norm_phrase(text)
    return any(phrase_present(normalized, marker) for marker in LOCATION_MARKERS)


def _starts_sentence_verb(text):
    words = tokens(text)
    return bool(words) and words[0] in EMPLOYMENT_SENTENCE_STARTS


def _record_parts(text):
    return [
        squeeze(part)
        for part in re.split(r"\s*(?:\||\s+-\s+|\s+–\s+|\s+—\s+|,)\s*", text)
        if squeeze(part)
    ]


def _clean_company_candidate(value):
    value = _plain_markup(value)
    value = re.sub(r"^(?:company|employer)\s*:\s*", "", value, flags=re.IGNORECASE)
    value = re.split(r"\s+(?:at|@)\s+", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = squeeze(value.strip(" ,;:|–—-"))
    if "," in value:
        first, _separator, _rest = value.partition(",")
        if first and (len(value.split()) > 1 or not _looks_like_location(first)):
            value = squeeze(first)
    normalized = norm_phrase(value)
    if not normalized or not re.search(r"[A-Za-z]", value):
        return ""
    if any(normalized == norm_phrase(marker)
           or normalized.startswith(norm_phrase(marker) + " ")
           for marker in NON_EMPLOYER_MARKERS):
        return value
    if _looks_like_location(value):
        return ""
    return value


def _employment_record(entry, next_entry=None):
    text = _plain_markup(entry.get("text", ""))
    if not text or _starts_sentence_verb(text):
        return None
    period = next((part for part in _record_parts(text) if DATE_RE.search(part)), "")
    kind = entry.get("kind", "plain")
    if kind == "heading":
        if period and _looks_like_role(text):
            return None
        heading_parts = _record_parts(text)
        role_first = (
            len(heading_parts) >= 2
            and _looks_like_role(heading_parts[0])
            and not _looks_like_location(heading_parts[1])
            and not DATE_RE.search(heading_parts[1])
        )
        if role_first:
            base = heading_parts[1]
        else:
            base = re.split(r"\s*(?:\||\s+-\s+|\s+–\s+|\s+—\s+)\s*", text, maxsplit=1)[0]
        company = _clean_company_candidate(base)
        role = ""
        if next_entry and next_entry.get("kind") != "heading":
            next_text = _plain_markup(next_entry.get("text", ""))
            if _looks_like_role(next_text) and not _starts_sentence_verb(next_text):
                role = next_text
                next_period = next((part for part in _record_parts(next_text) if DATE_RE.search(part)), "")
                period = next_period or period
        if company:
            return {"company": company, "role": role, "period": period, "shape": "heading"}
        return None
    if text.endswith("."):
        return None
    parts = _record_parts(text)
    if len(parts) < 2:
        return None
    non_date = [part for part in parts if not DATE_RE.search(part)]
    if not non_date:
        return None
    if len(non_date) == 1 and _looks_like_role(non_date[0]):
        return None
    role = ""
    if _looks_like_role(non_date[0]) and len(non_date) >= 2:
        role = non_date[0]
        company = non_date[1]
    else:
        company = non_date[0]
        if len(non_date) > 1 and _looks_like_role(non_date[1]):
            role = non_date[1]
    company = _clean_company_candidate(company)
    if not company:
        return None
    return {"company": company, "role": role, "period": period, "shape": "record"}


def _extract_employer_anchors(section, source, anchors, counters, unknowns):
    entries = _iter_entries(section)
    for index, entry in enumerate(entries):
        next_entry = entries[index + 1] if index + 1 < len(entries) else None
        record = _employment_record(entry, next_entry)
        if not record:
            continue
        employer = record["company"]
        evidence = _entry_evidence(source, "resume:experience", section, entry)
        if any(norm_phrase(employer) == norm_phrase(marker)
               or norm_phrase(employer).startswith(norm_phrase(marker) + " ")
               for marker in NON_EMPLOYER_MARKERS):
            unknowns.append({
                "anchor_type": "prior_employer",
                "status": "unknown",
                "reason": "only self-employment or non-employer text recorded in supplied resume",
                "evidence": evidence,
            })
            continue
        counters["prior_employer"] += 1
        anchors.append(_make_anchor(
            "prior_employer", employer, evidence,
            index=counters["prior_employer"],
            attributes={
                "role_observed": record["role"],
                "period": record["period"],
                "record_shape": record["shape"],
            },
        ))


def _extract_community_anchors(section, source, anchors, counters):
    for entry in _iter_entries(section):
        line = entry["text"]
        parts = [squeeze(part) for part in line.split(",") if squeeze(part)]
        value = parts[1] if len(parts) > 1 else parts[0] if parts else ""
        detail = ""
        if "(" in value:
            value, _, tail = value.partition("(")
            detail = squeeze(tail.rstrip(")"))
            value = squeeze(value)
        if not value or not re.search(r"[A-Za-z]", value):
            continue
        counters["rare_community"] += 1
        anchors.append(_make_anchor(
            "rare_community", value, _entry_evidence(source, "resume:community", section, entry),
            index=counters["rare_community"],
            attributes={"role_observed": parts[0] if len(parts) > 1 else "", "detail": detail},
        ))


def _extract_skill_anchors(section, source, anchors, counters):
    for entry in _iter_entries(section):
        line = entry["text"]
        for item in re.split(r"[,;|/]", line):
            value = squeeze(item)
            if not value or not re.search(r"[A-Za-z]", value):
                continue
            counters["skill"] += 1
            anchors.append(_make_anchor(
                "skill", value, _entry_evidence(source, "resume:skills", section, entry),
                generic=norm_phrase(value) in GENERIC_SKILLS,
                used_in_ranking=False, default_off=True, index=counters["skill"],
                attributes={"generic_skill": norm_phrase(value) in GENERIC_SKILLS},
            ))


def _replace_function_terms(value, replacements):
    result = squeeze(value)
    for source, replacement in replacements:
        result = re.sub(
            r"(?<![A-Za-z])" + re.escape(source) + r"(?![A-Za-z])",
            replacement,
            result,
            flags=re.IGNORECASE,
        )
    return squeeze(result)


def _title_function_phrase(value):
    return " ".join(
        word for word in re.findall(r"[A-Za-z][A-Za-z+.#\-]*", squeeze(value))
        if norm_phrase(word) not in SENIORITY_WORDS
    )


def _function_query_variants(title, phrase):
    """Return bounded, deterministic role-family aliases for a job title.

    The canonical anchor remains the exact job-title evidence. Variants only
    remove a trailing specialization or level word, translate common title
    abbreviations, and add a conservative initialism. This makes public-index
    retrieval resilient to title formatting and provider zero-yield variants
    while leaving ranking and eligibility evidence-backed.
    """
    raw_title = squeeze(title)
    parts = re.split(r"\s*(?:[,;:]|\(|\[|\s+[–—-]\s+)\s*", raw_title, maxsplit=1)
    prefix = _title_function_phrase(parts[0])
    suffix = _title_function_phrase(parts[1]) if len(parts) > 1 else ""
    prefix_tokens = [token for token in tokens(parts[0]) if token not in FUNCTION_STOPWORDS]
    prefix_level_only = bool(prefix_tokens) and all(
        token in TITLE_LEVEL_WORDS for token in prefix_tokens
    )

    variants = []
    seen = set()

    def add(value):
        value = squeeze(value)
        normalized = norm_phrase(value)
        if not value or normalized == norm_phrase(phrase) or normalized in seen:
            return
        seen.add(normalized)
        variants.append(value)

    def add_rewrite(value, replacements):
        rewritten = _replace_function_terms(value, replacements)
        if norm_phrase(rewritten) != norm_phrase(value):
            add(rewritten)

    def add_initialism_forms(value):
        for source, replacement in FUNCTION_INITIALISM_EXPANSIONS:
            add_rewrite(value, ((source, replacement),))
        for source, replacement in FUNCTION_PHRASE_REWRITES:
            add_rewrite(value, ((source, replacement),))

        words = [word for word in re.findall(r"[A-Za-z][A-Za-z+.#\-]*", value)
                 if norm_phrase(word) not in FUNCTION_STOPWORDS]
        if len(words) >= 2 and all(len(word) > 1 for word in words):
            acronym = "".join(word[0] for word in words).upper()
            if 2 <= len(acronym) <= 6:
                add(acronym)

    def add_forms(value):
        add_rewrite(value, FUNCTION_QUERY_EXPANSIONS)
        add(value)
        add_rewrite(value, FUNCTION_QUERY_COMPACTIONS)
        add_initialism_forms(value)

        # A provider may index the family without the trailing level token.
        # Keep at least two meaningful terms so a level-only or one-word query
        # cannot consume the bounded plan with a generic search.
        words = [word for word in re.findall(r"[A-Za-z][A-Za-z+.#\-]*", value)
                 if norm_phrase(word) not in FUNCTION_STOPWORDS]
        if len(words) >= 3 and norm_phrase(words[-1]) in TITLE_LEVEL_WORDS | {
            "engineer", "developer", "scientist", "researcher", "designer",
            "architect", "administrator", "consultant",
        }:
            add(" ".join(words[:-1]))

    # A prefix with a real function stem is the stable role-family query for
    # titles like "Sales Development Representative, Early Stage".
    if prefix and (not suffix or len(tokens(prefix)) >= 2):
        add_forms(prefix)
    # For titles shaped like "Manager, Customer Success", put the function
    # before the level token so the query matches normal public headlines.
    if suffix and (not prefix or prefix_level_only):
        add_forms(squeeze(f"{suffix} {prefix}"))
    if suffix and prefix and not prefix_level_only:
        add_forms(squeeze(f"{suffix} {prefix}"))
    # Preserve a compacted/expanded form of the full title when it is the only
    # useful variant. The canonical anchor value is appended by pack compiler.
    add_forms(phrase)
    return variants[:FUNCTION_QUERY_VARIANT_LIMIT]


def _function_anchors(job, source, anchors, counters):
    title = squeeze(job.get("title", ""))
    company = squeeze(job.get("company", ""))
    department = squeeze(job.get("department", ""))
    if not company:
        raise AnchorError("target job card is missing a company")
    title_words = [
        word for word in re.findall(r"[A-Za-z][A-Za-z+.#\-]*", title)
        if norm_phrase(word) not in SENIORITY_WORDS
    ]
    phrase = " ".join(title_words)
    if phrase:
        counters["function"] += 1
        anchors.append(_make_anchor(
            "function", phrase, _evidence(source, "job:title", 0, title, section="target_job", dialect="job_card"),
            index=counters["function"],
            attributes={
                "role_family": "target role family from supplied job title",
                "match_scope": "title_or_snippet",
                "terms": [tok for tok in tokens(phrase) if tok not in FUNCTION_STOPWORDS],
                "query_variants": _function_query_variants(title, phrase),
                "query_variant_policy": (
                    "bounded role-family variants derived from the supplied title; they do not "
                    "create a new seeker fact"
                ),
                "seniority_stripped": [w for w in re.findall(r"[A-Za-z]+", title)
                                       if norm_phrase(w) in SENIORITY_WORDS],
            },
        ))
    if department and norm_phrase(department) != norm_phrase(phrase):
        counters["function"] += 1
        anchors.append(_make_anchor(
            "function", department, _evidence(source, "job:department", 0, department, section="target_job", dialect="job_card"),
            weight=0.9, index=counters["function"],
            attributes={
                "role_family": "department keyword from supplied job card",
                "match_scope": "title",
                "query_variants": _function_query_variants(department, department),
                "query_variant_policy": (
                    "bounded aliases derived from the supplied department; they do not "
                    "create a new seeker fact"
                ),
                "why": ("a short department phrase matched inside a snippet is not role "
                        "evidence, so it only counts when a supplied result title carries it"),
            },
        ))
    counters["target_employer"] += 1
    anchors.append(_make_anchor(
        "target_employer", company, _evidence(source, "job:company", 0, company, section="target_job", dialect="job_card"),
        index=counters["target_employer"],
        attributes={"role": "required_filter_for_peer_packs"},
    ))
    return {"title": title, "company": company, "department": department,
            "location": squeeze(job.get("location", ""))}


def load_job_card(path):
    """Read one job card JSON. Only supplied local files; no network path exists."""
    if not path or not os.path.isfile(path):
        raise AnchorError(f"job card not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            job = json.load(handle)
    except (OSError, ValueError) as cause:
        raise AnchorError(f"job card is not readable JSON: {path} ({cause})") from cause
    if not isinstance(job, dict):
        raise AnchorError(f"job card must be a JSON object: {path}")
    for field in ("title", "company"):
        if not squeeze(job.get(field, "")):
            raise AnchorError(f"job card is missing required field '{field}': {path}")
    return job


def read_text_file(path, label):
    if not path or not os.path.isfile(path):
        raise AnchorError(f"{label} not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as cause:
        raise AnchorError(f"{label} is not readable: {path} ({cause})") from cause


def extract_anchors(resume_text, job, *, resume_source="<supplied resume>",
                    job_source="<supplied job card>"):
    """Extract typed anchors, target info and explicit unknown facts."""
    parsed = parse_resume(resume_text)
    anchors = []
    unknowns = []
    counters = {key: 0 for key in WEIGHTS}
    sections = {}
    for section in parsed["sections"]:
        sections.setdefault(section["name"], []).append(section)

    if sections.get("education"):
        for section in sections["education"]:
            _extract_school_anchors(section, resume_source, anchors, counters)
    else:
        unknowns.append({
            "anchor_type": "school",
            "status": "unknown",
            "reason": "no education section present in supplied resume text",
        })
    if sections.get("experience"):
        for section in sections["experience"]:
            _extract_employer_anchors(section, resume_source, anchors, counters, unknowns)
    else:
        unknowns.append({
            "anchor_type": "prior_employer",
            "status": "unknown",
            "reason": "no experience section present in supplied resume text",
        })
    if sections.get("community"):
        for section in sections["community"]:
            _extract_community_anchors(section, resume_source, anchors, counters)
    else:
        unknowns.append({
            "anchor_type": "rare_community",
            "status": "unknown",
            "reason": "no community or open-source section present in supplied resume text",
        })
    if sections.get("skills"):
        for section in sections["skills"]:
            _extract_skill_anchors(section, resume_source, anchors, counters)
    else:
        unknowns.append({
            "anchor_type": "skill",
            "status": "unknown",
            "reason": "no skills section present in supplied resume text",
        })

    target = _function_anchors(job, job_source, anchors, counters)

    location = parsed["labeled"].get("location", "")
    if location and counters["location"] == 0:
        counters["location"] += 1
        location_line = parsed.get("labeled_lines", {}).get("location", 0)
        anchors.append(_make_anchor(
            "location", location, _evidence(
                resume_source, "resume:header", location_line, location,
                section="profile", dialect="labeled",
            ),
            used_in_ranking=False, index=counters["location"],
            attributes={"role": "optional_low_signal_not_required_by_any_pack"},
        ))
    elif not location:
        unknowns.append({
            "anchor_type": "location",
            "status": "unknown",
            "reason": "no location recorded in supplied resume text",
        })

    for anchor_type in ("school", "prior_employer", "rare_community"):
        if counters[anchor_type] == 0 and not any(
            item["anchor_type"] == anchor_type for item in unknowns
        ):
            unknowns.append({
                "anchor_type": anchor_type,
                "status": "unknown",
                "reason": f"no {anchor_type} fact observed in supplied resume text",
            })

    anchors = _dedupe_anchors(anchors)

    present = [anchor["type"] for anchor in anchors]
    for anchor_type in RESTRICTED_ANCHOR_TYPES:
        if anchor_type not in present and not any(
            item["anchor_type"] == anchor_type for item in unknowns
        ):
            unknowns.append({
                "anchor_type": anchor_type,
                "status": "unknown",
                "reason": f"no {anchor_type} fact observed in supplied inputs",
            })

    return {
        "seeker": {
            "name": parsed["name"],
            "location_observed": location,
            "source_resume": resume_source,
        },
        "target": {
            "company": target["company"],
            "title": target["title"],
            "department": target["department"],
            "location": target["location"],
            "job_source": job_source,
        },
        "anchors": anchors,
        "unknowns": unknowns,
    }


def _dedupe_anchors(anchors):
    """One anchor per (type, value); later observations become extra evidence lines."""
    seen = {}
    ordered = []
    for anchor in anchors:
        key = (anchor["type"], anchor["normalized"])
        prior = seen.get(key)
        if prior is not None:
            extra = prior["attributes"].setdefault("also_observed_at", [])
            extra.append({
                "line": anchor["evidence"]["line"],
                "line_offset": anchor["evidence"].get("line_offset", 0),
                "section": anchor.get("provenance", {}).get("section", "other"),
                "dialect": anchor.get("provenance", {}).get("dialect", "plain"),
                "quote": anchor["evidence"]["quote"],
            })
            prior["attributes"].setdefault("provenance_observations", []).append(
                dict(anchor.get("provenance") or {})
            )
            continue
        seen[key] = anchor
        ordered.append(anchor)
    return ordered


def anchors_by_type(anchor_list, anchor_types):
    wanted = set(anchor_types)
    return [anchor for anchor in anchor_list if anchor["type"] in wanted and anchor["used_in_ranking"]]


def stamp_anchors(anchor_list):
    """The seeker's public stamps: the anchor types that make a shared_stamp proxy."""
    stamps = {"rare_community", "school", "program", "prior_employer"}
    return [anchor for anchor in anchor_list if anchor["type"] in stamps and anchor["used_in_ranking"]]


def evidence_is_traceable(anchor, source_text):
    """True when the anchor's quoted evidence is literally present in the source."""
    quote = anchor.get("evidence", {}).get("quote", "")
    if not quote:
        return False
    return squeeze(quote) in squeeze(source_text)


__all__ = [
    "AnchorError",
    "WEIGHTS",
    "GENERIC_SCHOOL_WEIGHT",
    "HIRING_ADJACENT_TERMS",
    "RESTRICTED_ANCHOR_TYPES",
    "GENERIC_SKILLS",
    "extract_anchors",
    "parse_resume",
    "load_job_card",
    "read_text_file",
    "anchors_by_type",
    "stamp_anchors",
    "evidence_is_traceable",
]
