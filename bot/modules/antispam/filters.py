from __future__ import annotations

import re

from bot.models import FilterType, Plan

LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)\S+", re.IGNORECASE)
LINK_DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?([^/\s:@]+)", re.IGNORECASE | re.UNICODE)


def detect_links(text: str, whitelist: frozenset[str]) -> bool:
    for match in LINK_RE.findall(text):
        m = LINK_DOMAIN_RE.search(match)
        domain = m.group(1).lower() if m else ""
        if domain and domain not in whitelist:
            return True
        if not domain:
            return True
    return False


def detect_caps(text: str, *, threshold: float = 0.7, min_len: int = 8) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters or len(letters) < min_len:
        return False
    upper = sum(ch.isupper() for ch in letters)
    return upper / len(letters) > threshold


def detect_stopwords(text: str, stopwords: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in stopwords)


def check_text(text: str, cfg: dict, plan: str) -> FilterType | None:
    if plan == Plan.PRO.value:
        whitelist = frozenset(cfg.get("link_whitelist", []))
        stopwords = frozenset(cfg.get("stopwords", []))
    else:
        whitelist = frozenset()
        stopwords = frozenset()
    if stopwords and detect_stopwords(text, stopwords):
        return FilterType.STOPWORD
    if detect_links(text, whitelist):
        return FilterType.LINK
    if detect_caps(
        text, threshold=cfg.get("caps_threshold", 0.7), min_len=cfg.get("caps_min_len", 8)
    ):
        return FilterType.CAPS
    return None
