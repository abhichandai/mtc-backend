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
MAX_PER_SUBREDDIT = 3   # max cards per subreddit in final results


def _velocity_score(post: dict) -> float:
    """
    Rank by comment velocity × upvote quality, with soft age decay.

    Formula:
      base     = (num_comments / max(hours_since_posted, 0.5)) × upvote_ratio
      age_factor = 1.0 for posts ≤ 48h old
                 = sqrt(48 / hours) for older posts (soft decay — active old
                   threads still surface, they just need more velocity)
      final    = base × age_factor
    """
    now = time.time()
    created = post.get("created_utc", 0)
    hours = max((now - created) / 3600, 0.5)  # avoid div/0

    comments = post.get("num_comments", 0)
    ratio = post.get("upvote_ratio", 0.5)

    base = (comments / hours) * ratio

    if hours <= 48:
        age_factor = 1.0
    else:
        age_factor = (48 / hours) ** 0.5  # square-root decay, not a hard cutoff

    return base * age_factor


def fetch_subreddit_hot(subreddit: str, limit: int = 25) -> list:
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

        created = post.get("created_utc", 0)

        posts.append({
            "id": post.get("id", ""),
            "title": post.get("title", ""),
            "preview": post.get("selftext", "") or "",   # capture post body if present
            "score": score,
            "num_comments": post.get("num_comments", 0),
            "upvote_ratio": post.get("upvote_ratio", 0),
            "subreddit": post.get("subreddit", subreddit),
            "url": post.get("url", f"https://reddit.com/r/{subreddit}"),
            "external_url": None,
            "is_text_post": bool(post.get("is_self", False)),
            "author": post.get("author", ""),
            "created_utc": created,
            "flair": post.get("link_flair_text", "") or "",
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

    # Deduplicate by title
    seen, deduped = set(), []
    for post in all_posts:
        key = post["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            deduped.append(post)

    # Rank by velocity score
    deduped.sort(key=_velocity_score, reverse=True)

    # Cap at MAX_PER_SUBREDDIT per subreddit so no single community dominates
    sub_counts: dict = {}
    capped = []
    for post in deduped:
        sub = post["subreddit"].lower()
        if sub_counts.get(sub, 0) < MAX_PER_SUBREDDIT:
            capped.append(post)
            sub_counts[sub] = sub_counts.get(sub, 0) + 1

    return {
        "success": True,
        "posts": capped,
        "count": len(capped),
        "subreddits_fetched": fetched_subs,
        "subreddits_failed": failed_subs,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "scrapecreators",
    }
