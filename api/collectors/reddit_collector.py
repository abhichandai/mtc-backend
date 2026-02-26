#!/usr/bin/env python3
"""
Reddit Collector for MakeThisContent
Uses ScrapeCreators API - works from any IP, no OAuth required.
"""

import requests
import time
import os
from datetime import datetime, timezone

BASE_URL = "https://api.scrapecreators.com/v1/reddit/subreddit"
REQUEST_DELAY = 0.3


def fetch_subreddit_hot(subreddit: str, limit: int = 25) -> list:
    # Read env var at call time, not module load time
    api_key = os.environ.get("SCRAPECREATORS_API_KEY", "")
    if not api_key:
        print("[Reddit] SCRAPECREATORS_API_KEY env var not set")
        return []

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    params = {"subreddit": subreddit, "sort": "hot", "trim": "true"}

    try:
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            print("[Reddit] Invalid API key")
            return []
        if resp.status_code == 402:
            print("[Reddit] Out of ScrapeCreators credits")
            return []
        if resp.status_code == 404:
            print(f"[Reddit] Subreddit r/{subreddit} not found")
            return []
        if resp.status_code == 429:
            print(f"[Reddit] Rate limited on r/{subreddit}")
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[Reddit] Error fetching r/{subreddit}: {e}")
        return []
    except Exception as e:
        print(f"[Reddit] Unexpected error r/{subreddit}: {e}")
        return []

    posts = []
    for post in data.get("posts", []):
        score = post.get("score", post.get("ups", 0))
        if score < 5:
            continue

        posts.append({
            "id": post.get("id", ""),
            "title": post.get("title", ""),
            "preview": "",
            "score": score,
            "num_comments": post.get("num_comments", 0),
            "engagement": score + post.get("num_comments", 0) * 3,
            "subreddit": post.get("subreddit", subreddit),
            "url": post.get("url", f"https://reddit.com/r/{subreddit}"),
            "external_url": None,
            "is_text_post": False,
            "author": post.get("author", ""),
            "created_utc": post.get("created_utc", 0),
            "flair": post.get("link_flair_text", ""),
            "upvote_ratio": post.get("upvote_ratio", 0),
            "source": "reddit",
        })

    return posts[:limit]


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
        "source": "scrapecreators",
    }

