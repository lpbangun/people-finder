"""Typed anchor extraction from resume text and a target job card.

The "vector" here is a sparse bag of typed anchors — never an embedding. Every
anchor carries the exact source fragment it was read from, so a reviewer can
trace it back into the supplied fixture text.
"""

import json
import os
import re

from .textutil import norm_phrase, squeeze, tokens

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
    "education": ("education", "academics", "academic", "studies", "schooling"),
    "experience": ("experience", "work experience", "employment", "career", "roles"),
    "community": ("community", "open source", "open-source", "oss", "public work",
                  "speaking", "talks", "conferences", "volunteering"),
    "skills": ("skills", "tools", "technologies", "tech stack", "stack"),
    "projects": ("projects", "papers", "publications", "research", "thesis work"),
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

_CONNECTORS = r"(?:of|the|and|for|de|du|di|von|at)"
_WORD = r"[A-Z][A-Za-z'&.\-]*"
# Longest capitalized phrase that ends in an organization suffix: "Northwind
# Institute of Technology" resolves as a whole, never as "Northwind Institute".
SCHOOL_RE = re.compile(
    r"(" + _WORD + r"(?:\s+" + _CONNECTORS + r"\s+" + _WORD + r"|\s+" + _WORD + r")*\s+"
    + r"(?:" + "|".join(ORGANIZATION_SUFFIXES) + r")"
    + r"(?:\s+" + _CONNECTORS + r"\s+" + _WORD + r"|\s+" + _WORD + r")*)"
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
)

SENIORITY_WORDS = {
    "senior", "staff", "principal", "lead", "junior", "associate", "mid", "entry",
    "intern", "internship", "chief", "vp", "vice", "president", "head", "director",
    "ii", "iii", "iv", "sr", "jr",
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
    }
    return anchor


def _evidence(source, field, line_number, quote):
    return {
        "source": source,
        "field": field,
        "line": line_number,
        "quote": squeeze(quote),
    }


def _section_of(heading):
    normalized = norm_phrase(heading)
    for section, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return section
    for section, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                return section
    return "other"


def parse_resume(text):
    """Parse resume text into {name, labeled, sections:[{name, lines:[(n, text)]}]}."""
    name = None
    labeled = {}
    sections = []
    current = {"name": "profile", "lines": []}
    for number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if name is None and heading:
                name = heading
            current = {"name": _section_of(heading), "heading": heading, "lines": []}
            sections.append(current)
            continue
        bullet = stripped.lstrip("-*•").strip()
        label_match = re.match(r"^([A-Za-z][A-Za-z /]{2,24}):\s*(.+)$", bullet)
        if label_match and current["name"] in ("profile", "other"):
            labeled[label_match.group(1).strip().lower()] = label_match.group(2).strip()
        if name is None and current["name"] == "profile" and not label_match:
            name = squeeze(bullet) or None
        current["lines"].append((number, bullet))
    return {"name": name or "", "labeled": labeled, "sections": sections}


def _extract_school_anchors(section, source, anchors, counters):
    for number, line in section["lines"]:
        match = SCHOOL_RE.search(line)
        school = squeeze(match.group(1)) if match else ""
        if not school:
            continue
        degree = ""
        program = ""
        degree_match = DEGREE_RE.search(line)
        if degree_match:
            degree = squeeze(degree_match.group(1))
            program = squeeze(degree_match.group(2)).strip(" ,")
        generic = norm_phrase(school) in GENERIC_SCHOOL_TOKENS
        counters["school"] += 1
        anchors.append(_make_anchor(
            "school", school, _evidence(source, "resume:education", number, line),
            weight=GENERIC_SCHOOL_WEIGHT if generic else None,
            generic=generic, index=counters["school"],
            attributes={"degree": degree, "program": program},
        ))
        if program:
            counters["program"] += 1
            anchors.append(_make_anchor(
                "program", program, _evidence(source, "resume:education", number, line),
                index=counters["program"],
                attributes={"degree": degree, "school": school},
            ))


