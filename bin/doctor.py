#!/usr/bin/env python3
"""Verify this machine can run the ablation matrix, before any tokens are spent.

Every check here exists because its absence produced a wrong or missing result
during development, not because it seemed prudent:

  dsh version   the overlays address composition rows BY ID. A release that
                renames or moves a row makes the patch apply to nothing, and
                the cell then measures the baseline while reporting itself as
                an axis. This is the failure this whole tool is built to
                prevent, so a version mismatch is fatal rather than a warning.
  zstd          session logs are `session.jsonl.zstd`; without it every metric
                comes back empty and the runs look like they produced nothing.
  profile       a missing or half-installed profile fails at mount, which is
                visible -- but a profile carrying EXTRA plugins fails silently
                by injecting tools into every cell.
  overlays      each one is composed for real; a typo'd row id is otherwise
                only discoverable by reading a result that looks plausible.

Exit code 0 means the matrix may run.

Usage:
    doctor.py [--profile ablation]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
from extract_metrics import dsh_home  # noqa: E402

EXPECTED_DSH = "0.1.0-rc.6"

# Everything a clean ablation profile should depend on. Anything else in the
# profile's node_modules top level is a contamination risk worth naming.
EXPECTED_BUNDLES = {"@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless"}


class Report:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  \033[32mok\033[0m    {label}{('  ' + detail) if detail else ''}")

    def warn(self, label: str, detail: str = "") -> None:
        print(f"  \033[33mwarn\033[0m  {label}{('  ' + detail) if detail else ''}")

    def fail(self, label: str, detail: str = "") -> None:
        self.failed = True
        print(f"  \033[31mFAIL\033[0m  {label}{('  ' + detail) if detail else ''}")


def check_commands(r: Report) -> None:
    print("commands")
    for cmd, why in [
        ("dsh", "the harness under test"),
        ("node", "runs dsh"),
        ("pnpm", "installs the profile"),
        ("zstd", "decompresses session logs; without it every metric is empty"),
    ]:
        path = shutil.which(cmd)
        if path:
            r.ok(cmd, path)
        else:
            r.fail(cmd, f"not on PATH -- {why}")


def check_version(r: Report) -> None:
    print("\ndsh version")
    if not shutil.which("dsh"):
        r.fail("version", "dsh not found")
        return
    try:
        out = subprocess.run(["dsh", "--version"], capture_output=True, text=True,
                             timeout=60).stdout.strip()
    except Exception as exc:
        r.fail("version", f"could not run `dsh --version`: {exc}")
        return
    if out == EXPECTED_DSH:
        r.ok("version", out)
    else:
        r.fail("version",
               f"found {out!r}, overlays are written for {EXPECTED_DSH!r}. "
               "Row ids may have moved; a patch that matches nothing produces a "
               "cell that silently measures the baseline.")


def check_profile(r: Report, profile: str) -> Path | None:
    print(f"\nprofile  {profile}")
    d = dsh_home() / "profiles" / profile
    if not d.is_dir():
        r.fail("directory", f"{d} missing -- run ./bootstrap.sh {profile}")
        return None
    r.ok("directory", str(d))

    pkg_path = d / "package.json"
    if not pkg_path.is_file():
        r.fail("package.json", "missing")
        return d
    pkg = json.loads(pkg_path.read_text())

    bundles = set(pkg.get("dsh", {}).get("profile", {}).get("bundles", []))
    if bundles == EXPECTED_BUNDLES:
        r.ok("bundles", ", ".join(sorted(bundles)))
    else:
        extra = bundles - EXPECTED_BUNDLES
        r.fail("bundles",
               f"expected exactly {sorted(EXPECTED_BUNDLES)}, found {sorted(bundles)}."
               + (f" Extra bundles inject tools into EVERY cell: {sorted(extra)}" if extra else ""))

    for name in ("dsh-ablation-prompt", "dsh-ablation-anchor"):
        target = d / "node_modules" / name
        if target.exists():
            r.ok(f"plugin {name}", "linked")
        else:
            r.fail(f"plugin {name}", "not installed -- re-run ./bootstrap.sh")

    patch = d / "cordis.patch.yml"
    if patch.is_file():
        body = [ln for ln in patch.read_text().splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        if body in ([], ["[]"]):
            r.ok("user patch layer", "empty, as required")
        else:
            r.fail("user patch layer",
                   "not empty -- whatever is here applies to every cell invisibly")
    return d


def check_overlays(r: Report, profile: str) -> None:
    """Compose every overlay the way run_matrix.py actually composes it.

    Cell overlays are NOT independently valid and must not be checked as if
    they were: `b0-minimal.yml` disables `tool-ask-user`, a row that only
    exists because `_base-standard.yml` inserted it, so composing it alone
    fails with `entry "tool-ask-user" not found`. Checking each file in
    isolation therefore reports a defect that does not exist while testing a
    configuration that never runs.
    """
    print("\noverlays")
    base = ROOT / "overlays" / "_base-standard.yml"
    overlays = sorted((ROOT / "overlays").glob("*.yml"))
    if not overlays:
        r.fail("overlays", "none found")
        return
    if not base.is_file():
        r.fail("_base-standard.yml", "missing -- every cell is layered on it")
        return
    for path in overlays:
        patches = ["--patch", str(base)]
        if path != base:
            patches += ["--patch", str(path)]
        proc = subprocess.run(
            ["dsh", "--profile", profile, *patches, "--dump-config"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0 and not proc.stderr.strip():
            r.ok(path.name)
        else:
            detail = (proc.stderr.strip().splitlines() or ["non-zero exit"])[0]
            r.fail(path.name, detail[:160])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ablation")
    parser.add_argument("--skip-overlays", action="store_true",
                        help="skip the per-overlay composition check (it boots dsh once per overlay)")
    args = parser.parse_args()

    print(f"dsh home  {dsh_home()}\n")
    r = Report()
    check_commands(r)
    check_version(r)
    profile_dir = check_profile(r, args.profile)
    if profile_dir and not args.skip_overlays and shutil.which("dsh"):
        check_overlays(r, args.profile)

    print()
    if r.failed:
        print("\033[31mNOT READY\033[0m -- fix the FAIL lines above before spending tokens.")
        return 1
    print("\033[32mREADY\033[0m -- the matrix may run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
