#!/usr/bin/env python3
"""Run the standalone offline gate and optionally the sibling JobSSS check.

Usage: python tests/tools/run_gate.py [label] [--with-jobsss]

The default gate is D, R, E, I and K, and does not need a JobSSS checkout.
Pass --with-jobsss to add Criterion J. JOBSSS_BIN may select a nonstandard
sibling executable for that optional integration check.
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STANDALONE_COMMANDS = (
    ("D", "tests/benchmark/check_discovery.py"),
    ("R", "tests/benchmark/check_ranking.py"),
    ("E", "tests/benchmark/check_exa_import.py"),
    ("I", "tests/benchmark/check_interfaces.py"),
    ("K", "tests/benchmark/check_keepouts.py"),
)
OPTIONAL_COMMANDS = (("J", "tests/benchmark/check_jobsss_composition.py"),)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", nargs="?", default="iter1",
                        help="label used for the evidence directory (default: iter1)")
    parser.add_argument("--with-jobsss", action="store_true",
                        help="also run the optional JobSSS sibling composition check")
    args = parser.parse_args(argv)

    out_dir = os.path.join(ROOT, ".tmp", f"benchmark-{args.label}")
    os.makedirs(out_dir, exist_ok=True)
    summary = {
        "label": args.label,
        "gate": "standalone+jobsss" if args.with_jobsss else "standalone",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commands": [],
    }
    commands = STANDALONE_COMMANDS + (OPTIONAL_COMMANDS if args.with_jobsss else ())
    for criterion, script in commands:
        command = [sys.executable, os.path.join(ROOT, script)]
        started = time.time()
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                   timeout=900)
        duration = round(time.time() - started, 2)
        stdout_path = os.path.join(out_dir, f"{criterion}-stdout.json")
        stderr_path = os.path.join(out_dir, f"{criterion}-stderr.txt")
        with open(stdout_path, "w", encoding="utf-8") as handle:
            handle.write(completed.stdout)
        with open(stderr_path, "w", encoding="utf-8") as handle:
            handle.write(completed.stderr)
        document = None
        try:
            document = json.loads(completed.stdout)
        except ValueError:
            pass
        row = {
            "criterion": criterion,
            "command": command,
            "exit_code": completed.returncode,
            "duration_s": duration,
            "stdout_path": os.path.relpath(stdout_path, ROOT),
            "stderr_path": os.path.relpath(stderr_path, ROOT),
            "json_parsed": document is not None,
            "status": "completed",
        }
        if document:
            row.update({
                "criterion_reported": document.get("criterion"),
                "offline": document.get("offline"),
                "passed": document.get("passed"),
                "assertions": [(item["id"], item["passed"])
                               for item in document.get("assertions", [])],
            })
        summary["commands"].append(row)
        print(f"{criterion}: exit={completed.returncode} passed={row.get('passed')} "
              f"assertions={row.get('assertions')} ({duration}s)")

    if not args.with_jobsss:
        summary["commands"].append({
            "criterion": "J",
            "status": "skipped",
            "reason": ("optional sibling integration; pass --with-jobsss to run it, and set "
                       "JOBSSS_BIN if the executable is not at ../jobsss/bin/jobsss"),
        })
        print("J: skipped (optional; use --with-jobsss to run sibling integration)")

    active = [row for row in summary["commands"] if row["status"] != "skipped"]
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["all_exit_zero"] = all(row["exit_code"] == 0 for row in active)
    summary["all_passed"] = all(row.get("passed") is True for row in active)
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print(f"summary: {summary_path}")
    print(f"all_exit_zero={summary['all_exit_zero']} all_passed={summary['all_passed']}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
