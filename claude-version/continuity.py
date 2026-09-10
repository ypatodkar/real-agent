"""Conflicts are computed before the prompt, not asked for.

An agent that must remember to call a continuity tool will call it exactly when
it is already suspicious — which is when it is least needed. So this runs on
every incoming answer and the result goes into the snapshot.

Crude on purpose: shared subject terms plus opposed polarity. It flags
candidates for the editor to raise with the writer. It never decides which
version is true, and a miss is not a correctness bug — it is a missed prompt.
"""

from __future__ import annotations

import re

NEGATORS = {"no", "not", "never", "nobody", "nothing", "none", "cannot", "cant",
            "doesnt", "didnt", "wont", "isnt", "wasnt", "hasnt", "havent"}

STOP = {"the", "and", "that", "this", "with", "from", "have", "has", "his", "her",
        "she", "they", "them", "their", "what", "when", "where", "which", "who",
        "into", "onto", "about", "just", "then", "than", "will", "would", "been",
        "was", "were", "are", "for", "but", "him", "you", "your", "has", "had"}


def terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{2,}", text.lower()) if w not in STOP}


def negated(text: str) -> bool:
    words = set(re.findall(r"[a-z']+", text.lower()))
    return bool(words & NEGATORS)


def conflicts(new_text: str, facts) -> list[dict]:
    """Facts the new text may contradict. Each is a candidate, not a verdict."""
    if not new_text.strip():
        return []
    new_terms = terms(new_text)
    if len(new_terms) < 2:
        return []

    found = []
    for fact in facts:
        fact_text = fact["text"] if not isinstance(fact, str) else fact
        shared = new_terms & terms(fact_text)
        opposed = negated(fact_text) != negated(new_text)
        # Two shared subjects, or one shared subject with opposed polarity.
        # "Nobody knows about the key" against "his brother has known about the
        # key for years" shares only `key` once stop words are gone, and that
        # pair is exactly the contradiction worth raising.
        if len(shared) < (1 if opposed else 3):
            continue
        if opposed or len(shared) >= 3:
            found.append({
                "fact_id": fact["id"] if not isinstance(fact, str) else "",
                "fact": fact_text,
                "shared": sorted(shared)[:4],
            })
    return found[:3]
