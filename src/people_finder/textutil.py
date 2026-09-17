"""Text normalization helpers shared by anchor extraction, ranking and validation.

Everything here is deterministic and offline: no clock, no locale, no network.
"""

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_PUNCT = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")

# Query parameters that only carry transport/session bookkeeping. They are
# removed before a public profile URL is used as an identity key, so the same
# profile observed under two transport variants collapses to one lead.
_TRACKING_KEYS = {
    "trk",
    "trkpublicprofile",
    "trackingid",
    "lipi",
    "licu",
    "original_referer",
    "originalreferer",
    "ref",
    "refid",
    "src",
    "ve",
    "utm_source",
    "utm_campaign",
    "utm_medium",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "sessionid",
    "midtoken",
    "midtokensig",
    "eid",
}


def squeeze(text):
    """Collapse whitespace to single spaces and trim."""
    return _WS.sub(" ", str(text or "")).strip()


def norm_phrase(text):
    """Lowercase, strip punctuation, collapse whitespace -> matchable phrase."""
    return squeeze(_PUNCT.sub(" ", str(text or "").lower()))


def tokens(text):
    return [tok for tok in norm_phrase(text).split(" ") if tok]


def phrase_present(haystack, needle):
    """Whole-phrase containment on normalized text (no partial word hits)."""
    needle_norm = norm_phrase(needle)
    if not needle_norm:
        return False
    return f" {needle_norm} " in f" {norm_phrase(haystack)} "


def all_terms_present(haystack, terms):
    normalized = norm_phrase(haystack)
    if not terms:
        return False
    return all(f" {norm_phrase(term)} " in f" {normalized} " for term in terms)


def is_public_profile_url(url):
    """True only for a public LinkedIn /in/ profile URL.

    A /in/ URL is a lead, not a contact. This function is the single filter that
    keeps company, jobs and feed URLs out of the candidate set.
    """
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if host == "lnkd.in":
        return True
    if host not in ("linkedin.com", "www.linkedin.com", "m.linkedin.com") \
            and not re.fullmatch(r"[a-z]{2}\.linkedin\.com", host):
        return False
    path = parts.path or ""
    return path.lower().startswith("/in/")


def normalize_public_url(url):
    """Canonical public-profile URL key: lowercase host, stripped tracking params.

    Returns the display form (no trailing slash, no query noise) or "" when the
    URL is not a public profile URL.
    """
    if not is_public_profile_url(url):
        return ""
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower()
    if host == "linkedin.com" or host == "m.linkedin.com" \
            or re.fullmatch(r"[a-z]{2}\.linkedin\.com", host):
        host = "www.linkedin.com"
    path = parts.path.rstrip("/")
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_KEYS
    ]
    query = urlencode(sorted(kept))
    return urlunsplit(("https", host, path, query, ""))


def profile_slug_tokens(url):
    """Tokens from the trailing slug of a public profile URL (observation text)."""
    key = normalize_public_url(url)
    if not key:
        return []
    path = urlsplit(key).path
    slug = path.rstrip("/").split("/")[-1]
    return tokens(slug)


def split_title(title):
    """Split a supplied result title into (name, remainder) on common separators."""
    text = squeeze(title)
    for separator in (" – ", " — ", " - ", " | ", " · ", "•"):
        if separator in text:
            head, _, tail = text.partition(separator)
            head = head.strip()
            if head:
                return head, tail.strip()
    return text, ""


def find_span(text, phrase):
    """Return the exact substring of `text` whose normalized form is `phrase`.

    Used to attach observed evidence that is literally present in the supplied
    input, never a paraphrase.
    """
    target = norm_phrase(phrase)
    if not target:
        return ""
    words = target.split(" ")
    tokens_with_spans = list(re.finditer(r"[A-Za-z0-9][A-Za-z0-9'&+.#\-]*", str(text or "")))
    normalized = [norm_phrase(match.group(0)) for match in tokens_with_spans]
    for start in range(len(normalized)):
        collected = []
        cursor = start
        while cursor < len(normalized) and len(collected) < len(words):
            token = normalized[cursor]
            if token:
                collected.extend(token.split(" "))
            if collected == words[: len(collected)] and len(collected) == len(words):
                return str(text)[tokens_with_spans[start].start():tokens_with_spans[cursor].end()]
            if collected != words[: len(collected)]:
                break
            cursor += 1
    return ""


def round_score(value):
    return round(float(value), 6)
