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

from collectors.twitter_search import TwitterTrendsCollector
from collectors.google_trends_rss import GoogleTrendsRSS
from collectors.reddit_collector import fetch_multiple_subreddits
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
_twitter_search_cache = {}
REDDIT_CACHE_TTL  = 1800   # 30 min
TWITTER_CACHE_TTL = 3600   # 1 hr

def _cache_get(store, key):
    entry = store.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["data"]
    if entry:
        del store[key]
    return None

def _cache_set(store, key, data, ttl):
    store[key] = {"data": data, "expires": time.time() + ttl}


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


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})
