"""One exception family, so `cli.py` has exactly one thing to catch.

R6.1 says every verb exits non-zero with the reason on stderr. That is only
simple if every foreseeable failure arrives as a BrainError; anything else
reaching the top level is a bug worth a traceback.
"""
from __future__ import annotations


class BrainError(Exception):
    """Anything the user should see as a one-line refusal, not a traceback."""


class RootNotFound(BrainError):
    """No brain root was given, found in the environment, or found above cwd."""


class GrammarError(BrainError):
    """A command line could not be parsed (R6.4)."""


class UnknownReference(BrainError):
    """A reference matched nothing (R6.2)."""


class AmbiguousReference(BrainError):
    """A reference matched more than one entity. R6.2: never a guess."""


class VerbError(BrainError):
    """A verb refused: bad argument, missing section, unknown status."""


# `brain/git.py` defines GitError before this module existed; Step 4 re-homes it
# under BrainError rather than redefining it here. R6.1 wants ONE thing for the
# CLI to catch - a git refusal is a refusal like any other.
