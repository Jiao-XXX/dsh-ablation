#!/usr/bin/env bash
# Build the fixture for `policy-audit` -- the task built to DISCRIMINATE, after
# two retrieval tasks failed to.
#
# Why not more volume. `incident-triage` (20KB) and `incident-triage-v2` (80KB)
# both saturated: Standard answered v2 perfectly in 58 seconds using 23,898
# input tokens. A 256k-window model reading 9% of its context has no reason to
# miss anything, and inverting the keyword vocabulary did not help -- the model
# grepped AND read the whole file, so the trap cost it nothing. Making retrieval
# hard enough to fail needs roughly 180k tokens per run, which is a different
# budget and also the only way to reach the 0.8 compaction threshold.
#
# What this task does instead: keep the corpus small and make the JUDGEMENT
# hard. Reportability is a three-clause conjunction with two exceptions, and
# every clause's facts are stated explicitly in the note. Nothing is ambiguous
# and nothing requires recall beyond the file -- but eight candidates must each
# be checked against five conditions, and only four survive.
#
# That is the shape that can answer the prompt-axis question. If Standard's
# 4.4KB of tool guidance degrades careful multi-clause reasoning, this is where
# it shows; if it does not, a null result here is worth more than another
# saturated retrieval run.
set -euo pipefail

mkdir -p notes policy

cat > policy/disclosure-policy.md <<'POLICY'
# Disclosure policy

An incident is REPORTABLE when ALL THREE of the following hold:

  C1. A secret value reached a person, system, or location outside the set
      allowed to hold it.
  C2. The exposure window was longer than 60 minutes.
  C3. The receiving location was NOT under our own control. A location is
      "under our own control" when we can delete the content ourselves without
      asking anyone else.

Two exceptions make an otherwise-reportable incident NOT reportable:

  E1. The secret had already been rotated and revoked BEFORE the exposure
      began, so the exposed value granted no access at any point.
  E2. The receiving party is a named partner already covered by our mutual
      non-disclosure agreement.

An incident that fails any of C1-C3, or that meets E1 or E2, is not reportable.
POLICY

python3 - <<'PY'
FILLER = [
    "Deploy to the staging cluster stalled for eleven minutes while the image registry finished a garbage collection pass. No customer traffic was affected.",
    "A batch job double-counted rows in the nightly rollup because the upstream export ran twice. The rollup was recomputed the following morning.",
    "Latency on the search endpoint rose to 900ms during a reindex. The reindex was rescheduled to off-peak hours.",
    "An engineer reported that the admin console rendered a blank page on Safari 17. A dropped polyfill was restored.",
    "Disk pressure on a logging node triggered an alert. Debug-level retention was reduced from 30 to 14 days and the alert cleared.",
    "The on-call rotation paged twice for the same flapping health check. The threshold was widened after review.",
    "Background workers backed up behind a slow database migration. It was split into batches and the queue drained.",
    "A canary release showed a 2% error-rate increase on checkout. The canary rolled back automatically and the commit was reverted.",
    "Two regions briefly disagreed about feature-flag state after a config push. The flag service reconciled within ninety seconds.",
    "Memory on the recommendation service grew over four days. An unbounded cache was given a size limit.",
]

