"""
MTC Backend Unit Tests
Tests pure functions that power trend normalization, scoring, and enrichment.
No external API calls — these run locally with zero dependencies.

Run: cd mtc-backend && python -m pytest tests/ -v
"""
import time
import math
import pytest


# ─── Imports from the backend ─────────────────────────────────────────────────

from index import (
    _slugify,
    _normalize_trend,
    _parse_timestamp,
    _parse_relative,
    _subreddit_category,
    _normalize_reddit_trend,
    _cache_get,
    _cache_set,
)
from collectors.reddit_collector import _velocity_score


# ═══════════════════════════════════════════════════════════════════════════════
# _slugify
# ═══════════════════════════════════════════════════════════════════════════════

class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("What's up? #trending!") == "what-s-up-trending"

    def test_unicode(self):
        # alavés should become alaves
        result = _slugify("Deportivo Alavés")
        assert "alav" in result
        assert all(c.isascii() for c in result)

    def test_empty(self):
        assert _slugify("") == "trend"
        assert _slugify(None) == "trend"

    def test_max_len(self):
        long_text = "a" * 100
        assert len(_slugify(long_text)) <= 60
        assert len(_slugify(long_text, max_len=10)) <= 10

    def test_only_special_chars(self):
        assert _slugify("!!!???") == "trend"

    def test_leading_trailing_dashes(self):
        result = _slugify("  --hello--  ")
        assert not result.startswith("-")
        assert not result.endswith("-")


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_timestamp
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseTimestamp:
    def test_unix_seconds(self):
        result = _parse_timestamp(1700000000)
        assert result is not None
        assert "2023-11-14" in result

    def test_unix_milliseconds(self):
        result = _parse_timestamp(1700000000000)
        assert result is not None
        assert "2023-11-14" in result

    def test_iso_string(self):
        result = _parse_timestamp("2024-01-15T10:30:00Z")
        assert result is not None
        assert "2024-01-15" in result

    def test_iso_with_timezone(self):
        result = _parse_timestamp("2024-01-15T10:30:00+05:00")
        assert result is not None

    def test_relative_hours_ago(self):
        result = _parse_timestamp("3 hours ago")
        assert result is not None
        # Should be a recent ISO timestamp
        assert "T" in result

    def test_relative_days_ago(self):
        result = _parse_timestamp("2 days ago")
        assert result is not None

    def test_relative_months_ago(self):
        result = _parse_timestamp("6 months ago")
        assert result is not None

    def test_none(self):
        assert _parse_timestamp(None) is None

    def test_empty_string(self):
        assert _parse_timestamp("") is None

    def test_garbage(self):
        assert _parse_timestamp("not a timestamp at all") is None

    def test_zero(self):
        # 0 is outside the sanity range (1e8 < ts < 1e11)
        assert _parse_timestamp(0) is None

    def test_negative(self):
        assert _parse_timestamp(-1) is None

    def test_float_seconds(self):
        result = _parse_timestamp(1700000000.5)
        assert result is not None
        assert "2023-11-14" in result


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_relative
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseRelative:
    def test_hours_ago(self):
        result = _parse_relative("3 hours ago")
        assert result is not None

    def test_singular_unit(self):
        result = _parse_relative("1 hour ago")
        assert result is not None

    def test_days_ago(self):
        result = _parse_relative("5 days ago")
        assert result is not None

    def test_no_match(self):
        assert _parse_relative("hello world") is None
        assert _parse_relative("") is None

    def test_youtube_style(self):
        # YouTube returns things like "2 weeks ago"
        result = _parse_relative("2 weeks ago")
        assert result is not None

    def test_year_ago(self):
        result = _parse_relative("1 year ago")
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════════
# _subreddit_category
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubredditCategory:
    def test_known_subreddits(self):
        assert _subreddit_category("nba") == "Sports"
        assert _subreddit_category("nfl") == "Sports"
        assert _subreddit_category("technology") == "Business"
        assert _subreddit_category("movies") == "Entertainment"
        assert _subreddit_category("news") == "News"

    def test_case_insensitive(self):
        assert _subreddit_category("NBA") == "Sports"
        assert _subreddit_category("Technology") == "Business"

    def test_unknown_subreddit(self):
        assert _subreddit_category("obscuresubreddit123") == "Trending"

    def test_empty(self):
        assert _subreddit_category("") == "Trending"
        assert _subreddit_category(None) == "Trending"


