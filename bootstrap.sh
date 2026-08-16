#!/usr/bin/env bash
# Create the ablation profile this testbed runs on. Idempotent -- re-running it
# is how you repair a profile, not something to avoid.
#
# The profile is a GENERATED artifact living under $DSH_HOME, not part of this
# repository. That is why it may contain absolute paths: each machine generates
# its own. Nothing committed here is machine-specific.
#
# The profile holds ONLY dsh-base + dsh-headless plus this repo's two helper
# plugins. That emptiness is the point: a third-party plugin injects its tools
# into whatever preset is selected, and an ablation run against a contaminated
# baseline measures the contamination. (Measured on the author's daily profile:
# `ssh_*` x6 and `vision_*` x3 took Minimal from 2 tools to 11 and Standard
# from 25 to 34.)
#
# Usage:
#   ./bootstrap.sh [profile-name]     # default: ablation
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${1:-ablation}"
DSH_HOME_DIR="${DSH_HOME:-$HOME/.dsh}"
DIR="$DSH_HOME_DIR/profiles/$PROFILE"

# The overlays address rows by id in the composition dsh ships at this exact
# version. A different release may rename, move, or merge those rows, in which
# case a patch silently applies to nothing and the cell measures the baseline
# while claiming to measure an axis. bin/doctor.py enforces the match; this
# pin is what it enforces against.
DSH_VERSION="0.1.0-rc.6"

for cmd in dsh pnpm node; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "missing required command: $cmd" >&2; exit 1; }
done

echo "repo     $REPO"
echo "profile  $DIR"
echo "dsh pin  $DSH_VERSION"
echo

mkdir -p "$DIR"

cat > "$DIR/package.json" <<JSON
{
  "name": "dsh-profile-$PROFILE",
  "private": true,
  "dependencies": {
    "@deepseek-ai/dsh-base": "$DSH_VERSION",
    "@deepseek-ai/dsh-headless": "$DSH_VERSION",
    "@deepseek-ai/dsh-persona": "$DSH_VERSION",
    "@deepseek-ai/dsh-terminal": "$DSH_VERSION",
    "@deepseek-ai/dsh-terminal-bash": "$DSH_VERSION",
    "@deepseek-ai/dsh-tool-bash-persistent": "$DSH_VERSION",
    "@deepseek-ai/dsh-tool-ask-user": "$DSH_VERSION",
    "dsh-ablation-prompt": "link:$REPO/plugins/dsh-ablation-prompt",
    "dsh-ablation-anchor": "link:$REPO/plugins/dsh-ablation-anchor"
  },
  "dsh": {
    "profile": {
      "bundles": [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-headless"
      ]
    }
  }
}
JSON

cat > "$DIR/cordis.yml" <<'YAML'
# dsh profile root -- an empty entry list. The tree is composed as patches:
# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml,
# then any --patch overlays. Edit cordis.patch.yml, not this file.
[]
YAML

cat > "$DIR/cordis.patch.yml" <<'YAML'
# Ablation testbed profile -- user layer.
#
# Deliberately EMPTY. Every experimental condition arrives as a `--patch`
# overlay, so that the profile's own layer can never become a hidden confound
# shared by all cells. If this file stops being empty, every result produced
# afterwards carries whatever you put here, in every cell, invisibly.
[]
YAML

cat > "$DIR/pnpm-workspace.yaml" <<'YAML'
packages:
  - .

nodeLinker: hoisted
autoInstallPeers: false

# The B0 and A4 cells mount the persistent PTY shell, which is native
# (node-pty), and every cell needs the local subprocess backend. Both must be
# allowed to build or those cells fail at mount rather than producing a
# datapoint. The rest are declined: nothing here uses them.
allowBuilds:
  '@deepseek-ai/dsh-subprocess-local': true
  '@google/genai': false
  koffi: false
  node-pty: true
  protobufjs: true
YAML

echo "installing helper plugin dependencies..."
for plugin in dsh-ablation-prompt dsh-ablation-anchor; do
  (cd "$REPO/plugins/$plugin" && pnpm install --reporter=append-only </dev/null >/dev/null)
done

echo "installing profile dependencies..."
(cd "$DIR" && pnpm install --reporter=append-only </dev/null | tail -3)

echo
echo "done. verify with:  python3 $REPO/bin/doctor.py --profile $PROFILE"
