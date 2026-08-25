"""Shared error types for the convert pipeline."""

from __future__ import annotations


class ConversionCancelled(RuntimeError):
    """Raised when the user cancels a conversion (GUI Cancel or CLI SIGINT).

    Prefer catching this type over matching ``str(exc)`` for cancel detection.
    Subclasses :class:`RuntimeError` so older ``except RuntimeError`` handlers
    that matched the ``"Cancelled"`` message still work.
    """

    def __init__(self, message: str = "Cancelled") -> None:
        super().__init__(message)
