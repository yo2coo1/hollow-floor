"""Viewer vote for Part 2. Part 1 ends on two choices, each with a one-word label (e.g. OPEN / RUN).
Comments are untrusted: they are only ever counted, never passed to the story writer.

A comment votes when it names exactly one choice: its label word ("open"), its number ("1"/"2"), or its letter ("A"/"B").
One vote per person per platform (their latest comment counts); the channel's own comments are ignored.
Majority wins; a tie or too few votes falls back to the writer's default branch."""
import re


def parse_vote(text, label_a, label_b):
    t = text.lower()
    a = bool(re.search(rf"\b{re.escape(label_a.lower())}\b", t)) or bool(re.fullmatch(r"\W*(1|a|option\s*1|option\s*a)\W*", t))
    b = bool(re.search(rf"\b{re.escape(label_b.lower())}\b", t)) or bool(re.fullmatch(r"\W*(2|b|option\s*2|option\s*b)\W*", t))
    return "A" if a and not b else "B" if b and not a else None


def tally(comments_by_platform, label_a, label_b, own_ids=()):
    """comments_by_platform: {"youtube": [{"author", "text", "at"}], ...} -> {"A": n, "B": n, "by_platform": {...}}"""
    total = {"A": 0, "B": 0, "by_platform": {}}
    for platform, comments in comments_by_platform.items():
        latest = {}
        for c in sorted(comments, key=lambda c: c.get("at", "")):
            if c["author"] in own_ids:
                continue
            v = parse_vote(c.get("text", ""), label_a, label_b)
            if v:
                latest[c["author"]] = v
        counts = {"A": sum(v == "A" for v in latest.values()), "B": sum(v == "B" for v in latest.values())}
        total["by_platform"][platform] = counts
        total["A"] += counts["A"]; total["B"] += counts["B"]
    return total


def decide(counts, default_choice, min_votes):
    """Returns (winner, reason)."""
    n = counts["A"] + counts["B"]
    if n < min_votes:
        return default_choice, f"only {n} votes (< {min_votes}), used the default"
    if counts["A"] == counts["B"]:
        return default_choice, f"tie {counts['A']}-{counts['B']}, used the default"
    w = "A" if counts["A"] > counts["B"] else "B"
    return w, f"viewers chose {w} {counts[w]}-{counts['B' if w == 'A' else 'A']}"
