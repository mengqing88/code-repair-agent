"""Leaderboard ordering."""

from __future__ import annotations


def rank(players):
    """Sort by group ascending, then score descending, keeping ties stable."""
    return sorted(sorted(players, key=lambda item: item["group"]), key=lambda item: -item["score"])
