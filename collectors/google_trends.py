#!/usr/bin/env python3
"""
Google Trends Collector for makethiscontent.com
Fetches real-time trending searches from Google Trends
"""

from pytrends.request import TrendReq
import json
from datetime import datetime
import time

def fetch_google_trends(geo='US', max_results=20):
    """
    Fetch current trending searches from Google Trends
    
    Args:
        geo: Country code (US, GB, CA, etc.)
        max_results: Number of trends to return
    
    Returns:
        List of trending topics with metadata
    """
    try:
        # Initialize pytrends
        pytrends = TrendReq(hl='en-US', tz=360)
        
        # Get real-time trending searches
        # Use today's searches instead (more reliable)
        trending_data = pytrends.today_searches(pn=geo)
        
        # If today_searches fails, try trending_searches
        if trending_data is None or len(trending_data) == 0:
            trending_searches = pytrends.trending_searches(pn='united_states')
        else:
            trending_searches = trending_data
        
        # Format results
        trends = []
        for idx, topic in enumerate(trending_searches[0][:max_results]):
            trends.append({
                'rank': idx + 1,
                'topic': topic,
                'source': 'google_trends',
                'geo': geo,
                'timestamp': datetime.utcnow().isoformat(),
                'score': max_results - idx  # Simple scoring: higher rank = higher score
            })
        
        return {
            'success': True,
            'count': len(trends),
            'trends': trends,
            'fetched_at': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'fetched_at': datetime.utcnow().isoformat()
        }

def get_related_queries(keyword):
    """
    Get related queries for a specific keyword
    
    Args:
        keyword: Topic to get related queries for
        
    Returns:
        Related rising and top queries
    """
    try:
        pytrends = TrendReq(hl='en-US', tz=360)
        pytrends.build_payload([keyword], timeframe='now 7-d')
        
        related = pytrends.related_queries()
        
        return {
            'success': True,
            'keyword': keyword,
            'related': related[keyword] if keyword in related else {},
            'fetched_at': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'keyword': keyword,
            'fetched_at': datetime.utcnow().isoformat()
        }

if __name__ == '__main__':
    print("🔍 Fetching Google Trends...")
    
    # Fetch US trends
    result = fetch_google_trends(geo='US', max_results=10)
    
    if result['success']:
        print(f"\n✅ Found {result['count']} trending topics:\n")
        for trend in result['trends']:
            print(f"  #{trend['rank']} - {trend['topic']} (score: {trend['score']})")
        
        # Save to file
        output_file = '/root/clawd/mtc-backend/google-trends-latest.json'
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Saved to: {output_file}")
        
    else:
        print(f"\n❌ Error: {result['error']}")
