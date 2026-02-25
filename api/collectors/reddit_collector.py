#!/usr/bin/env python3
"""
Reddit Collector for MakeThisContent
Fetches hot posts using Reddit's public JSON API — no OAuth required.
"""

import requests
import time
from datetime import datetime, timezone

USER_AGENT = "MakeThisContent/1.0 (makethiscontent.com trend intelligence)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
REQUEST_DELAY = 0.5


def fetch_subreddit_hot(subreddit: str, limit: int = 25) -> list:
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    params = {"limit": limit, "raw_json": 1}

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=8)
        if resp.status_code == 404:
            print(f"[Reddit] r/{subreddit} not found")
            return []
        if resp.status_code == 403:
            print(f"[Reddit] r/{subreddit} is private/quarantined")
            return []
        if resp.status_code == 429:
            print(f"[Reddit] Rate limited on r/{subreddit}")
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Reddit] Error fetching r/{subreddit}: {e}")
        return []

    posts = []
    for item in data.get("data", {}).get("children", []):
        post = item.get("data", {})
        if post.get("stickied") or post.get("distinguished") == "moderator":
            continue
        if post.get("score", 0) < 5:
            continue
        selftext = post.get("selftext", "").strip()
        preview = selftext[:280] + "..." if len(selftext) > 280 else selftext
        posts.append({
            "id": post.get("id", ""),
            "title": post.get("title", ""),
            "preview": preview,
            "score": post.get("score", 0),
            "num_comments": post.get("num_comments", 0),
            "engagement": post.get("score", 0) + post.get("num_comments", 0) * 3,
            "subreddit": post.get("subreddit", subreddit),
            "url": f"https://reddit.com{post.get('permalink', '')}",
            "external_url": post.get("url", "") if not post.get("is_self") else None,
            "is_text_post": post.get("is_self", False),
            "author": post.get("author", ""),
            "created_utc": post.get("created_utc", 0),
            "flair": post.get("link_flair_text", ""),
            "source": "reddit",
        })
    return posts


def fetch_multiple_subreddits(subreddits: list, limit_per_sub: int = 20) -> dict:
    all_posts, fetched_subs, failed_subs = [], [], []

    for subreddit in subreddits:
        posts = fetch_subreddit_hot(subreddit, limit=limit_per_sub)
        if posts:
            all_posts.extend(posts)
            fetched_subs.append(subreddit)
        else:
            failed_subs.append(subreddit)
        time.sleep(REQUEST_DELAY)

    all_posts.sort(key=lambda p: p["engagement"], reverse=True)

    seen, deduped = set(), []
    for post in all_posts:
        key = post["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(post)

    return {
        "success": True,
        "posts": deduped,
        "count": len(deduped),
        "subreddits_fetched": fetched_subs,
        "subreddits_failed": failed_subs,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
