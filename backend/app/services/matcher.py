"""Match a customer email to one topic, or to none.

The matcher never writes text. It only chooses which topic (if any) the email is about, and
reports how sure it is so a person can see why.

Method: each topic has example phrasings ("How do I reset my PIN?"). We split the
email into sentences and compare every sentence with every example using TF-IDF cosine
similarity over words and character fragments (which tolerates typos like "pasword"). The
topic's score is its best sentence-to-example similarity.

An email is matched only when:
  * the best score reaches the confidence threshold, and
  * the runner-up topic is clearly behind (otherwise the email is ambiguous).
Anything else goes to an officer. It runs in pure Python, with no model files to download,
and gives the same result every time for the same input.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field

# If the second-best topic scores within this distance of the best, we do not guess.
AMBIGUITY_MARGIN = 0.05
MAX_SENTENCES = 40

_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have i im i'm in is it its me my of on or our please "
    "so that the their them there this to us was we what when where which who will with would you your "
    "hi hello dear good morning afternoon evening regards thanks thank kind sir madam team".split()
)


def normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _features(text: str) -> Counter:
    words = [w for w in normalise(text).split() if w not in _STOPWORDS]
    feats: Counter = Counter()
    feats.update(f"w:{w}" for w in words)
    feats.update(f"b:{a}_{b}" for a, b in zip(words, words[1:], strict=False))
    for w in words:
        padded = f" {w} "
        for n in (3, 4):
            feats.update(f"c:{padded[i:i + n]}" for i in range(len(padded) - n + 1))
    return feats


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p and p.strip()]
    # Very short fragments ("Hi team,") carry no meaning and only add noise.
    parts = [p for p in parts if len(normalise(p).split()) >= 2]
    return parts[:MAX_SENTENCES]


@dataclass
class Candidate:
    # For a topic: topic id + revision id. For a knowledge entry: entry id + document version id.
    topic_id: uuid.UUID
    revision_id: uuid.UUID
    title: str
    examples: list[str]
    kind: str = "topic"  # "topic" | "knowledge"


@dataclass
class ScoredAnswer:
    topic_id: uuid.UUID
    revision_id: uuid.UUID
    title: str
    score: float
    best_example: str
    best_sentence: str
    kind: str = "topic"


@dataclass
class MatchResult:
    matched: ScoredAnswer | None
    ranking: list[ScoredAnswer] = field(default_factory=list)
    reason: str = ""
    reason_code: str | None = None  # "no_match" or "ambiguous" when nothing was matched


class Matcher:
    def __init__(self, candidates: list[Candidate]):
        self._candidates = candidates
        self._examples: list[tuple[int, str, Counter]] = []
        doc_freq: Counter = Counter()
        for idx, cand in enumerate(candidates):
            texts = [cand.title, *cand.examples]
            for text in texts:
                feats = _features(text)
                if feats:
                    self._examples.append((idx, text, feats))
                    doc_freq.update(feats.keys())
        n_docs = max(len(self._examples), 1)
        self._idf = {f: math.log((1 + n_docs) / (1 + df)) + 1.0 for f, df in doc_freq.items()}
        self._default_idf = math.log(1 + n_docs) + 1.0
        self._example_vecs = [(idx, text, self._vector(feats)) for idx, text, feats in self._examples]

    def _vector(self, feats: Counter) -> dict[str, float]:
        vec = {f: (1 + math.log(c)) * self._idf.get(f, self._default_idf) for f, c in feats.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {f: v / norm for f, v in vec.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(v * b.get(f, 0.0) for f, v in a.items())

    def rank(self, subject: str, body: str) -> list[ScoredAnswer]:
        body_sentences = split_sentences(body)
        sentences = split_sentences(subject) + body_sentences
        # Also read the opening as a whole: "I forgot my PIN. How can I reset it?" only makes
        # sense together, because "it" in the second sentence means the PIN.
        if len(body_sentences) > 1:
            sentences.append(" ".join(body_sentences[:3]))
        best: dict[int, ScoredAnswer] = {}
        for sentence in sentences:
            feats = _features(sentence)
            if not feats:
                continue
            svec = self._vector(feats)
            for idx, example_text, evec in self._example_vecs:
                score = self._cosine(svec, evec)
                current = best.get(idx)
                if current is None or score > current.score:
                    cand = self._candidates[idx]
                    best[idx] = ScoredAnswer(
                        cand.topic_id,
                        cand.revision_id,
                        cand.title,
                        round(score, 4),
                        example_text,
                        sentence,
                        cand.kind,
                    )
        return sorted(best.values(), key=lambda s: s.score, reverse=True)

    def match(self, subject: str, body: str, threshold: float) -> MatchResult:
        ranking = self.rank(subject, body)
        if not ranking:
            return MatchResult(
                None, ranking, "Nothing in the knowledge base or topics to compare with yet.", "no_match"
            )
        top = ranking[0]
        if top.score < threshold:
            return MatchResult(
                None,
                ranking,
                f"Nothing matches closely enough (closest: {top.title}, {top.score:.0%}).",
                "no_match",
            )
        if len(ranking) > 1 and top.score - ranking[1].score < AMBIGUITY_MARGIN:
            return MatchResult(
                None,
                ranking,
                f"Could be '{top.title}' or '{ranking[1].title}'; too close to choose.",
                "ambiguous",
            )
        return MatchResult(top, ranking, f"Matched '{top.title}' with score {top.score:.2f}.")
