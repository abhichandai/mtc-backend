#!/usr/bin/env python3
"""
Twitter Search Collector for makethiscontent.com
"""

import requests
from datetime import datetime


class TwitterTrendsCollector:
    def __init__(self, bearer_token):
        self.bearer_token = bearer_token
        self.base_url = "https://api.twitter.com/2"
        self.headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "makethiscontent-trend-collector/1.0"
        }

    def search_recent_tweets(self, query, max_results=100):
        endpoint = f"{self.base_url}/tweets/search/recent"
        params = {
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,entities",
            "expansions": "author_id"
        }
        try:
            response = requests.get(endpoint, headers=self.headers, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}", "message": response.text}
        except Exception as e:
            return {"error": str(e)}

    def extract_hashtags(self, tweets_data):
        if "data" not in tweets_data:
            return []
        hashtag_stats = {}
        for tweet in tweets_data["data"]:
            hashtags = []
            if "entities" in tweet and "hashtags" in tweet["entities"]:
                hashtags = [tag["tag"].lower() for tag in tweet["entities"]["hashtags"]]
            metrics = tweet.get("public_metrics", {})
            engagement = (
                metrics.get("like_count", 0) +
                metrics.get("retweet_count", 0) * 2 +
                metrics.get("reply_count", 0)
            )
            for tag in hashtags:
                if tag not in hashtag_stats:
                    hashtag_stats[tag] = {"count": 0, "total_engagement": 0, "avg_engagement": 0}
                hashtag_stats[tag]["count"] += 1
                hashtag_stats[tag]["total_engagement"] += engagement
        for tag in hashtag_stats:
            if hashtag_stats[tag]["count"] > 0:
                hashtag_stats[tag]["avg_engagement"] = (
                    hashtag_stats[tag]["total_engagement"] / hashtag_stats[tag]["count"]
                )
        ranked = []
        for tag, stats in hashtag_stats.items():
            velocity_score = stats["count"] * stats["avg_engagement"]
            ranked.append({
                "hashtag": f"#{tag}",
                "mentions": stats["count"],
                "total_engagement": stats["total_engagement"],
                "avg_engagement": int(stats["avg_engagement"]),
                "velocity_score": int(velocity_score)
            })
        ranked.sort(key=lambda x: x["velocity_score"], reverse=True)
        return ranked

    def find_trending_topics(self, categories=None, tweets_per_category=100):
        if categories is None:
            categories = [
                "(trending OR viral OR breaking) -is:retweet lang:en",
                "#breaking -is:retweet lang:en",
                "what's happening -is:retweet lang:en"
            ]
        all_hashtags = []
        for query in categories:
            tweets_data = self.search_recent_tweets(query, max_results=tweets_per_category)
            if "data" in tweets_data:
                all_hashtags.extend(self.extract_hashtags(tweets_data))

        combined = {}
        for item in all_hashtags:
            tag = item["hashtag"]
            if tag not in combined:
                combined[tag] = {"hashtag": tag, "mentions": 0, "total_engagement": 0, "velocity_score": 0}
            combined[tag]["mentions"] += item["mentions"]
            combined[tag]["total_engagement"] += item["total_engagement"]
            combined[tag]["velocity_score"] += item["velocity_score"]

        final_trends = sorted(combined.values(), key=lambda x: x["velocity_score"], reverse=True)
        for idx, trend in enumerate(final_trends):
            trend["rank"] = idx + 1
            trend["source"] = "twitter_search"
            trend["timestamp"] = datetime.utcnow().isoformat()

        return {
            "success": True,
            "count": len(final_trends),
            "trends": final_trends[:20],
            "fetched_at": datetime.utcnow().isoformat()
        }
