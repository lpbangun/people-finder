#!/usr/bin/env python3
"""Run the six frozen benchmark commands and record their evidence.

Usage: python3 tests/tools/run_gate.py [iteration-label]

Writes, for each command:
  .tmp/benchmark-<label>/<criterion>-stdout.json   raw stdout (one JSON object)
  .tmp/benchmark-<label>/<criterion>-stderr.txt    diagnostics
  .tmp/benchmark-<label>/summary.json              exit codes and assertion results
"""

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMMANDS = (
    ("D", "python3 tests/benchmark/check_discovery.py"),
    ("R", "python3 tests/benchmark/check_ranking.py"),
    ("E", "python3 tests/benchmark/check_exa_import.py"),
    ("I", "python3 tests/benchmark/check_interfaces.py"),
    ("J", "python3 tests/benchmark/check_jobsss_composition.py"),
    ("K", "python3 tests/benchmark/check_keepouts.py"),
)


def main(argv):
    label = argv[0] if argv else "iter1"
    out_dir = os.path.join(ROOT, ".tmp", f"benchmark-{label}")
    os.makedirs(out_dir, exist_ok=True)
    summary = {"label": label, "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "commands": []}
    for criterion, command in COMMANDS:
        started = time.time()
        completed = subprocess.run(command, shell=True, cwd=ROOT, capture_output=True, text=True,
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
            document = None
        row = {
            "criterion": criterion,
            "command": command,
            "exit_code": completed.returncode,
            "duration_s": duration,
            "stdout_path": os.path.relpath(stdout_path, ROOT),
            "stderr_path": os.path.relpath(stderr_path, ROOT),
            "json_parsed": document is not None,
        }
        if document:
            row.update({
                "criterion_reported": document.get("criterion"),
                "offline": document.get("offline"),
                "passed": document.get("passed"),
                "assertions": [(item["id"], item["passed"]) for item in document.get("assertions", [])],
            })
        summary["commands"].append(row)
        print(f"{criterion}: exit={completed.returncode} passed={row.get('passed')} "
              f"assertions={row.get('assertions')} ({duration}s)")
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["all_exit_zero"] = all(row["exit_code"] == 0 for row in summary["commands"])
    summary["all_passed"] = all(row.get("passed") for row in summary["commands"])
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    print(f"summary: {summary_path}")
    print(f"all_exit_zero={summary['all_exit_zero']} all_passed={summary['all_passed']}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
