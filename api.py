#!/usr/bin/env python3
"""
makethiscontent.com - Trends API
Simple Flask API to serve trending topics
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import json
import os
from datetime import datetime, timezone
import sys
import time
import hashlib
import requests
from collections import OrderedDict

# Add collectors to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'collectors'))
from twitter_search import TwitterTrendsCollector
from google_trends_rss import GoogleTrendsRSS
import google_trends_serpapi

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

# Load Twitter credentials
CREDS_PATH = '/root/clawd/credentials/twitter-api.json'
with open(CREDS_PATH, 'r') as f:
    creds = json.load(f)

twitter_collector = TwitterTrendsCollector(creds['bearer_token'])
google_rss_collector = GoogleTrendsRSS()

# Cache file paths
TWITTER_CACHE = '/root/clawd/mtc-backend/twitter-trends-latest.json'
GOOGLE_CACHE = '/root/clawd/mtc-backend/google-trends-serpapi-latest.json'
GOOGLE_RSS_CACHE = '/root/clawd/mtc-backend/google-trends-rss-latest.json'

# ── TWITTER SEARCH CACHE (for individual trend tweet searches) ────────────────
_twitter_search_cache = {}
TWITTER_CACHE_TTL = 3600  # 1 hour

def _twitter_cache_get(key):
    entry = _twitter_search_cache.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["data"]
    if entry:
        del _twitter_search_cache[key]
    return None

def _twitter_cache_set(key, data):
    _twitter_search_cache[key] = {"data": data, "expires": time.time() + TWITTER_CACHE_TTL}

def _get_bearer_token():
    """Get Twitter bearer token from credentials file"""
    try:
        with open(CREDS_PATH) as f:
            return json.load(f)["bearer_token"]
    except Exception:
        return None

def _fetch_tweets_from_api(query, max_results=15):
    """Fetch tweets from Twitter API for a specific query"""
    bearer_token = _get_bearer_token()
    if not bearer_token:
        return {"error": "Twitter credentials not configured"}
    
    # Build the search query — exclude retweets for cleaner results
    search_query = f"{query} -is:retweet lang:en"
    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": search_query,
        "max_results": min(max_results, 100),  # API max is 100
        "tweet.fields": "created_at,public_metrics,author_id,text",
        "expansions": "author_id",
        "user.fields": "name,username",
    }
    
    try:
        print(f"[DEBUG] Calling Twitter API: {url}")
        print(f"[DEBUG] Params: {params}")
        print(f"[DEBUG] Headers: Authorization Bearer {bearer_token[:20]}...")
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"[DEBUG] Response status: {resp.status_code}")
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        status = getattr(e.response, 'status_code', 0)
        try:
            error_json = e.response.json() if e.response else {}
            error_detail = json.dumps(error_json)
        except:
            error_detail = e.response.text if e.response else str(e)
        msg = "Rate limited — try again in 15 minutes" if status == 429 else f"Twitter API error ({status}): {error_detail}"
        return {"error": msg}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {str(e)}"}
    
    raw = resp.json()
    tweets_data = raw.get("data", [])
    users_data = {u["id"]: u for u in raw.get("includes", {}).get("users", [])}
    
    if not tweets_data:
        return {"tweets": [], "count": 0}
    
    tweets = []
    for t in tweets_data:
        metrics = t.get("public_metrics", {})
        likes = metrics.get("like_count", 0)
        retweets = metrics.get("retweet_count", 0)
        replies = metrics.get("reply_count", 0)
        engagement = likes + retweets + replies
        
        author = users_data.get(t.get("author_id", ""), {})
        username = author.get("username", "unknown")
        author_name = author.get("name", username)
        
        tweets.append({
            "id": t["id"],
            "text": t["text"],
            "author": username,
            "author_name": author_name,
            "created_at": t.get("created_at", ""),
            "likes": likes,
            "retweets": retweets,
            "replies": replies,
            "engagement_score": engagement,
            "url": f"https://twitter.com/{username}/status/{t['id']}",
        })
    
    # Sort by engagement descending
    tweets.sort(key=lambda x: x["engagement_score"], reverse=True)
    
    return {"tweets": tweets, "count": len(tweets)}


@app.route('/')
def home():
    """API status endpoint"""
    return jsonify({
        "service": "makethiscontent.com Trends API",
        "status": "online",
        "version": "0.3.0",
        "endpoints": {
            "/trends": "Get all trends (default: Twitter)",
            "/trends/twitter": "Get Twitter trends (Twitter Search API)",
            "/trends/google": "Get Google Trends (SerpAPI Trending Now - 381 trends)",
            "/trends/google/rss": "Get Google Trends RSS (free fallback)",
            "/trends/refresh": "Force refresh trends (POST)"
        },
        "data_sources": {
            "google_trends": "SerpAPI Trending Now API (250 searches/mo free tier)",
            "twitter": "Twitter Search API",
            "fallback": "Google Trends RSS (free)"
        },
        "cache": {
            "google_trends": "12 hours (preserves API quota)",
            "twitter": "1 hour"
        }
    })


@app.route('/trends', methods=['GET'])
def get_trends():
    """
    Get all trending topics
    Query params:
        - source: twitter (default)
        - limit: number of results (default: 20)
        - fresh: force refresh if true
    """
    source = request.args.get('source', 'twitter')
    limit = int(request.args.get('limit', 20))
    fresh = request.args.get('fresh', 'false').lower() == 'true'
    
    if source == 'twitter':
        return get_twitter_trends(limit, fresh)
    else:
        return jsonify({
            "error": "Invalid source",
            "message": f"Source '{source}' not supported. Try: twitter"
        }), 400


@app.route('/trends/twitter', methods=['GET'])
def get_twitter_trends(limit=None, fresh=False):
    """Get Twitter trending topics"""
    
    if limit is None:
        limit = int(request.args.get('limit', 20))
    
    if fresh is False:
        fresh = request.args.get('fresh', 'false').lower() == 'true'
    
    # Check cache first
    if not fresh and os.path.exists(TWITTER_CACHE):
        # Check if cache is recent (less than 1 hour old)
        cache_age = datetime.now().timestamp() - os.path.getmtime(TWITTER_CACHE)
        
        if cache_age < 3600:  # 1 hour
            with open(TWITTER_CACHE, 'r') as f:
                cached_data = json.load(f)
            
            # Normalize trend format (hashtag -> topic for consistency)
            for trend in cached_data.get('trends', []):
                if 'hashtag' in trend and 'topic' not in trend:
                    trend['topic'] = trend['hashtag']
            
            cached_data['trends'] = cached_data['trends'][:limit]
            cached_data['cached'] = True
            cached_data['cache_age_seconds'] = int(cache_age)
            
            return jsonify(cached_data)
    
    # Fetch fresh data
    result = twitter_collector.find_trending_topics()
    
    if result['success']:
        # Normalize format
        for trend in result.get('trends', []):
            if 'hashtag' in trend and 'topic' not in trend:
                trend['topic'] = trend['hashtag']
        
        # Save to cache
        with open(TWITTER_CACHE, 'w') as f:
            json.dump(result, f, indent=2)
        
        result['trends'] = result['trends'][:limit]
        result['cached'] = False
        
        return jsonify(result)
    else:
        return jsonify({
            "error": "Failed to fetch trends",
            "message": result.get('error', 'Unknown error')
        }), 500


@app.route('/trends/twitter/search', methods=['GET'])
def get_twitter_for_trend():
    """Get tweets for a specific trend query"""
    query = request.args.get('query', '').strip()
    if not query:
        return jsonify({"success": False, "error": "query parameter is required"}), 400
    
    limit = min(int(request.args.get('limit', 10)), 20)
    
    # Cache key based on query
    cache_key = hashlib.md5(query.lower().encode()).hexdigest()
    cached = _twitter_cache_get(cache_key)
    
    if cached:
        cached["cached"] = True
        return jsonify(cached)
    
    # Fetch from API
    result = _fetch_tweets_from_api(query, max_results=limit + 5)
    
    if "error" in result:
        return jsonify({
            "success": False,
            "query": query,
            "error": result["error"],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }), 503
    
    # Trim to requested limit after sorting
    result["tweets"] = result["tweets"][:limit]
    result["count"] = len(result["tweets"])
    
    response = {
        "success": True,
        "query": query,
        "count": result["count"],
        "tweets": result["tweets"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    
    _twitter_cache_set(cache_key, response)
    return jsonify(response)


@app.route('/trends/google', methods=['GET'])
def get_google_trends():
    """Get Google Trends data via SerpAPI Trending Now API"""
    
    limit = int(request.args.get('limit', 20))
    fresh = request.args.get('fresh', 'false').lower() == 'true'
    geo = request.args.get('geo', 'US')
    
    # Check cache first
    if not fresh and os.path.exists(GOOGLE_CACHE):
        cache_age = datetime.now().timestamp() - os.path.getmtime(GOOGLE_CACHE)
        
        if cache_age < 43200:  # 12 hour cache (250 API calls/mo limit)
            with open(GOOGLE_CACHE, 'r') as f:
                cached_data = json.load(f)
            
            # Apply limit to cached data
            if limit and limit < len(cached_data.get('trends', [])):
                cached_data['trends'] = cached_data['trends'][:limit]
            
            cached_data['cached'] = True
            cached_data['cache_age_seconds'] = int(cache_age)
            cached_data['cache_age_hours'] = round(cache_age / 3600, 1)
            
            return jsonify(cached_data)
    
    # Fetch fresh data from SerpAPI Trending Now
    result = google_trends_serpapi.get_trending_searches(geo=geo, limit=limit)
    
    if result['success']:
        # Add cache metadata
        result['cached'] = False
        
        # Save to cache
        with open(GOOGLE_CACHE, 'w') as f:
            json.dump(result, f, indent=2)
        
        return jsonify(result)
    else:
        # Return error
        return jsonify({
            "error": "Failed to fetch Google trends",
            "message": result.get('message', 'Unknown error'),
            "details": result.get('error', '')
        }), 500


@app.route('/trends/google/rss', methods=['GET'])
def get_google_trends_rss(limit=None, geo=None):
    """Get Google Trends RSS data (free, limited)"""
    
    if limit is None:
        limit = int(request.args.get('limit', 20))
    if geo is None:
        geo = request.args.get('geo', 'US')
    
    fresh = request.args.get('fresh', 'false').lower() == 'true'
    
    # Check cache first
    if not fresh and os.path.exists(GOOGLE_RSS_CACHE):
        cache_age = datetime.now().timestamp() - os.path.getmtime(GOOGLE_RSS_CACHE)
        
        if cache_age < 7200:  # 2 hours (Google RSS updates slowly)
            with open(GOOGLE_RSS_CACHE, 'r') as f:
                cached_data = json.load(f)
            
            cached_data['trends'] = cached_data['trends'][:limit]
            cached_data['cached'] = True
            cached_data['cache_age_seconds'] = int(cache_age)
            
            return jsonify(cached_data)
    
    # Fetch fresh data
    result = google_rss_collector.fetch_trends(geo=geo)
    
    if result['success']:
        # Save to cache
        with open(GOOGLE_RSS_CACHE, 'w') as f:
            json.dump(result, f, indent=2)
        
        result['trends'] = result['trends'][:limit]
        result['cached'] = False
        result['source'] = 'rss_fallback'
        
        return jsonify(result)
    else:
        return jsonify({
            "error": "Failed to fetch Google trends",
            "message": result.get('error', 'Unknown error')
        }), 500


@app.route('/trends/refresh', methods=['POST'])
def refresh_trends():
    """Force refresh all trends"""
    
    result = twitter_collector.find_trending_topics()
    
    if result['success']:
        # Save to cache
        with open(TWITTER_CACHE, 'w') as f:
            json.dump(result, f, indent=2)
        
        return jsonify({
            "success": True,
            "message": "Trends refreshed successfully",
            "count": result['count'],
            "fetched_at": result['fetched_at']
        })
    else:
        return jsonify({
            "error": "Failed to refresh trends",
            "message": result.get('error', 'Unknown error')
        }), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    })


if __name__ == '__main__':
    print("🚀 Starting makethiscontent.com Trends API...")
    print("📡 API will be available at: http://localhost:5000")
    print("\nEndpoints:")
    print("  GET  /           - API info")
    print("  GET  /trends     - Get trending topics")
    print("  GET  /trends/twitter - Get Twitter trends")
    print("  POST /trends/refresh - Force refresh")
    print("  GET  /health     - Health check")
    print("\n✅ Ready to serve trends!\n")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
