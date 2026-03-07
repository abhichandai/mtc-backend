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
_reddit_comments_cache = {}
_twitter_search_cache = {}
REDDIT_CACHE_TTL          = 1800   # 30 min
REDDIT_COMMENTS_CACHE_TTL = 3600   # 1 hr
TWITTER_CACHE_TTL         = 3600   # 1 hr

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

    comments = []
    for c in raw_comments:
        body = (c.get("body") or c.get("text") or c.get("selftext") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue
        comments.append({
            "id": c.get("id", ""),
            "body": body,
            "score": c.get("score", c.get("ups", 0)),
            "author": c.get("author", ""),
            "created_utc": c.get("created_utc", 0),
        })

    # Sort by score so highest-resonance comments come first
    comments.sort(key=lambda x: x["score"], reverse=True)

    result = {
        "success": True,
        "post_url": post_url,
        "post_body": post_body,          # selftext — shown as preview in panel
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

        return jsonify({
            "http_status": resp.status_code,
            "top_level_keys": list(data.keys()),
            "more_key_value": str(more)[:200] if more else None,
            "top_level_comment_count": len(comments),
            "total_including_nested_replies": total_including_replies,
            "first_comment_keys": first_comment_keys,
            "first_comment_has_replies_field": has_replies,
            "replies_count_in_first_comment": replies_count_first,
            "top_3_comments_sample": sample,
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




