"""people-finder — sparse typed-anchor candidate discovery.

Standalone sibling tool: turns a seeker profile plus one target job into a small
ranked set of public candidate leads. It never establishes identity, never
performs an external action, and never writes another tool's state.

Module map:
  textutil   normalization, tokenization, public-URL filtering
  anchors    typed anchor extraction from resume text and job cards
  packs      query-pack compilation over extracted anchors
  results    supplied-result (recorded SERP) and provider-envelope loading
  rank       sparse typed-anchor scoring -> people-candidates.v1
  exa_import recorded provider-envelope merge -> recall evidence only
  schema     structural validation of the emitted documents
  mcp_server stdio JSON-RPC surface
  cli        command dispatch
"""

PRODUCT = "people-finder"
PRODUCT_VERSION = "0.1.0"
SCHEMA_CANDIDATES = "people-candidates.v1"
SCHEMA_QUERIES = "people-queries.v1"
SCHEMA_SERP = "recorded-serp.v1"

__all__ = [
    "PRODUCT",
    "PRODUCT_VERSION",
    "SCHEMA_CANDIDATES",
    "SCHEMA_QUERIES",
    "SCHEMA_SERP",
]
