"""Backward-compatible alias for the suspend-on-delegate checkpoint.

`PendingDelegation` became `PendingCompletion` when the deferred-tool-round
machinery was unified — sub-agent delegation is now one completer kind among
several (see `app/models/pending_completion.py`). Import the new name in new
code; this alias keeps existing imports working.
"""
from .pending_completion import PendingCompletion

PendingDelegation = PendingCompletion