# Eight candidates. Each states its facts plainly; only four satisfy the policy.
# The distribution is deliberate: every non-reportable one fails a DIFFERENT
# condition, so a model that has collapsed the rule to "was a secret exposed?"
# reports all eight, and one that over-applies the exceptions reports fewer
# than four. Both error directions are detectable.
CANDIDATES = {
    # REPORTABLE: C1 yes, 2h > 60m, company-wide channel we cannot unilaterally purge.
    41: ("REPORT", "While debugging the export pipeline a developer pasted the entire contents of their local .env file into the company-wide #engineering channel. It remained visible for about two hours before a moderator removed it. Message retention in that workspace is controlled by the vendor, not by us."),
    # NOT: fails C2 -- 12 minutes.
    88: ("SKIP", "A commit replaced the placeholder with the real value the production service authenticates to the database with, and was pushed to our PUBLIC GitHub repository. A pre-receive alert fired immediately and the branch was force-removed 12 minutes later."),
    # REPORTABLE: C1 yes, 3 days, public CI dashboard.
    134: ("REPORT", "The mobile build pipeline echoed the value it uses to sign release artifacts into its job log. That log sits on the project's public CI dashboard and stayed readable for three days until the retention window rolled it off."),
    # NOT: fails C3 -- internal bucket we control and purged ourselves.
    179: ("SKIP", "An onboarding export containing the values our partners use to authenticate to our API was copied into an internal S3 bucket readable by every employee. It sat there for nine days. The bucket is ours and the platform team deleted the object directly."),
    # NOT: exception E2 -- named NDA partner.
    226: ("SKIP", "A support agent attached the wrong file to a ticket reply and sent Northwind Logistics the service-account file our billing integration signs in with. Northwind is a named partner under our mutual NDA. The file was in their inbox for six hours before they confirmed deletion."),
    # REPORTABLE: C1 yes, 40 minutes of footage but exposure window is weeks; public help centre.
    287: ("REPORT", "A screen recording made for the onboarding guide was uploaded to the public help centre. For about forty seconds the terminal scrollback shows the private half of the production certificate. The video was live for five weeks before anyone noticed."),
    # NOT: exception E1 -- already rotated and revoked before exposure.
    333: ("SKIP", "An old deployment archive was attached to a public forum post. It contains the value CI formerly used to publish releases. That value had been rotated and revoked four months earlier, so it granted no access at any point. The post is still up."),
    # REPORTABLE: C1 yes, 2 days, customer inbox we cannot reach into.
    401: ("REPORT", "A misdirected invoice email sent Brightpath Retail the file our reconciliation job authenticates with. Brightpath has no agreement with us beyond their ordinary terms of service. Two days passed before they replied to say they had deleted it."),
}

# Credential-adjacent routine work: no secret reached anyone. Fails C1.
NEAR_MISSES = {
    19: "As part of quarterly hygiene the team rotated the payments API key ahead of schedule. The previous key was revoked the same hour and never left the secrets vault.",
    63: "A scanner flagged what looked like a hardcoded token in a test fixture. Review confirmed it is a dummy string granting no access to any environment.",
    108: "An engineer requested standing production database credentials. The request was denied and they received a time-boxed read replica session instead.",
    155: "Secrets were migrated between vaults. Access logs for the window were audited and confirmed no read by any principal outside the platform team.",
    203: "A phishing email asked twelve recipients to re-enter their SSO password. Nobody interacted with it and the message was purged from all mailboxes.",
    258: "An audit found three service accounts with broader permissions than needed. Their roles were narrowed; no credential material was disclosed.",
    310: "CI briefly printed environment variable NAMES while debugging a job configuration. Values were masked throughout, as the masking rules intend.",
    370: "A developer noticed the staging database password had not been rotated in fourteen months. It was rotated; the old value had only ever existed in the vault.",
}

lines = ["# Incident notes, FY26", ""]
for i in range(1, 421):
    if i in CANDIDATES:
        body = CANDIDATES[i][1]
    elif i in NEAR_MISSES:
        body = NEAR_MISSES[i]
    else:
        body = FILLER[i % len(FILLER)]
    lines.append(f"## note_{i:03d}")
    lines.append(body)
    lines.append("")

with open("notes/incidents.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))

# The answer key is NOT written into the workspace. The agent has read access
# to everything under its working directory, so a key on disk is a key it can
# find -- and a run that reads the answer instead of deriving it looks exactly
# like a run that reasoned perfectly. grade.sh holds the expected set instead.
PY

wc -c notes/incidents.md >/dev/null
