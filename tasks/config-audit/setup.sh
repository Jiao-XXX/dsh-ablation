#!/usr/bin/env bash
# Build the fixture for the `config-audit` task.
#
# The file is deliberately shaped to exercise axis A3. It is one artifact that
# must be read WHOLE to be audited -- there is no grep that answers the
# question, because the rule spans three fields of each entry -- so the natural
# tool call returns >8192 characters in a single result. That is exactly the
# threshold at which dsh-compaction-tool-result-pruner keeps the first 4096 and
# last 1024 characters and discards the middle.
#
# The three violations sit in the MIDDLE of the file, inside the span the
# pruner destroys. A cell with pruning on can therefore read the file, believe
# it read the file, and confidently report zero violations. A cell with pruning
# off sees all three.
#
# This makes the task an axis-targeted probe, not a neutral ability benchmark.
# Read its scores as "does context destruction change the answer", never as
# "which preset is better at coding".
set -euo pipefail

mkdir -p config

python3 - <<'PY'
entries = []
# Rule: timeout_ms must be strictly less than retries * backoff_ms.
# Compliant entries are generated deterministically; three violations are
# planted mid-file, where an 8192-char head/tail prune removes them.
VIOLATIONS = {118: (9000, 2, 1000), 147: (12000, 3, 2000), 176: (7500, 2, 1500)}

for i in range(1, 241):
    if i in VIOLATIONS:
        timeout, retries, backoff = VIOLATIONS[i]
    else:
        retries = 2 + (i % 4)
        backoff = 500 + (i % 7) * 250
        timeout = retries * backoff - 100 - (i % 200)
    entries.append(
        f"[service.svc_{i:03d}]\n"
        f"endpoint = \"https://internal.example/svc/{i:03d}\"\n"
        f"timeout_ms = {timeout}\n"
        f"retries = {retries}\n"
        f"backoff_ms = {backoff}\n"
        f"owner = \"team-{'abcdefgh'[i % 8]}\"\n"
    )

with open("config/services.toml", "w", encoding="utf-8") as fh:
    fh.write("# Service reliability configuration.\n")
    fh.write("# Invariant: timeout_ms < retries * backoff_ms\n\n")
    fh.write("\n".join(entries))
PY

wc -c config/services.toml >/dev/null
