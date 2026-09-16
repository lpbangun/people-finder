"""Command dispatch.

Public operations: compile, rank, import-exa, mcp, validate. Every command is
offline, credential-free and file-based. No command performs an external action.
"""

import argparse
import json
import os
import sys
import tempfile
import traceback

from . import PRODUCT, PRODUCT_VERSION, SCHEMA_CANDIDATES, SCHEMA_QUERIES
from .anchors import AnchorError
from .exa_import import import_exa
from .mcp_server import main as mcp_main
from .packs import compile_from_paths
from .rank import rank_candidates
from .results import ResultError, load_document, load_exa_envelope, load_supplied_results
from .schema import validate_document, validate_file

EXIT_OK = 0
EXIT_ASSERTION_FAILED = 1
EXIT_MALFORMED_INPUT = 2

DUMP_KWARGS = {"indent": 2, "ensure_ascii": False, "sort_keys": False}


def _dump(document):
    return json.dumps(document, **DUMP_KWARGS)


def _write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle_fd, temporary = tempfile.mkstemp(dir=directory, prefix=".people-finder-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _emit(document, out_path, quiet=False):
    text = _dump(document)
    if out_path:
        _write_atomic(out_path, text + "\n")
    if not quiet:
        sys.stdout.write(text + "\n")
    return EXIT_OK


def _report_invalid(errors, out_path):
    payload = {"ok": False, "errors": errors}
    if not out_path:
        pass
    sys.stderr.write(_dump(payload) + "\n")
    return EXIT_MALFORMED_INPUT


def cmd_compile(args):
    document = compile_from_paths(args.resume, args.job, generated_at=args.at)
    errors = validate_document(document)
    if errors:
        return _report_invalid(errors, args.out)
    return _emit(document, args.out, args.quiet)


def cmd_rank(args):
    queries = load_document(args.queries, "compiled queries", SCHEMA_QUERIES)
    results = load_supplied_results(args.results)
    document = rank_candidates(
        queries, results, queries_source=args.queries, results_source=args.results,
        generated_at=args.at,
    )
    errors = validate_document(document)
    if errors:
        return _report_invalid(errors, args.out)
    return _emit(document, args.out, args.quiet)


def cmd_import_exa(args):
    candidates = load_document(args.candidates, "candidate document", SCHEMA_CANDIDATES)
    envelope = load_exa_envelope(args.result)
    document, outcome = import_exa(candidates, envelope, candidates_source=args.candidates)
    errors = validate_document(document)
    if errors:
        return _report_invalid(errors, args.out)
    if args.out:
        sys.stderr.write(_dump({"import": outcome}) + "\n")
    return _emit(document, args.out, args.quiet)


def cmd_validate(args):
    errors = validate_file(args.file)
    document = None
    if not errors:
        with open(args.file, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    result = {
        "ok": not errors,
        "file": args.file,
        "schema": (document or {}).get("schema"),
        "errors": errors,
    }
    if args.expect_schema and result["schema"] != args.expect_schema:
        result["ok"] = False
        result["errors"] = errors + [f"expected schema '{args.expect_schema}'"]
    sys.stdout.write(_dump(result) + "\n")
    if result["ok"]:
        return EXIT_OK
    return EXIT_ASSERTION_FAILED if errors else EXIT_MALFORMED_INPUT


def build_parser():
    parser = argparse.ArgumentParser(
        prog=PRODUCT,
        description=(
            "Sparse typed-anchor candidate discovery from a seeker profile and one "
            "target job. Offline, credential-free, discovery only."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT} {PRODUCT_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser(
        "compile", help="compile typed-anchor query packs from a resume file and a job file"
    )
    compile_parser.add_argument("--resume", required=True, help="resume markdown/text file")
    compile_parser.add_argument("--job", required=True, help="job card JSON file")
    compile_parser.add_argument("--out", help="write the compiled document to this path")
    compile_parser.add_argument("--at", help="fixed retrieval timestamp for reproducible output")
    compile_parser.add_argument("--quiet", action="store_true", help="do not write the document to stdout")
    compile_parser.set_defaults(handler=cmd_compile)

    rank_parser = subparsers.add_parser(
        "rank", help="rank supplied results against compiled packs"
    )
    rank_parser.add_argument("--queries", required=True, help="compiled people-queries.v1 file")
    rank_parser.add_argument("--results", required=True, help="supplied recorded results file")
    rank_parser.add_argument("--out", help="write the candidate document to this path")
    rank_parser.add_argument("--at", help="fixed retrieval timestamp for reproducible output")
    rank_parser.add_argument("--quiet", action="store_true", help="do not write the document to stdout")
    rank_parser.set_defaults(handler=cmd_rank)

    import_parser = subparsers.add_parser(
        "import-exa", help="merge one recorded provider envelope into a candidate document"
    )
    import_parser.add_argument("--candidates", required=True, help="existing people-candidates.v1 file")
    import_parser.add_argument("--result", required=True, help="recorded normalized provider envelope")
    import_parser.add_argument("--out", help="write the merged document to this path")
    import_parser.add_argument("--quiet", action="store_true", help="do not write the document to stdout")
    import_parser.set_defaults(handler=cmd_import_exa)

    validate_parser = subparsers.add_parser("validate", help="validate an emitted document")
    validate_parser.add_argument("file", help="document to validate")
    validate_parser.add_argument("--expect-schema", help="require this schema value")
    validate_parser.set_defaults(handler=cmd_validate)

    mcp_parser = subparsers.add_parser("mcp", help="serve the stated tools over stdio JSON-RPC")
    mcp_parser.set_defaults(handler=lambda args: mcp_main([]))

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ResultError, AnchorError) as cause:
        sys.stderr.write(_dump({"ok": False, "error": {"kind": "malformed_input", "message": str(cause)}}) + "\n")
        return EXIT_MALFORMED_INPUT
    except FileNotFoundError as cause:
        sys.stderr.write(_dump({"ok": False, "error": {"kind": "missing_input", "message": str(cause)}}) + "\n")
        return EXIT_MALFORMED_INPUT
    except (OSError, ValueError) as cause:
        sys.stderr.write(_dump({"ok": False, "error": {"kind": "unusable_input", "message": str(cause)}}) + "\n")
        return EXIT_MALFORMED_INPUT
    except Exception as cause:  # pragma: no cover - defensive only
        sys.stderr.write(f"unexpected failure: {cause}\n")
        traceback.print_exc(file=sys.stderr)
        return EXIT_ASSERTION_FAILED


if __name__ == "__main__":
    sys.exit(main())
