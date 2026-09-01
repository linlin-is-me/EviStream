"""Stable multilingual normalization used by indexing and querying."""

import re
import unicodedata

LATIN_TOKEN = re.compile(r"[a-z0-9]+")
CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).lower()).strip()


def search_lexemes(value: str) -> str:
    normalized = normalize_text(value)
    tokens = LATIN_TOKEN.findall(normalized)
    for run in CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(dict.fromkeys(token for token in tokens if token))
