#!/usr/bin/env python3
"""
Google Trends collector using SerpAPI - Trending Now API
api_key is passed in as a parameter (loaded from env var in index.py)
"""

import requests
from datetime import datetime, timezone


def get_trending_searches(geo="US", limit=None, api_key=""):
    if not api_key:
        return {"success": False, "error": "SERPAPI_KEY env var not set"}

    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_trends_trending_now",
        "geo": geo,
        "api_key": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            return {"success": False, "error": data["error"]}

        trending_searches = data.get("trending_searches", [])
        if not trending_searches:
            return {"success": False, "error": "No trending searches found"}

        if limit:
            trending_searches = trending_searches[:limit]

        return {
            "success": True,
            "count": len(trending_searches),
            "total_available": len(data.get("trending_searches", [])),
            "geo": geo,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "serpapi_trending_now",
            "trends": trending_searches,
        }

    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_trend_news(page_token, api_key=""):
    if not api_key:
        return []
    url = "https://serpapi.com/search.json"
    params = {"engine": "google_trends_news", "page_token": page_token, "api_key": api_key}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("news_results", [])
    except Exception:
        return []
