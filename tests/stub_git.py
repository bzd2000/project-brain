#!/usr/bin/env python3
"""A10's stub `git`: records argv, refuses what R11.4 forbids, else delegates.

Installed ahead of the real git on PATH by the `stub_git` fixture. It is the
only thing that can catch `brain` reaching for a forbidden operation, so it
delegates rather than mocking — the tests still exercise real git.
"""
import os
import subprocess
import sys

# R11.4: never push, fetch, pull, merge, rebase, branch, amend, cherry-pick or
# clean. Matched on the exact subcommand, so `merge-base` is unaffected.
FORBIDDEN_SUBCOMMANDS = {
    "push", "fetch", "pull", "merge", "rebase", "clean", "cherry-pick", "branch",
}


def main() -> int:
    argv = sys.argv[1:]
    log = os.environ.get("BRAIN_GIT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("\x00".join(argv) + "\n")

    sub = next((a for a in argv if not a.startswith("-")), None)
    reason = None
    if sub in FORBIDDEN_SUBCOMMANDS:
        reason = "R11.4 forbids `git {}`".format(sub)
    elif sub == "checkout" and "-b" in argv:
        reason = "R11.4 forbids creating branches"
    elif sub == "add" and ({"-A", "--all"} & set(argv)):
        reason = "R11.2 forbids `git add -A`"
    elif sub == "commit" and "--amend" in argv:
        reason = "R11.4 forbids amending"
    if reason:
        sys.stderr.write("STUB GIT REFUSED: {}\n  argv: {}\n".format(reason, argv))
        return 99

    real = os.environ["BRAIN_REAL_GIT"]
    return subprocess.call([real] + argv)


if __name__ == "__main__":
    sys.exit(main())