def _extract_employer_anchors(section, source, anchors, counters, unknowns):
    for number, line in section["lines"]:
        segments = [squeeze(part) for part in line.split(",") if squeeze(part)]
        if len(segments) < 2:
            continue
        candidates = [seg for seg in segments if not DATE_RE.search(seg)]
        if len(candidates) < 2:
            continue
        employer = candidates[1]
        if not re.search(r"[A-Za-z]", employer):
            continue
        if norm_phrase(employer) in {norm_phrase(marker) for marker in NON_EMPLOYER_MARKERS}:
            unknowns.append({
                "anchor_type": "prior_employer",
                "status": "unknown",
                "reason": "only self-employment or non-employer text recorded in supplied resume",
                "evidence": _evidence(source, "resume:experience", number, line),
            })
            continue
        period = next((seg for seg in segments if DATE_RE.search(seg)), "")
        counters["prior_employer"] += 1
        anchors.append(_make_anchor(
            "prior_employer", employer,
            _evidence(source, "resume:experience", number, line),
            index=counters["prior_employer"],
            attributes={"role_observed": candidates[0], "period": period},
        ))


def _extract_community_anchors(section, source, anchors, counters):
    for number, line in section["lines"]:
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
            "rare_community", value, _evidence(source, "resume:community", number, line),
            index=counters["rare_community"],
            attributes={"role_observed": parts[0] if len(parts) > 1 else "", "detail": detail},
        ))


def _extract_skill_anchors(section, source, anchors, counters):
    for number, line in section["lines"]:
        for item in re.split(r"[,;|/]", line):
            value = squeeze(item)
            if not value or not re.search(r"[A-Za-z]", value):
                continue
            counters["skill"] += 1
            anchors.append(_make_anchor(
                "skill", value, _evidence(source, "resume:skills", number, line),
                generic=norm_phrase(value) in GENERIC_SKILLS,
                used_in_ranking=False, default_off=True, index=counters["skill"],
                attributes={"generic_skill": norm_phrase(value) in GENERIC_SKILLS},
            ))


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
            "function", phrase, _evidence(source, "job:title", 0, title),
            index=counters["function"],
            attributes={
                "role_family": "target role family from supplied job title",
                "match_scope": "title_or_snippet",
                "terms": [tok for tok in tokens(phrase) if tok not in FUNCTION_STOPWORDS],
                "seniority_stripped": [w for w in re.findall(r"[A-Za-z]+", title)
                                       if norm_phrase(w) in SENIORITY_WORDS],
            },
        ))
    if department and norm_phrase(department) != norm_phrase(phrase):
        counters["function"] += 1
        anchors.append(_make_anchor(
            "function", department, _evidence(source, "job:department", 0, department),
            weight=0.9, index=counters["function"],
            attributes={
                "role_family": "department keyword from supplied job card",
                "match_scope": "title",
                "why": ("a short department phrase matched inside a snippet is not role "
                        "evidence, so it only counts when a supplied result title carries it"),
            },
        ))
    counters["target_employer"] += 1
    anchors.append(_make_anchor(
        "target_employer", company, _evidence(source, "job:company", 0, company),
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
    sections = {section["name"]: section for section in parsed["sections"]}

    if "education" in sections:
        _extract_school_anchors(sections["education"], resume_source, anchors, counters)
    else:
        unknowns.append({
            "anchor_type": "school",
            "status": "unknown",
            "reason": "no education section present in supplied resume text",
        })
    if "experience" in sections:
        _extract_employer_anchors(sections["experience"], resume_source, anchors, counters, unknowns)
    else:
        unknowns.append({
            "anchor_type": "prior_employer",
            "status": "unknown",
            "reason": "no experience section present in supplied resume text",
        })
    if "community" in sections:
        _extract_community_anchors(sections["community"], resume_source, anchors, counters)
    else:
        unknowns.append({
            "anchor_type": "rare_community",
            "status": "unknown",
            "reason": "no community or open-source section present in supplied resume text",
        })
    if "skills" in sections:
        _extract_skill_anchors(sections["skills"], resume_source, anchors, counters)
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
        anchors.append(_make_anchor(
            "location", location, _evidence(resume_source, "resume:header", 0, location),
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
            extra.append({"line": anchor["evidence"]["line"], "quote": anchor["evidence"]["quote"]})
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
