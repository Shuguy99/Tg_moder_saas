from bot.models import FilterType, Plan
from bot.modules.antispam.filters import (
    check_text,
    detect_caps,
    detect_links,
    detect_stopwords,
)

CFG = {
    "link_whitelist": ["example.com"],
    "stopwords": ["free money", "spam"],
    "caps_threshold": 0.7,
    "caps_min_len": 8,
}


def test_detect_links_finds_plain_url():
    assert detect_links("смотри http://evilsite.net/abc", frozenset()) is True


def test_detect_links_ignores_whitelisted():
    assert detect_links("www.example.com/hello", frozenset({"example.com"})) is False


def test_detect_links_finds_tme():
    assert detect_links("подпишись t.me/anybody", frozenset()) is True


def test_detect_caps_positive():
    assert detect_caps("СРОЧНАЯ ПРОДАЖА ТОКЕНОВ") is True


def test_detect_caps_short_negative():
    assert detect_caps("ОК КУПЛЮ") is False


def test_detect_caps_mostly_lower_negative():
    assert detect_caps("Покупка и Продажа токенов для друзей") is False


def test_detect_stopwords_positive_and_negative():
    assert detect_stopwords("получи free money сейчас", frozenset({"free money"})) is True
    assert detect_stopwords("нормальное сообщение", frozenset({"free money"})) is False


def test_check_text_stopword_first():
    assert check_text("free money http://evilsite.net", CFG, Plan.PRO.value) == FilterType.STOPWORD


def test_check_text_link_when_no_stopwords():
    assert check_text("http://evilsite.net", CFG, Plan.PRO.value) == FilterType.LINK


def test_check_text_whitelisted_link_not_fired():
    assert check_text("www.example.com", CFG, Plan.PRO.value) is None


def test_check_text_caps():
    assert check_text("АКЦИЯ ВСЕМ РАЗДАТЬ ТОКЕНЫ БЕСПЛАТНО", CFG, Plan.PRO.value) == FilterType.CAPS


def test_check_text_free_ignores_stopwords_and_whitelist():
    assert check_text("получи free money", CFG, Plan.FREE.value) is None
    assert check_text("www.example.com", CFG, Plan.FREE.value) == FilterType.LINK
