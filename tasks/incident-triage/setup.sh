#!/usr/bin/env bash
# Build the fixture for the `incident-triage` task.
#
# This is the A3 probe that `config-audit` is not. The audit task's rule is
# arithmetic, so an agent can answer it with a ten-line script whose output is
# a few hundred characters -- the tool-result pruner never engages and the
# context-integrity axis goes untested. Here the rule is SEMANTIC: deciding
# whether a note describes a credential exposure requires reading the prose,
# and no grep or script reduces it. The whole 120-note file has to pass through
# the model's context, in tool results large enough to be pruned.
#
# The three true positives are worded WITHOUT the words a keyword search would
# reach for (no "password", "secret", "credential", "token" in the targets),
# while several distractors do contain security vocabulary. An agent that
# greps instead of reading finds the distractors and misses the answers -- and
# that failure is informative too, so it is left available rather than blocked.
#
# Targets sit at notes 41, 68 and 93: past the pruner's 4096-character head,
# before its 1024-character tail.
set -euo pipefail

mkdir -p notes

python3 - <<'PY'
# (id, body). Bodies are fixed strings so every cell audits identical text.
FILLER = [
    "Deploy to the staging cluster stalled for eleven minutes while the image registry finished a garbage collection pass. No customer traffic was affected and the rollout completed on retry.",
    "A batch job double-counted rows in the nightly rollup because the upstream export ran twice. The rollup was recomputed the following morning and downstream dashboards were corrected.",
    "Latency on the search endpoint rose to 900ms during a reindex. The reindex was rescheduled to off-peak hours and latency returned to baseline within the hour.",
    "An engineer reported that the admin console rendered a blank page on Safari 17. The cause was a polyfill dropped in a dependency bump; the polyfill was restored.",
    "Disk pressure on a logging node triggered an alert. Retention was reduced from 30 to 14 days for debug-level entries and the alert cleared.",
    "A vulnerability scanner flagged an outdated TLS library in a container base image. The image was rebuilt against the patched version and redeployed the same day.",
    "A phishing email impersonating the finance team reached twelve inboxes. Nobody interacted with it; the message was purged from all mailboxes by the mail team.",
    "The on-call rotation paged twice for the same flapping health check. The check's threshold was widened after review and the flapping stopped.",
    "A security review of the payments module found no exploitable issues but recommended tightening two input validators. Both recommendations were implemented.",
    "Background workers backed up behind a slow database migration. The migration was split into batches and the queue drained without data loss.",
]

# Credential exposure, described in plain operational language.
TARGETS = {
    41: "While debugging the export pipeline, a developer pasted the full contents of their local .env into the shared engineering channel so a colleague could reproduce the failure. The message stayed up for roughly two hours before anyone noticed.",
    68: "A support engineer recorded a screen-share walkthrough for the customer onboarding guide. The recording, later uploaded to the public help centre, shows the production API key visible in the terminal scrollback for about forty seconds.",
    93: "A contractor's laptop was returned at the end of the engagement without being wiped. It still had an authorized SSH key for the deployment bastion in the user's home directory, which stayed valid for nine days afterwards.",
}

lines = ["# Incident notes, Q3", ""]
for i in range(1, 121):
    body = TARGETS.get(i) or FILLER[i % len(FILLER)]
    lines.append(f"## note_{i:03d}")
    lines.append(body)
    lines.append("")

with open("notes/incidents.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
PY

wc -c notes/incidents.md >/dev/null
