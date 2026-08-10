"""Deterministic Portuguese detection for the `response_language_pt` oracle.

`LanguageDetector` was a Protocol with no implementation, and no runner ever
passed one, so the assertion always evaluated to inconclusive: the corpus
declared that final responses must be in Portuguese and could never check it.

ponytail: function-word counting, not a language model. It separates Portuguese
from English reliably on answers of a sentence or more, which is what the
fixtures produce. It does not separate Portuguese from Spanish, and it abstains
on very short text instead of guessing. Upgrade path is a real classifier, and
it would need its own digest in the RuntimeProfile to stay reproducible.
"""

from __future__ import annotations

import re
import unicodedata

# Function words carry the signal: they are frequent, and a model answering in
# the wrong language cannot avoid its own.
_PORTUGUESE = frozenset(
    [
        "a",
        "as",
        "o",
        "os",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "por",
        "para",
        "com",
        "sem",
        "que",
        "nao",
        "e",
        "ou",
        "mas",
        "se",
        "ja",
        "foi",
        "ser",
        "sao",
        "esta",
        "estao",
        "tem",
        "temos",
        "onde",
        "quando",
        "como",
        "isso",
        "este",
        "esta",
        "esse",
        "essa",
        "aquele",
        "aquela",
        "voce",
        "eu",
        "nos",
        "seu",
        "sua",
        "seus",
        "suas",
        "mais",
        "menos",
        "muito",
        "pouco",
        "entao",
        "porque",
        "pois",
        "ate",
        "depois",
        "antes",
        "sobre",
        "entre",
        "cada",
        "todos",
        "todas",
        "arquivo",
        "arquivos",
        "pagina",
        "conteudo",
        "busca",
        "encontrado",
        "encontrados",
    ]
)

_ENGLISH = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "and",
        "or",
        "but",
        "if",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "there",
        "here",
        "what",
        "when",
        "where",
        "which",
        "who",
        "how",
        "not",
        "no",
        "yes",
        "file",
        "files",
        "page",
        "content",
        "search",
        "found",
        "result",
        "results",
    ]
)

# Sequences that occur in Portuguese and essentially never in English.
_PORTUGUESE_MARKERS = ("ção", "ções", "ão", "õe", "nh", "lh", "ç")

_MINIMUM_WORDS = 6
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


class PortugueseDetector:
    """Counts function words in each language and answers only when they disagree."""

    @property
    def deterministic(self) -> bool:
        return True

    def is_portuguese(self, text: str) -> bool:
        words = _WORD.findall(text)
        if len(words) < _MINIMUM_WORDS:
            # Too short to distinguish; the oracle treats abstention as a failure
            # to demonstrate Portuguese, which is the conservative reading.
            return False
        folded = [_fold(word) for word in words]
        portuguese = sum(word in _PORTUGUESE for word in folded)
        english = sum(word in _ENGLISH for word in folded)
        lowered = text.casefold()
        markers = sum(lowered.count(marker) for marker in _PORTUGUESE_MARKERS)
        return portuguese + markers > english


__all__ = ["PortugueseDetector"]
