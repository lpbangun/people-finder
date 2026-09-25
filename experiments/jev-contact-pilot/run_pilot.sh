#!/usr/bin/env bash
# Run the two private job inputs from the isolated experiment worktree.
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo 'Usage: bash run_pilot.sh "/path/to/private-run-dir" [--check]' >&2
  exit 2
fi

run_dir="$1"
mode="${2:-run}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for job in vapi notion; do
  if [[ ! -f "$run_dir/inputs/$job.json" ]]; then
    echo "Missing input: $run_dir/inputs/$job.json" >&2
    exit 2
  fi
done

if [[ "$mode" == "--check" ]]; then
  echo "Ready: $script_dir/jev_rank.py"
  echo "Inputs: $run_dir/inputs/{vapi,notion}.json"
  exit 0
fi
if [[ "$mode" != "run" ]]; then
  echo "Unknown option: $mode" >&2
  exit 2
fi

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  read -rsp 'OpenRouter API key (hidden; not saved): ' OPENROUTER_API_KEY
  echo
  if [[ -z "$OPENROUTER_API_KEY" ]]; then
    echo 'An OpenRouter API key is required.' >&2
    exit 2
  fi
  export OPENROUTER_API_KEY
fi
trap 'unset OPENROUTER_API_KEY' EXIT

for job in vapi notion; do
  python3 "$script_dir/jev_rank.py" "$run_dir/inputs/$job.json" --out "$run_dir/$job-jev-results.json"
  echo "Wrote $run_dir/$job-jev-results.json"
done
