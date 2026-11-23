"""Match a user's message to a configured question.

Score (0..1) for each enabled question that has an active answer in an enabled
category, using the best of:

* character similarity of the whole message to the question text, and
* word overlap (F1) of their meaningful words (common Indonesian/English
  filler words removed),

then raised when the message contains one of the question's keywords
(one keyword -> at least 0.65, each further keyword +0.15).

The question with the highest score is used when it reaches the "Match
strictness" threshold from Assistant settings.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import ConfiguredAnswer, ConfiguredQuestion, QuestionCategory
from .knowledge_search import normalize

STOPWORDS = frozenset("""
    a an the is are was were be to of in on at for and or what when where who how which do does did
    can could i you me my your it this that there please about with from
    apa apakah kapan dimana di mana siapa bagaimana berapa yang dan atau ke dari untuk dengan
    ini itu ada saya aku kamu anda tolong mohon bisa boleh nya kah sih ya tentang pada adalah
""".split())

KEYWORD_FLOOR = 0.5
KEYWORD_STEP = 0.15


@dataclass
class Match:
    question: ConfiguredQuestion
    score: float
    keywords: list


def words(text):
    return {w for w in normalize(text).split() if w not in STOPWORDS and len(w) > 1}


def score_question(message, question):
    """(score, matched keywords) for one question."""
    text = normalize(message)
    target = normalize(question.text)
    if not text or not target:
        return 0.0, []
    if text == target:
        return 1.0, []

    similarity = SequenceMatcher(None, text, target).ratio()
    mine, theirs = words(message), words(question.text)
    overlap = len(mine & theirs)
    f1 = 2 * overlap / (len(mine) + len(theirs)) if overlap else 0.0
    score = max(similarity, f1)

    padded = f" {text} "
    matched = [k for k in (question.keywords or []) if (nk := normalize(k)) and f" {nk} " in padded]
    if matched:
        score = max(score, KEYWORD_FLOOR) + KEYWORD_STEP * len(matched)
    return round(min(score, 1.0), 3), matched


def answerable_questions():
    """Questions AI Chat may answer with: enabled, in an enabled category, with an enabled answer."""
    return db.session.scalars(
        select(ConfiguredQuestion).join(ConfiguredQuestion.category)
        .where(ConfiguredQuestion.is_enabled.is_(True), QuestionCategory.is_enabled.is_(True))
        .options(selectinload(ConfiguredQuestion.answers).selectinload(ConfiguredAnswer.template))
        .order_by(ConfiguredQuestion.display_order, ConfiguredQuestion.id)
    ).all()


def rank_questions(message, limit=5):
    """Best candidates first (ties keep the admin's display order)."""
    results = []
    for question in answerable_questions():
        if question.active_answer is None:
            continue
        score, matched = score_question(message, question)
        if score > 0:
            results.append(Match(question, score, matched))
    results.sort(key=lambda m: m.score, reverse=True)  # stable sort
    return results[:limit]


def best_match(message, threshold):
    """(match or None, best candidate or None)."""
    ranked = rank_questions(message, limit=1)
    best = ranked[0] if ranked else None
    return (best if best and best.score >= threshold else None), best
