#!/usr/bin/env python3
"""
makethiscontent.com - Trends API
Vercel-compatible Flask backend
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import time
import hashlib
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from collectors.twitter_search import TwitterTrendsCollector
from collectors.google_trends_rss import GoogleTrendsRSS
from collectors.reddit_collector import fetch_multiple_subreddits, _velocity_score
import collectors.google_trends_serpapi as google_trends_serpapi

app = Flask(__name__)
CORS(app)

# ── CREDENTIALS (from Vercel env vars) ───────────────────────────────────────
def get_twitter_bearer_token():
    return os.environ.get("TWITTER_BEARER_TOKEN", "")

def get_serpapi_key():
    return os.environ.get("SERPAPI_KEY", "")

# Initialise collectors
_twitter_collector = None
def get_twitter_collector():
    global _twitter_collector
    token = get_twitter_bearer_token()
    if token and (_twitter_collector is None or _twitter_collector.bearer_token != token):
        _twitter_collector = TwitterTrendsCollector(token)
    return _twitter_collector

google_rss_collector = GoogleTrendsRSS()

# ── IN-MEMORY CACHES ─────────────────────────────────────────────────────────
_reddit_cache  = {}
_reddit_comments_cache = {}
_twitter_search_cache = {}
_pulse_trends_cache = {}
REDDIT_CACHE_TTL          = 1800   # 30 min
REDDIT_COMMENTS_CACHE_TTL = 3600   # 1 hr
TWITTER_CACHE_TTL         = 3600   # 1 hr
PULSE_TRENDS_CACHE_TTL    = 3600   # 1 hr — Pulse Chunk 1 raw trends

def _cache_get(store, key):
    entry = store.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["data"]
    if entry:
        del store[key]
    return None

def _cache_set(store, key, data, ttl):
    store[key] = {"data": data, "expires": time.time() + ttl}


# ── PULSE HELPERS ────────────────────────────────────────────────────────────
import re
import unicodedata

def _slugify(text, max_len=60):
    """ASCII-safe lowercase slug. Handles unicode (e.g. 'alavés' → 'alaves')."""
    if not text:
        return "trend"
    nkfd = unicodedata.normalize("NFKD", str(text))
    ascii_text = nkfd.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (slug or "trend")[:max_len]

def _normalize_trend(raw):
    """Map a raw SerpAPI trending_search dict to the Pulse trend schema.
    Defensive: never throw on missing fields, return null instead."""
    query = raw.get("query", "") or ""
    start_ts = raw.get("start_timestamp")  # Unix seconds
    started_at_iso = None
    hours_trending = None
    if isinstance(start_ts, (int, float)) and start_ts > 0:
        try:
            started_at_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()
            hours_trending = round((time.time() - start_ts) / 3600.0, 2)
        except (ValueError, OSError, OverflowError):
            pass

    # SerpAPI returns categories as list of {id, name}; flatten to names
    raw_cats = raw.get("categories") or []
    categories = [c.get("name") for c in raw_cats if isinstance(c, dict) and c.get("name")]

    return {
        "id": f"{_slugify(query)}-{start_ts or 0}",
        "query": query,
        "search_volume": raw.get("search_volume"),
        "velocity_pct": raw.get("increase_percentage"),
        "active": bool(raw.get("active", False)),
        "started_at": started_at_iso,
        "hours_trending": hours_trending,
        "categories": categories,
        "trend_breakdown": raw.get("trend_breakdown") or [],
        "news_page_token": raw.get("news_page_token"),
    }


# ── TWITTER SEARCH HELPER ────────────────────────────────────────────────────
def _fetch_tweets_from_api(query, max_results=15):
    bearer_token = get_twitter_bearer_token()
    if not bearer_token:
        return {"error": "TWITTER_BEARER_TOKEN env var not set"}

    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": f"{query} -is:retweet lang:en",
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,public_metrics,author_id,text",
        "expansions": "author_id",
        "user.fields": "name,username",
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, "status_code", 0)
        try:
            detail = json.dumps(e.response.json())
        except Exception:
            detail = e.response.text if e.response else str(e)
        msg = "Rate limited — try again in 15 minutes" if status == 429 else f"Twitter API error ({status}): {detail}"
        return {"error": msg}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {e}"}

    raw = resp.json()
    tweets_data = raw.get("data", [])
    users_map = {u["id"]: u for u in raw.get("includes", {}).get("users", [])}

    if not tweets_data:
        return {"tweets": [], "count": 0}

    tweets = []
    for t in tweets_data:
        m = t.get("public_metrics", {})
        likes, rts, replies = m.get("like_count", 0), m.get("retweet_count", 0), m.get("reply_count", 0)
        author = users_map.get(t.get("author_id", ""), {})
        username = author.get("username", "unknown")
        tweets.append({
            "id": t["id"],
            "text": t["text"],
            "author": username,
            "author_name": author.get("name", username),
            "created_at": t.get("created_at", ""),
            "likes": likes,
            "retweets": rts,
            "replies": replies,
            "engagement_score": likes + rts + replies,
            "url": f"https://twitter.com/{username}/status/{t['id']}",
        })

    tweets.sort(key=lambda x: x["engagement_score"], reverse=True)
    return {"tweets": tweets, "count": len(tweets)}


# ── ROUTES ───────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({
        "service": "makethiscontent.com Trends API",
        "status": "online",
        "version": "1.0.0",
        "runtime": "vercel",
        "endpoints": {
            "/trends/google": "Google Trends via SerpAPI",
            "/trends/google/rss": "Google Trends RSS (free fallback)",
            "/pulse/trends/raw": "Pulse normalized active trends (?geo=US&limit=30)",
            "/pulse/trends/reddit": "Pulse Reddit discovery trends (?subreddits=news,nba&limit=30)",
            "/pulse/enrich": "Cross-platform enrichment for a trend (?query=topic&limit=5)",
            "/trends/twitter/search": "Tweet search for a query (?query=X&limit=10)",
            "/trends/reddit": "Hot Reddit posts (?subreddits=X,Y&limit=20)",
            "/health": "Health check",
        }
    })


@app.route("/trends/google")
def get_google_trends():
    limit = request.args.get("limit", type=int)
    geo   = request.args.get("geo", "US")
    result = google_trends_serpapi.get_trending_searches(
        geo=geo, limit=limit, api_key=get_serpapi_key()
    )
    if result["success"]:
        return jsonify(result)
    return jsonify({"error": "Failed to fetch Google trends", "details": result}), 500


@app.route("/trends/google/rss")
def get_google_trends_rss():
    geo   = request.args.get("geo", "US")
    limit = request.args.get("limit", type=int, default=20)
    result = google_rss_collector.fetch_trends(geo=geo)
    if result["success"]:
        result["trends"] = result["trends"][:limit]
        return jsonify(result)
    return jsonify({"error": "Failed to fetch RSS trends", "details": result}), 500


@app.route("/pulse/trends/raw")
def get_pulse_trends_raw():
    """
    Pulse Chunk 1 — normalized raw trends from Google Trends.

    Steps:
      1. Call get_trending_searches (existing SerpAPI Trending Now)
      2. Filter to active == true
      3. Sort by search_volume descending
      4. Take top N (default 30, max 50)
      5. Normalize each into Pulse trend schema
      6. Cache in-memory per geo, 1h TTL
    """
    geo   = request.args.get("geo", "US").upper()
    limit = request.args.get("limit", type=int, default=30)
    limit = max(1, min(limit, 50))  # clamp to [1,50]

    cache_key = f"{geo}:{limit}"
    cached = _cache_get(_pulse_trends_cache, cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # Fetch raw trends from SerpAPI (existing collector)
    raw = google_trends_serpapi.get_trending_searches(
        geo=geo, limit=None, api_key=get_serpapi_key()
    )
    if not raw.get("success"):
        return jsonify({
            "success": False,
            "error": "Failed to fetch trends from SerpAPI",
            "details": raw,
        }), 502

    all_trends = raw.get("trends", []) or []
    active_trends = [t for t in all_trends if t.get("active")]

    # Sort by search_volume descending (treat missing volume as 0)
    active_trends.sort(key=lambda t: t.get("search_volume") or 0, reverse=True)

    top = active_trends[:limit]
    normalized = [_normalize_trend(t) for t in top]

    response = {
        "success": True,
        "source": "serpapi_trending_now",
        "geo": geo,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(normalized),
        "total_active_available": len(active_trends),
        "total_raw_available": len(all_trends),
        "trends": normalized,
        "cached": False,
    }
    _cache_set(_pulse_trends_cache, cache_key, response, PULSE_TRENDS_CACHE_TTL)
    return jsonify(response)


# ── PULSE REDDIT DISCOVERY (Chunk 2) ─────────────────────────────────────────
_pulse_reddit_cache = {}
PULSE_REDDIT_CACHE_TTL = 1800  # 30 min — matches the dashboard Reddit cache
PULSE_MAX_PER_SUBREDDIT = 3    # surface only the N hottest topics per subreddit

# Fallback cultural subreddit set — mirrors the profiles.pulse_subreddits column
# default. Used ONLY when the client sends no list (e.g. isolated testing).
# Source of truth for real users is their profiles row.
PULSE_DEFAULT_SUBREDDITS = [
    "news", "worldnews", "politics",
    "popculturechat", "entertainment", "television", "Music", "movies",
    "sports", "nba", "nfl",
    "technology", "business", "OutOfTheLoop",
]

# Subreddit → Pulse category. Pure display logic so Reddit cards work with the
# existing (Google-derived) category filter. Keys matched lowercase.
SUBREDDIT_CATEGORY_MAP = {
    "news": "News", "worldnews": "News", "politics": "News",
    "popculturechat": "Entertainment", "entertainment": "Entertainment",
    "television": "Entertainment", "music": "Entertainment", "movies": "Entertainment",
    "sports": "Sports", "nba": "Sports", "nfl": "Sports",
    "technology": "Business", "business": "Business", "outoftheloop": "Business",
}


def _subreddit_category(subreddit):
    return SUBREDDIT_CATEGORY_MAP.get((subreddit or "").lower(), "Trending")


def _normalize_reddit_trend(post, velocity_norm):
    """Map a Reddit post (collector shape) to the Pulse trend schema.

    Option (b): Reddit has no search_volume, so that stays null; the upvote
    count rides in its own descriptive field (reddit_upvotes). source,
    velocity, subreddit and permalink are additive — Google trends won't
    carry them, and the frontend branches on `source` for display.
    """
    subreddit = post.get("subreddit", "")
    post_id = post.get("id", "")
    created = post.get("created_utc", 0)

    started_at_iso = None
    hours_trending = None
    if isinstance(created, (int, float)) and created > 0:
        try:
            started_at_iso = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            hours_trending = round((time.time() - created) / 3600.0, 2)
        except (ValueError, OSError, OverflowError):
            pass

    # trend_breakdown = free context chips on the card: subreddit + flair
    breakdown = []
    if subreddit:
        breakdown.append(f"r/{subreddit}")
    flair = post.get("flair") or ""
    if flair:
        breakdown.append(flair)

    return {
        "id": f"reddit-{_slugify(subreddit)}-{post_id}",
        "query": post.get("title", ""),
        "search_volume": None,           # option (b): Reddit has no search volume
        "velocity_pct": None,            # Reddit has no % increase metric
        "active": True,                  # live hot posts are always active
        "started_at": started_at_iso,
        "hours_trending": hours_trending,
        "categories": [_subreddit_category(subreddit)],
        "trend_breakdown": breakdown,
        "news_page_token": None,
        # ── Reddit-specific additive fields ──
        "source": "reddit",
        "reddit_upvotes": post.get("score", 0),
        "subreddit": f"r/{subreddit}" if subreddit else "",
        "velocity": velocity_norm,       # normalised 0–1 within this source
        "permalink": post.get("permalink", ""),
    }


@app.route("/pulse/trends/reddit")
def get_pulse_trends_reddit():
    """
    Pulse Chunk 2 — Reddit as an independent discovery source.

    Pulls hot posts from the given (or default) subreddit set in parallel,
    velocity-ranks them with the dashboard formula, normalises each to the
    Pulse trend schema with source="reddit".

    Query params:
      subreddits  comma-separated bare names (e.g. news,nba). Falls back to
                  PULSE_DEFAULT_SUBREDDITS when omitted.
      limit       max cards returned (default 30, max 50)
    """
    subs_param = request.args.get("subreddits", "").strip()
    if subs_param:
        subreddits = [s.strip() for s in subs_param.split(",") if s.strip()]
    else:
        subreddits = list(PULSE_DEFAULT_SUBREDDITS)

    subreddits = subreddits[:20]  # fan-out guardrail
    if not subreddits:
        return jsonify({"success": False, "error": "no subreddits to fetch"}), 400

    limit = request.args.get("limit", type=int, default=30)
    limit = max(1, min(limit, 50))

    cache_key = ",".join(sorted(subreddits)) + f":limit={limit}"
    cached = _cache_get(_pulse_reddit_cache, cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    # Parallel fetch across all subreddits (reuses the collector + ThreadPool)
    result = fetch_multiple_subreddits(subreddits, limit_per_sub=25)
    posts = result.get("posts", []) if result.get("success") else []

    # Score with the dashboard velocity formula, normalise to 0–1 within source
    scored = [(p, _velocity_score(p)) for p in posts]
    max_score = max((s for _, s in scored), default=0) or 1.0
    scored.sort(key=lambda ps: ps[1], reverse=True)

    # Cap at the N hottest posts per subreddit so one busy community (e.g. an
    # NBA playoff night) can't monopolise the feed. The list is already sorted
    # by velocity desc, so the first N seen per subreddit ARE its hottest N.
    # max_score is taken pre-cap, so the hottest post stays 1.0 regardless.
    per_sub_count = {}
    capped = []
    for p, s in scored:
        sub = (p.get("subreddit") or "").lower()
        if per_sub_count.get(sub, 0) >= PULSE_MAX_PER_SUBREDDIT:
            continue
        per_sub_count[sub] = per_sub_count.get(sub, 0) + 1
        capped.append((p, s))

    normalized = [
        _normalize_reddit_trend(p, round(s / max_score, 4))
        for p, s in capped[:limit]
    ]

    response = {
        "success": True,
        "source": "reddit",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(normalized),
        "subreddits_requested": subreddits,
        "subreddits_fetched": result.get("subreddits_fetched", []),
        "subreddits_failed": result.get("subreddits_failed", []),
        "trends": normalized,
        "cached": False,
    }
    _cache_set(_pulse_reddit_cache, cache_key, response, PULSE_REDDIT_CACHE_TTL)
    return jsonify(response)


# ── PULSE ENRICHMENT (Chunk F1) ──────────────────────────────────────────────
_pulse_enrich_cache = {}
PULSE_ENRICH_CACHE_TTL = 1800  # 30 min


def _fetch_tiktok_top_search(query, api_key, max_items=5):
    """TikTok Top Search — returns videos + carousels matching the query."""
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/tiktok/search/top",
            headers={"x-api-key": api_key},
            params={"query": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        items = data.get("items", [])[:max_items]
        results = []
        for item in items:
            stats = item.get("statistics", {})
            author = item.get("author", {}) or {}
            caption = item.get("desc", "")[:200]
            results.append({
                "title": caption or "(no caption)",
                "author": author.get("nickname", author.get("unique_id", "")),
                "author_handle": author.get("unique_id", ""),
                "plays": stats.get("play_count", 0),
                "likes": stats.get("digg_count", 0),
                "comments": stats.get("comment_count", 0),
                "shares": stats.get("share_count", 0),
                "url": f"https://www.tiktok.com/@{author.get('unique_id', '')}/video/{item.get('aweme_id', item.get('id', ''))}",
            })
        return results
    except Exception as e:
        print(f"[enrich] TikTok error: {e}")
        return []


def _fetch_youtube_search(query, api_key, max_items=5):
    """YouTube Search — returns videos matching the query."""
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/youtube/search",
            headers={"x-api-key": api_key},
            params={"query": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        videos = data.get("videos", [])[:max_items]
        results = []
        for v in videos:
            ch = v.get("channel", {}) or {}
            results.append({
                "title": v.get("title", "")[:200],
                "author": ch.get("title", ""),
                "author_handle": ch.get("handle", ""),
                "views": v.get("viewCountInt", v.get("viewCount", 0)),
                "likes": v.get("likeCountInt", 0),
                "comments": v.get("commentCountInt", 0),
                "url": v.get("url", f"https://www.youtube.com/watch?v={v.get('id', '')}"),
            })
        # Also include shorts if present
        shorts = data.get("shorts", [])[:2]
        for s in shorts:
            ch = s.get("channel", {}) or {}
            results.append({
                "title": s.get("title", "")[:200],
                "author": ch.get("title", ""),
                "author_handle": ch.get("handle", ""),
                "views": s.get("viewCountInt", 0),
                "likes": s.get("likeCountInt", 0),
                "comments": s.get("commentCountInt", 0),
                "url": s.get("url", ""),
                "is_short": True,
            })
        return results[:max_items]
    except Exception as e:
        print(f"[enrich] YouTube error: {e}")
        return []


def _fetch_instagram_reels(query, api_key, max_items=5):
    """Instagram Search Reels — returns reels matching the query."""
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v2/instagram/reels/search",
            headers={"x-api-key": api_key},
            params={"query": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        reels = data.get("reels", data.get("items", []))[:max_items]
        results = []
        for r in reels:
            user = r.get("user", r.get("owner", {})) or {}
            caption = r.get("caption", {})
            text = (caption.get("text", "") if isinstance(caption, dict) else str(caption or ""))[:200]
            results.append({
                "title": text or "(no caption)",
                "author": user.get("full_name", user.get("username", "")),
                "author_handle": user.get("username", ""),
                "plays": r.get("play_count", 0) or 0,
                "likes": r.get("like_count", 0) or 0,
                "comments": r.get("comment_count", 0) or 0,
                "url": r.get("url", f"https://www.instagram.com/reel/{r.get('shortcode', r.get('code', ''))}"),
            })
        return results
    except Exception as e:
        print(f"[enrich] Instagram error: {e}")
        return []


def _fetch_linkedin_posts(query, api_key, max_items=5):
    """LinkedIn Search Posts — returns professional posts matching the query."""
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/linkedin/search/posts",
            headers={"x-api-key": api_key},
            params={"query": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        posts = data.get("posts", [])[:max_items]
        results = []
        for p in posts:
            author = p.get("author", {}) or {}
            desc = p.get("description", p.get("name", ""))[:200]
            results.append({
                "title": desc or "(no text)",
                "author": author.get("name", ""),
                "author_url": author.get("url", ""),
                "followers": author.get("followers", 0),
                "likes": p.get("likeCount", 0) or 0,
                "comments": p.get("commentCount", 0) or 0,
                "url": p.get("url", ""),
            })
        return results
    except Exception as e:
        print(f"[enrich] LinkedIn error: {e}")
        return []


@app.route("/pulse/enrich")
def get_pulse_enrichment():
    """
    Pulse Chunk F1 — Cross-platform enrichment for a trend topic.

    Fires TikTok, YouTube, Instagram, and LinkedIn search queries in parallel
    and returns normalised results grouped by platform.

    Query params:
      query   The trend title to search for (required)
      limit   Max items per platform (default 5, max 8)
    """
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"success": False, "error": "query parameter required"}), 400

    limit = request.args.get("limit", type=int, default=5)
    limit = max(1, min(limit, 8))

    # Optional LLM-extracted search phrase for YouTube. YouTube's search
    # tokenizer struggles with long headline-style queries from Reddit; the
    # frontend bridge route extracts a short 3-5 word phrase via Sonnet and
    # passes it here. Falls back to the main query if not provided.
    youtube_query = (request.args.get("youtube_query") or "").strip() or query

    # Cache key includes the youtube_query so we don't serve YT-mismatched
    # results when the same query has a different YT phrase.
    cache_key = f"{query.lower().strip()}|yt={youtube_query.lower()}:limit={limit}"
    cached = _cache_get(_pulse_enrich_cache, cache_key)
    if cached:
        return jsonify({**cached, "cached": True})

    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    if not api_key:
        return jsonify({"success": False, "error": "SCRAPECREATORS_API_KEY not set"}), 500

    # Fire all 4 platform searches in parallel. YouTube gets the focused
    # phrase; the rest use the original query (their searches handle long
    # headline-style queries fine).
    platform_fetchers = {
        "tiktok": (_fetch_tiktok_top_search, query, api_key, limit),
        "youtube": (_fetch_youtube_search, youtube_query, api_key, limit),
        "instagram": (_fetch_instagram_reels, query, api_key, limit),
        "linkedin": (_fetch_linkedin_posts, query, api_key, limit),
    }

    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(fn, *args): platform
            for platform, (fn, *args) in platform_fetchers.items()
        }
        for future in as_completed(futures):
            platform = futures[future]
            try:
                results[platform] = future.result(timeout=12)
            except Exception as e:
                print(f"[enrich] {platform} failed: {e}")
                results[platform] = []

    response = {
        "success": True,
        "query": query,
        "platforms": {
            "tiktok": {"name": "TikTok", "items": results.get("tiktok", [])},
            "youtube": {"name": "YouTube", "items": results.get("youtube", [])},
            "instagram": {"name": "Instagram", "items": results.get("instagram", [])},
            "linkedin": {"name": "LinkedIn", "items": results.get("linkedin", [])},
        },
        "cached": False,
    }
    _cache_set(_pulse_enrich_cache, cache_key, response, PULSE_ENRICH_CACHE_TTL)
    return jsonify(response)


@app.route("/trends/twitter/search")
def get_twitter_for_trend():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"success": False, "error": "query parameter required"}), 400

    limit = min(request.args.get("limit", type=int, default=10), 20)
    cache_key = hashlib.md5(query.lower().encode()).hexdigest()

    cached = _cache_get(_twitter_search_cache, cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    result = _fetch_tweets_from_api(query, max_results=limit + 5)
    if "error" in result:
        return jsonify({"success": False, "query": query, "error": result["error"]}), 503

    result["tweets"] = result["tweets"][:limit]
    result["count"]  = len(result["tweets"])

    response = {
        "success": True,
        "query": query,
        "count": result["count"],
        "tweets": result["tweets"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    _cache_set(_twitter_search_cache, cache_key, response, TWITTER_CACHE_TTL)
    return jsonify(response)


@app.route("/trends/reddit")
def get_reddit_trends():
    subs_param = request.args.get("subreddits", "").strip()
    if not subs_param:
        return jsonify({"success": False, "error": "subreddits parameter required"}), 400

    subreddits = [s.strip() for s in subs_param.split(",") if s.strip()]
    if len(subreddits) > 10:
        return jsonify({"success": False, "error": "Max 10 subreddits per request"}), 400

    limit     = min(request.args.get("limit", type=int, default=20), 50)
    fresh     = request.args.get("fresh", "false").lower() == "true"
    cache_key = ",".join(sorted(subreddits)) + f":limit={limit}"

    if not fresh:
        cached = _cache_get(_reddit_cache, cache_key)
        if cached:
            cached["cached"] = True
            return jsonify(cached)

    result = fetch_multiple_subreddits(subreddits, limit_per_sub=limit)
    if result["success"]:
        _cache_set(_reddit_cache, cache_key, result, REDDIT_CACHE_TTL)
        result["cached"] = False
        return jsonify(result)
    return jsonify({"success": False, "error": "Failed to fetch Reddit data"}), 500


@app.route("/trends/reddit/comments")
def get_reddit_comments():
    post_url = request.args.get("url", "").strip()
    if not post_url:
        return jsonify({"success": False, "error": "url parameter required"}), 400

    amount = min(request.args.get("amount", type=int, default=15), 25)
    cache_key = hashlib.md5(post_url.encode()).hexdigest()

    cached = _cache_get(_reddit_comments_cache, cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    if not api_key:
        return jsonify({"success": False, "error": "SCRAPECREATORS_API_KEY not set"}), 500

    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/reddit/post/comments",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            params={"url": post_url, "trim": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"ScrapeCreators error: {e}"}), 502
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # Extract post body (selftext) from the post object — use as preview on the card
    post_obj = data.get("post", {})
    post_body = (post_obj.get("selftext") or "").strip()
    # Clean up common Reddit artifacts
    if post_body in ("[deleted]", "[removed]", ""):
        post_body = ""

    # Handle different response structures from ScrapeCreators
    raw_comments = (
        data.get("comments") or
        data.get("data", {}).get("comments") or
        []
    )

    if not raw_comments:
        print(f"[Comments] Empty. Keys: {list(data.keys())} | Sample: {str(data)[:400]}")

    # Recursively flatten the full comment tree including nested replies
    def flatten_comments(clist, results):
        for c in clist:
            body = (c.get("body") or c.get("text") or c.get("selftext") or "").strip()
            if body and body not in ("[deleted]", "[removed]"):
                results.append({
                    "id": c.get("id", ""),
                    "body": body,
                    "score": c.get("score", c.get("ups", 0)),
                    "author": c.get("author", ""),
                    "created_utc": c.get("created_utc", 0),
                    "depth": c.get("depth", 0),
                    "parent_id": c.get("parent_id", ""),
                    "controversiality": c.get("controversiality", 0),
                })
            # Traverse replies.items recursively
            replies = c.get("replies", {})
            if isinstance(replies, dict):
                nested = replies.get("items", [])
                if isinstance(nested, list) and nested:
                    flatten_comments(nested, results)

    comments = []
    flatten_comments(raw_comments, comments)

    # Sort by score descending — highest community resonance first
    comments.sort(key=lambda x: x["score"], reverse=True)

    result = {
        "success": True,
        "post_url": post_url,
        "post_body": post_body,
        "comments": comments,
        "count": len(comments),
        "cached": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache_set(_reddit_comments_cache, cache_key, result, REDDIT_COMMENTS_CACHE_TTL)
    return jsonify(result)


@app.route("/debug/comments")
def debug_comments():
    """Inspect raw ScrapeCreators comment structure"""
    post_url = request.args.get("url", "https://www.reddit.com/r/productivity/comments/1rf6iqj/how_can_you_escape_the_hell_that_is_brain_fog")
    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    if not api_key:
        return jsonify({"error": "no api key"})
    try:
        resp = requests.get(
            "https://api.scrapecreators.com/v1/reddit/post/comments",
            headers={"x-api-key": api_key},
            params={"url": post_url, "trim": "false"},
            timeout=15,
        )
        data = resp.json()
        comments = data.get("comments", [])
        more = data.get("more", None)

        # Inspect the first comment in full detail
        first_comment = comments[0] if comments else {}
        first_comment_keys = list(first_comment.keys()) if first_comment else []
        has_replies = "replies" in first_comment
        replies_sample = first_comment.get("replies", [])
        replies_count_first = len(replies_sample) if isinstance(replies_sample, list) else "not a list"

        # Count total comments including nested replies recursively
        def count_all(clist):
            total = 0
            for c in clist:
                total += 1
                replies = c.get("replies", [])
                if isinstance(replies, list):
                    total += count_all(replies)
            return total

        total_including_replies = count_all(comments)

        # Sample of top 3 comments: just id, score, body snippet, reply count
        sample = []
        for c in comments[:3]:
            replies = c.get("replies", [])
            sample.append({
                "score": c.get("score", c.get("ups", "?")),
                "body_snippet": (c.get("body") or c.get("text") or "")[:120],
                "reply_count": len(replies) if isinstance(replies, list) else 0,
                "replies_is": type(replies).__name__,
            })

        # Find first comment that has non-empty replies.items to inspect
        replies_structure_sample = None
        def count_tree(clist):
            total = 0
            for c in clist:
                total += 1
                r = c.get("replies", {})
                items = r.get("items", []) if isinstance(r, dict) else []
                if isinstance(items, list) and items:
                    total += count_tree(items)
            return total

        total_tree_count = count_tree(comments)

        for c in comments:
            r = c.get("replies", {})
            if not isinstance(r, dict):
                continue
            items = r.get("items", [])
            if not isinstance(items, list) or not items:
                continue
            # Found a comment with actual reply items
            first_reply = items[0]
            first_reply_keys = list(first_reply.keys()) if isinstance(first_reply, dict) else []
            # Check if the reply itself has replies.items
            nested_replies = first_reply.get("replies", {}) if isinstance(first_reply, dict) else {}
            nested_items = nested_replies.get("items", []) if isinstance(nested_replies, dict) else []
            replies_structure_sample = {
                "parent_score": c.get("score"),
                "parent_body_snippet": (c.get("body") or "")[:80],
                "items_count_in_replies": len(items),
                "first_reply_keys": first_reply_keys,
                "first_reply_body": (first_reply.get("body") or "")[:120] if isinstance(first_reply, dict) else None,
                "first_reply_score": first_reply.get("score") if isinstance(first_reply, dict) else None,
                "first_reply_depth": first_reply.get("depth") if isinstance(first_reply, dict) else None,
                "first_reply_created_utc": first_reply.get("created_utc") if isinstance(first_reply, dict) else None,
                "first_reply_controversiality": first_reply.get("controversiality") if isinstance(first_reply, dict) else None,
                "first_reply_has_nested_replies_items": isinstance(nested_items, list) and len(nested_items) > 0,
                "first_reply_nested_replies_count": len(nested_items) if isinstance(nested_items, list) else 0,
            }
            break

        # Reply distribution: how many top-level comments have 0, 1-3, 4-10, 10+ replies
        distribution = {"0": 0, "1-3": 0, "4-10": 0, "10+": 0}
        chain_lengths = []
        for c in comments:
            r = c.get("replies", {})
            items = r.get("items", []) if isinstance(r, dict) else []
            n = len(items) if isinstance(items, list) else 0
            chain_lengths.append({"score": c.get("score"), "reply_count": n, "body": (c.get("body") or "")[:60]})
            if n == 0: distribution["0"] += 1
            elif n <= 3: distribution["1-3"] += 1
            elif n <= 10: distribution["4-10"] += 1
            else: distribution["10+"] += 1

        # Sort by reply count descending to surface most active chains
        chain_lengths.sort(key=lambda x: x["reply_count"], reverse=True)

        return jsonify({
            "http_status": resp.status_code,
            "top_level_comment_count": len(comments),
            "total_tree_count_all_depths": total_tree_count,
            "first_comment_has_replies_field": has_replies,
            "reply_distribution": distribution,
            "most_active_chains_top10": chain_lengths[:10],
            "replies_structure_of_first_non_empty": replies_structure_sample,
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})


@app.route("/debug/reddit")
def debug_reddit():
    import os
    import requests as req
    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    result = {
        "api_key_set": bool(api_key),
        "api_key_prefix": api_key[:6] + "..." if api_key else "MISSING"
    }
    if api_key:
        try:
            resp = req.get(
                "https://api.scrapecreators.com/v1/reddit/subreddit",
                headers={"x-api-key": api_key},
                params={"subreddit": "ChatGPT", "sort": "hot", "trim": "true"},
                timeout=15
            )
            data = resp.json()
            result["http_status"] = resp.status_code
            result["response_keys"] = list(data.keys())
            result["post_count"] = len(data.get("posts", []))
        except Exception as e:
            result["error"] = str(e)
    return jsonify(result)


@app.route("/debug/reddit2")
def debug_reddit2():
    """Step-by-step diagnostic calling the actual collector code"""
    import os
    import requests as req

    steps = []

    # Step 1: env var
    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    steps.append({"step": "1_env_var", "ok": bool(api_key), "value": api_key[:6] + "..." if api_key else "MISSING"})

    # Step 2: raw API call identical to collector
    if api_key:
        try:
            resp = req.get(
                "https://api.scrapecreators.com/v1/reddit/subreddit",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                params={"subreddit": "ChatGPT", "sort": "hot", "timeframe": "day", "trim": "true"},
                timeout=15
            )
            data = resp.json()
            raw_posts = data.get("posts", [])
            steps.append({"step": "2_api_call", "ok": True, "status": resp.status_code, "post_count": len(raw_posts)})

            # Step 3: filter (score >= 5)
            filtered = [p for p in raw_posts if p.get("score", p.get("ups", 0)) >= 5]
            steps.append({"step": "3_filter_score_gte_5", "ok": True, "count_before": len(raw_posts), "count_after": len(filtered)})

            # Step 4: show first post raw
            if filtered:
                steps.append({"step": "4_first_post_raw", "ok": True, "post": {k: filtered[0].get(k) for k in ["id","title","score","num_comments","url"]}})
            else:
                steps.append({"step": "4_first_post_raw", "ok": False, "reason": "no posts passed filter"})

        except Exception as e:
            steps.append({"step": "2_api_call", "ok": False, "error": str(e)})

    # Step 5: call actual collector function
    try:
        from collectors.reddit_collector import fetch_subreddit_hot
        result = fetch_subreddit_hot("ChatGPT", limit=5)
        steps.append({"step": "5_collector_function", "ok": True, "count": len(result), "first_title": result[0]["title"] if result else None})
    except Exception as e:
        steps.append({"step": "5_collector_function", "ok": False, "error": str(e)})

    return jsonify({"steps": steps})




