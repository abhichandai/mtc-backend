#!/usr/bin/env python3
"""
Twitter Search Collector for makethiscontent.com
Detects trending topics by analyzing tweet engagement velocity
"""

import requests
import json
from datetime import datetime, timedelta
from collections import Counter
import re

class TwitterTrendsCollector:
    def __init__(self, bearer_token):
        self.bearer_token = bearer_token
        self.base_url = "https://api.twitter.com/2"
        self.headers = {
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "makethiscontent-trend-collector/1.0"
        }
    
    def search_recent_tweets(self, query, max_results=100):
        """
        Search recent tweets (last 7 days)
        
        Args:
            query: Search query
            max_results: Number of tweets to fetch (10-100)
        
        Returns:
            List of tweets with metadata
        """
        endpoint = f"{self.base_url}/tweets/search/recent"
        
        params = {
            "query": query,
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,entities",
            "expansions": "author_id"
        }
        
        try:
            response = requests.get(endpoint, headers=self.headers, params=params)
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "error": f"HTTP {response.status_code}",
                    "message": response.text
                }
                
        except Exception as e:
            return {"error": str(e)}
    
    def extract_hashtags(self, tweets_data):
        """
        Extract and rank hashtags by frequency and engagement
        
        Args:
            tweets_data: Raw Twitter API response
        
        Returns:
            Ranked list of trending hashtags
        """
        if "data" not in tweets_data:
            return []
        
        hashtag_stats = {}
        
        for tweet in tweets_data["data"]:
            # Extract hashtags
            hashtags = []
            if "entities" in tweet and "hashtags" in tweet["entities"]:
                hashtags = [tag["tag"].lower() for tag in tweet["entities"]["hashtags"]]
            
            # Get engagement metrics
            metrics = tweet.get("public_metrics", {})
            engagement = (
                metrics.get("like_count", 0) + 
                metrics.get("retweet_count", 0) * 2 +  # Retweets count double
                metrics.get("reply_count", 0)
            )
            
            # Score each hashtag
            for tag in hashtags:
                if tag not in hashtag_stats:
                    hashtag_stats[tag] = {
                        "count": 0,
                        "total_engagement": 0,
                        "avg_engagement": 0
                    }
                
                hashtag_stats[tag]["count"] += 1
                hashtag_stats[tag]["total_engagement"] += engagement
        
        # Calculate average engagement
        for tag in hashtag_stats:
            if hashtag_stats[tag]["count"] > 0:
                hashtag_stats[tag]["avg_engagement"] = (
                    hashtag_stats[tag]["total_engagement"] / hashtag_stats[tag]["count"]
                )
        
        # Sort by velocity score (frequency * avg engagement)
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
        
        # Sort by velocity score
        ranked.sort(key=lambda x: x["velocity_score"], reverse=True)
        
        return ranked
    
    def find_trending_topics(self, categories=None, tweets_per_category=100):
        """
        Find trending topics across multiple search categories
        
        Args:
            categories: List of search queries/categories
            tweets_per_category: Tweets to fetch per category
        
        Returns:
            Aggregated trending topics
        """
        if categories is None:
            # Default categories to search
            categories = [
                "(trending OR viral OR breaking) -is:retweet lang:en",
                "#breaking -is:retweet lang:en",
                "what's happening -is:retweet lang:en"
            ]
        
        all_hashtags = []
        
        for query in categories:
            tweets_data = self.search_recent_tweets(query, max_results=tweets_per_category)
            
            if "data" in tweets_data:
                hashtags = self.extract_hashtags(tweets_data)
                all_hashtags.extend(hashtags)
        
        # Combine and re-rank
        combined = {}
        for item in all_hashtags:
            tag = item["hashtag"]
            if tag not in combined:
                combined[tag] = {
                    "hashtag": tag,
                    "mentions": 0,
                    "total_engagement": 0,
                    "velocity_score": 0
                }
            
            combined[tag]["mentions"] += item["mentions"]
            combined[tag]["total_engagement"] += item["total_engagement"]
            combined[tag]["velocity_score"] += item["velocity_score"]
        
        # Convert to list and sort
        final_trends = list(combined.values())
        final_trends.sort(key=lambda x: x["velocity_score"], reverse=True)
        
        # Add rank and format
        for idx, trend in enumerate(final_trends):
            trend["rank"] = idx + 1
            trend["source"] = "twitter_search"
            trend["timestamp"] = datetime.utcnow().isoformat()
        
        return {
            "success": True,
            "count": len(final_trends),
            "trends": final_trends[:20],  # Top 20
            "fetched_at": datetime.utcnow().isoformat()
        }


def main():
    """Test the Twitter trends collector"""
    
    # Load credentials
    with open('/root/clawd/credentials/twitter-api.json', 'r') as f:
        creds = json.load(f)
    
    collector = TwitterTrendsCollector(creds['bearer_token'])
    
    print("🐦 Fetching Twitter trends...")
    print("   Searching for trending/viral content...\n")
    
    result = collector.find_trending_topics()
    
    if result['success']:
        print(f"✅ Found {result['count']} trending topics:\n")
        
        for trend in result['trends'][:10]:
            print(f"  #{trend['rank']} - {trend['hashtag']}")
            print(f"      Mentions: {trend['mentions']} | Engagement: {trend['total_engagement']} | Velocity: {trend['velocity_score']}")
        
        # Save to file
        output_file = '/root/clawd/mtc-backend/twitter-trends-latest.json'
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"\n💾 Saved to: {output_file}")
        
    else:
        print(f"❌ Error: {result.get('error', 'Unknown error')}")


if __name__ == '__main__':
    main()
