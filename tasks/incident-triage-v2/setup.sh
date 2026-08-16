#!/usr/bin/env bash
# Build the fixture for `incident-triage-v2`, the replacement for v1.
#
# v1 failed as an instrument in three specific ways, and each is addressed here.
#
# 1. AMBIGUOUS GROUND TRUTH. v1's note_093 described a contractor's laptop
#    returned unwiped with a still-valid SSH key. One run spent 38k reasoning
#    tokens arguing that nothing had reached anyone unauthorized -- the laptop
#    went back to the organization -- and answered without it. That reading is
#    defensible, so the grader was scoring interpretive stance, which varies
#    independently of every experimental axis and is therefore pure noise.
#
#    Every target here names a party or place that is plainly outside the set
#    allowed to hold the credential: a public repository, a public dashboard,
#    the wrong customer, a company-wide bucket. No target requires deciding
#    whether an exposure "counts".
#
# 2. SATURATION. 23 of 24 v1 runs were perfect, so the task separated nothing.
#    This file is ~4x larger with 6 targets instead of 3, which is a volume and
#    recall problem rather than a reasoning one -- the failure mode it induces
#    (missing a target that sits deep in the file) is exactly the failure mode
#    the context axes are supposed to cause.
#
# 3. NO FALSE-POSITIVE PRESSURE. v1 produced zero false positives across 24
#    runs, so precision was never tested. The distractors here are
#    credential-ADJACENT and explicitly state that no exposure occurred
#    (proactive rotation, a dummy value, a denied request, an audited
#    migration). Reporting one is an unambiguous error, not a judgement call.
#
# The task stays semantic on purpose. A mechanically checkable rule would be
# answerable by a five-line script whose output is a few hundred characters,
# which sidesteps the context axes entirely -- the defect that makes
# `config-audit` a weak probe.
set -euo pipefail

mkdir -p notes

python3 - <<'PY'
FILLER = [
    "Deploy to the staging cluster stalled for eleven minutes while the image registry finished a garbage collection pass. No customer traffic was affected and the rollout completed on retry.",
    "A batch job double-counted rows in the nightly rollup because the upstream export ran twice. The rollup was recomputed the following morning and downstream dashboards were corrected.",
    "Latency on the search endpoint rose to 900ms during a reindex. The reindex was rescheduled to off-peak hours and latency returned to baseline within the hour.",
    "An engineer reported that the admin console rendered a blank page on Safari 17. The cause was a polyfill dropped in a dependency bump; the polyfill was restored.",
    "Disk pressure on a logging node triggered an alert. Retention was reduced from 30 to 14 days for debug-level entries and the alert cleared.",
    "The on-call rotation paged twice for the same flapping health check. The check's threshold was widened after review and the flapping stopped.",
    "Background workers backed up behind a slow database migration. The migration was split into batches and the queue drained without data loss.",
    "A canary release showed a 2% error-rate increase on the checkout path. The canary was rolled back automatically and the offending commit was reverted.",
    "Two regions briefly disagreed about feature-flag state after a config push. The flag service reconciled within ninety seconds and no user-visible inconsistency was reported.",
    "A dependency bump broke the integration test suite because a mocked response shape changed. The mocks were updated and the suite went green.",
    "Memory usage on the recommendation service grew steadily over four days. A cache without an eviction bound was identified and given a size limit.",
    "The nightly backup job reported success but wrote a zero-byte artifact. The job now verifies artifact size before reporting success.",
]

# Credential-ADJACENT distractors. Each states plainly that nothing was
# exposed, so reporting one is an error rather than a defensible reading.
NEAR_MISSES = {
    23: "As part of quarterly hygiene the team rotated the payments API key ahead of schedule. The previous key was revoked the same hour and never left the secrets vault.",
    57: "A scanner flagged what looked like a hardcoded token in a test fixture. Review confirmed it is a dummy string that grants no access to any environment, and it was renamed to make that obvious.",
    94: "An engineer requested standing production database credentials for debugging. The request was denied and they were given a time-boxed read replica session instead.",
    131: "Secrets were migrated from the legacy vault to the new one. Access logs for the migration window were audited afterwards and confirmed no read by any principal outside the platform team.",
    168: "A phishing email impersonating the finance team reached twelve inboxes and asked recipients to re-enter their SSO password. Nobody interacted with it and the message was purged from all mailboxes.",
    205: "The security review of the payments module found no exploitable issues. Two input validators were tightened on the reviewer's recommendation.",
    242: "A vulnerability scanner flagged an outdated TLS library in a container base image. The image was rebuilt against the patched version and redeployed the same day.",
    279: "A developer noticed the staging database password had not been rotated in fourteen months. It was rotated; the old value had only ever been present in the vault and the staging environment.",
    316: "An audit found three service accounts with broader permissions than needed. Their roles were narrowed. No credential material was disclosed at any point.",
    353: "A contractor's access was reviewed at the end of their engagement. All their accounts and keys were disabled the same day and the review was signed off by their sponsor.",
    390: "A support engineer asked whether customer API keys could be displayed in the internal admin tool. The answer was no, and the tool continues to show only the last four characters.",
    427: "CI briefly printed environment variable NAMES while debugging a job configuration. Values were masked throughout, as the masking rules intend.",
}

# TARGETS. Each names a party or place plainly outside the authorized set.
#
# None of these contain the words key, token, password, passphrase, secret, or
# credential -- and every NEAR_MISS above does. That is deliberate. A first
# attempt at this task put the loud vocabulary in the targets, and a Standard
# run answered it perfectly in 52 seconds by grepping: the answer was reachable
# without reading the file, so the task measured search, not recall.
#
# Inverting the vocabulary turns the shortcut into a trap. `grep -E
# 'key|token|password|secret'` now returns near-misses and nothing else, so the
# only path to the answer is reading all 480 notes and recognising the secret
# from its description ("the .env file", "the value CI authenticates with",
# "the private half of the certificate"). That is precisely the capability the
# context axes are supposed to affect.
TARGETS = {
    38: "While debugging the export pipeline, a developer pasted the entire contents of their local .env file into the company-wide #engineering channel so a colleague could reproduce the failure. It stayed up for roughly two hours before someone removed it.",
    112: "A commit tidying the sample configuration replaced the placeholder with the real value the production service authenticates to the database with. It was pushed to the project's PUBLIC GitHub repository and sat in the default branch for three days.",
    186: "The mobile build pipeline echoed the value it uses to sign release artifacts into its job log. That log is published on the project's public CI dashboard, which anyone can read without logging in.",
    263: "A support agent attached the wrong file to a ticket reply, sending a customer the service-account file the billing integration signs in with. It landed in that customer's shared support inbox.",
    341: "An onboarding export was copied into an S3 bucket every employee in the company can list and read. The export contained a spreadsheet of the values our partners use to authenticate to our API.",
    418: "A screen recording made for the onboarding guide was uploaded to the public help centre. For about forty seconds the terminal scrollback shows the private half of the production certificate.",
}

lines = ["# Incident notes, FY26", ""]
for i in range(1, 481):
    body = TARGETS.get(i) or NEAR_MISSES.get(i) or FILLER[i % len(FILLER)]
    lines.append(f"## note_{i:03d}")
    lines.append(body)
    lines.append("")

with open("notes/incidents.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
PY

wc -c notes/incidents.md >/dev/null
