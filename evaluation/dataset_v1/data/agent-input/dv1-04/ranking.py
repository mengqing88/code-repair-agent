"""Leaderboard ranking helpers."""

from __future__ import annotations


def top_k(scores, k):
    """Return the ``k`` best ``(name, score)`` pairs.

    Higher score first; equal scores are ordered by name ascending.
    """
    if k < 0:
        raise ValueError("k must be >= 0")
    ranked = sorted(scores, key=lambda item: -item[1])
    return ranked[:k]
