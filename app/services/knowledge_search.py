"""Keyword/overlap scoring over knowledge entries stored in the database.

Same scoring rules as the original services/school_data.py, but the data now
comes from the `knowledge_entries` table (published + available to AI only).
"""

import re

from sqlalchemy import select

from ..extensions import db
from ..models import KnowledgeEntry

MIN_SCORE = 2
MIN_MARGIN = 0.5


def normalize(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(entry, query, query_words, category):
    title_words = set(normalize(entry.title).split())
    content_words = set(normalize(entry.content_markdown).split())
    keywords = [normalize(k) for k in (entry.keywords or [])]
    keyword_matches = [k for k in keywords if k and k in query]

    score = len(query_words & title_words) + len(query_words & content_words) * 0.25
    score += sum(min(len(k.split()), 3) * 2 for k in keyword_matches)
    if category and entry.category and entry.category.slug == category:
        score += 0.5
    return score, bool(keyword_matches)


def rank_knowledge(question, category=None, include_unavailable=False):
    """All entries with a positive score, best first: [(score, has_keyword, entry)]."""
    query = normalize(question)
    query_words = set(query.split())

    stmt = select(KnowledgeEntry)
    if not include_unavailable:
        stmt = stmt.where(KnowledgeEntry.is_published.is_(True), KnowledgeEntry.available_to_ai.is_(True))

    results = []
    for entry in db.session.scalars(stmt):
        score, has_keyword = _score(entry, query, query_words, category)
        if score > 0:
            results.append((score, has_keyword, entry))
    results.sort(key=lambda r: r[0], reverse=True)
    return results


def search_knowledge(question, category=None):
    """The single best entry to answer with, or None when nothing is a confident match."""
    results = rank_knowledge(question, category)
    if not results:
        return None

    best_score, best_has_keyword, best = results[0]
    if best_score < MIN_SCORE:
        return None

    # Two entries almost tied and neither matched a keyword: too ambiguous to answer.
    if len(results) > 1 and best_score - results[1][0] < MIN_MARGIN:
        if not (best_has_keyword or results[1][1]):
            return None

    return best
