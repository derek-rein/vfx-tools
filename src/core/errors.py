"""Shared error types for the convert pipeline."""

from __future__ import annotations


class ConversionCancelled(Exception):
    """Raised when the user cancels a conversion (GUI Cancel or CLI SIGINT).

    Prefer catching this type over matching ``str(exc)`` for cancel detection.
    """

    def __init__(self, message: str = "Cancelled") -> None:
        super().__init__(message)