# ═══════════════════════════════════════════════════════════════════════════════
# _normalize_trend (Google Trends → Pulse schema)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeTrend:
    def test_basic(self):
        raw = {
            "query": "NBA Finals",
            "search_volume": 500000,
            "increase_percentage": 200,
            "active": True,
            "start_timestamp": int(time.time()) - 3600,
            "categories": [{"id": 1, "name": "Sports"}],
            "trend_breakdown": ["Game 7", "Celtics"],
            "news_page_token": "abc123",
        }
        result = _normalize_trend(raw)
        assert result["query"] == "NBA Finals"
        assert result["search_volume"] == 500000
        assert result["velocity_pct"] == 200
        assert result["active"] is True
        assert result["categories"] == ["Sports"]
        assert result["trend_breakdown"] == ["Game 7", "Celtics"]
        assert result["news_page_token"] == "abc123"
        assert result["id"].startswith("nba-finals-")
        assert result["started_at"] is not None
        assert isinstance(result["hours_trending"], float)

    def test_missing_fields(self):
        """Should never throw on missing fields."""
        result = _normalize_trend({})
        assert result["query"] == ""
        assert result["search_volume"] is None
        assert result["active"] is False
        assert result["categories"] == []
        assert result["trend_breakdown"] == []
        assert result["started_at"] is None
        assert result["hours_trending"] is None

    def test_empty_categories(self):
        result = _normalize_trend({"query": "test", "categories": []})
        assert result["categories"] == []

    def test_malformed_categories(self):
        result = _normalize_trend({"query": "test", "categories": [{"bad": "data"}, "string"]})
        assert result["categories"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# _normalize_reddit_trend (Reddit post → Pulse schema)
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalizeRedditTrend:
    def test_basic(self):
        post = {
            "id": "abc123",
            "title": "Breaking: Something happened",
            "subreddit": "news",
            "score": 5000,
            "created_utc": int(time.time()) - 7200,
            "flair": "Politics",
            "permalink": "/r/news/comments/abc123/breaking/",
        }
        result = _normalize_reddit_trend(post, velocity_norm=0.85)

        assert result["query"] == "Breaking: Something happened"
        assert result["source"] == "reddit"
        assert result["subreddit"] == "r/news"
        assert result["reddit_upvotes"] == 5000
        assert result["velocity"] == 0.85
        assert result["active"] is True
        assert result["search_volume"] is None  # Reddit has no search volume
        assert result["categories"] == ["News"]
        assert "r/news" in result["trend_breakdown"]
        assert "Politics" in result["trend_breakdown"]
        assert result["id"].startswith("reddit-news-")

    def test_missing_fields(self):
        result = _normalize_reddit_trend({}, velocity_norm=0.0)
        assert result["query"] == ""
        assert result["source"] == "reddit"
        assert result["velocity"] == 0.0
        assert result["reddit_upvotes"] == 0

    def test_no_flair(self):
        post = {"subreddit": "nba", "id": "x"}
        result = _normalize_reddit_trend(post, velocity_norm=0.5)
        assert len(result["trend_breakdown"]) == 1
        assert result["trend_breakdown"][0] == "r/nba"


# ═══════════════════════════════════════════════════════════════════════════════
# _velocity_score (Reddit ranking formula)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVelocityScore:
    def test_fresh_high_engagement(self):
        """A 1-hour-old post with 500 comments should score very high."""
        post = {
            "created_utc": time.time() - 3600,  # 1 hour ago
            "num_comments": 500,
            "upvote_ratio": 0.95,
        }
        score = _velocity_score(post)
        assert score > 100  # Very high velocity

    def test_old_post_decays(self):
        """A 72h-old post should score lower than the same post at 1h."""
        base = {"num_comments": 500, "upvote_ratio": 0.95}
        fresh = {**base, "created_utc": time.time() - 3600}      # 1h
        old = {**base, "created_utc": time.time() - 72 * 3600}   # 72h

        assert _velocity_score(fresh) > _velocity_score(old)

    def test_age_decay_soft(self):
        """Decay is sqrt, not linear — a 96h post still gets some score."""
        post = {
            "created_utc": time.time() - 96 * 3600,
            "num_comments": 1000,
            "upvote_ratio": 0.90,
        }
        assert _velocity_score(post) > 0

    def test_no_age_decay_under_48h(self):
        """Posts under 48h get age_factor = 1.0 (no decay)."""
        base = {"num_comments": 200, "upvote_ratio": 0.90}
        h24 = {**base, "created_utc": time.time() - 24 * 3600}
        h47 = {**base, "created_utc": time.time() - 47 * 3600}

        # Both under 48h — score should differ only by comment velocity (inversely proportional to hours)
        s24 = _velocity_score(h24)
        s47 = _velocity_score(h47)
        # 24h post has ~2x velocity of 47h post (same comments, fewer hours)
        assert s24 > s47
        assert s24 / s47 < 2.5  # Roughly proportional to hours ratio

    def test_zero_comments(self):
        post = {
            "created_utc": time.time() - 3600,
            "num_comments": 0,
            "upvote_ratio": 0.95,
        }
        assert _velocity_score(post) == 0.0

    def test_low_upvote_ratio_penalizes(self):
        base = {"created_utc": time.time() - 3600, "num_comments": 200}
        high_ratio = {**base, "upvote_ratio": 0.95}
        low_ratio = {**base, "upvote_ratio": 0.50}

        assert _velocity_score(high_ratio) > _velocity_score(low_ratio)

    def test_missing_fields_defaults(self):
        """Should handle missing fields gracefully."""
        post = {"created_utc": 0}  # ancient post, missing comments/ratio
        score = _velocity_score(post)
        assert isinstance(score, float)


# ═══════════════════════════════════════════════════════════════════════════════
# _cache_get / _cache_set (in-memory TTL cache)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCache:
    def test_set_and_get(self):
        store = {}
        _cache_set(store, "key1", {"data": "hello"}, ttl=60)
        result = _cache_get(store, "key1")
        assert result == {"data": "hello"}

    def test_expired(self):
        store = {}
        _cache_set(store, "key1", {"data": "hello"}, ttl=0)
        # TTL=0 means it expired immediately
        time.sleep(0.01)
        result = _cache_get(store, "key1")
        assert result is None

    def test_missing_key(self):
        store = {}
        assert _cache_get(store, "nonexistent") is None

    def test_overwrite(self):
        store = {}
        _cache_set(store, "key1", "old", ttl=60)
        _cache_set(store, "key1", "new", ttl=60)
        assert _cache_get(store, "key1") == "new"
