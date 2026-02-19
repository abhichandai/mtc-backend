#!/usr/bin/env python3
"""
Google Trends collector using SerpAPI - Trending Now API
Fetches real-time trending searches with full metadata
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Optional
import requests

CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'credentials', 'serpapi.json')

def load_api_key():
    """Load SerpAPI key from credentials file"""
    with open(CREDENTIALS_PATH, 'r') as f:
        creds = json.load(f)
        return creds['api_key']

def get_trending_searches(geo='US', limit=None):
    """
    Fetch trending searches from Google Trends Trending Now API
    
    Args:
        geo: Country code (US, UK, CA, etc.)
        limit: Number of trends to return (None = all)
    
    Returns:
        Dict with success status and list of trending topics
    """
    api_key = load_api_key()
    
    url = 'https://serpapi.com/search.json'
    params = {
        'engine': 'google_trends_trending_now',
        'geo': geo,
        'api_key': api_key
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if 'error' in data:
            return {
                'success': False,
                'error': data['error'],
                'message': 'SerpAPI returned an error'
            }
        
        # Extract trending searches
        trending_searches = data.get('trending_searches', [])
        
        if not trending_searches:
            return {
                'success': False,
                'error': 'No trending searches found',
                'message': 'API returned empty results'
            }
        
        # Apply limit if specified
        if limit:
            trending_searches = trending_searches[:limit]
        
        # Add metadata
        result = {
            'success': True,
            'count': len(trending_searches),
            'total_available': len(data.get('trending_searches', [])),
            'geo': geo,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'source': 'serpapi_trending_now',
            'trends': trending_searches
        }
        
        return result
    
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': str(e),
            'message': 'Failed to fetch from SerpAPI'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'message': 'Unexpected error'
        }

def get_trend_news(page_token):
    """
    Fetch news articles for a specific trending topic
    
    Args:
        page_token: News page token from trending search result
    
    Returns:
        List of news articles
    """
    api_key = load_api_key()
    
    url = 'https://serpapi.com/search.json'
    params = {
        'engine': 'google_trends_news',
        'page_token': page_token,
        'api_key': api_key
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if 'error' in data:
            return []
        
        return data.get('news_results', [])
    
    except Exception as e:
        print(f"Error fetching trend news: {e}")
        return []

if __name__ == '__main__':
    # Test the collector
    print("🔍 Testing Google Trends Trending Now API...")
    print("=" * 60)
    
    result = get_trending_searches(geo='US', limit=10)
    
    if result['success']:
        print(f"✅ Success!")
        print(f"   Total trends available: {result['total_available']}")
        print(f"   Showing: {result['count']}")
        print(f"   Geo: {result['geo']}")
        print(f"   Fetched: {result['fetched_at']}")
        print()
        
        print("Top 10 Trending Searches:")
        print("-" * 60)
        
        for idx, trend in enumerate(result['trends'], 1):
            print(f"\n{idx}. {trend['query'].upper()}")
            print(f"   Search Volume: {trend['search_volume']:,}")
            print(f"   Increase: {trend['increase_percentage']}%")
            print(f"   Active: {trend['active']}")
            
            if trend.get('categories'):
                cats = [c['name'] for c in trend['categories']]
                print(f"   Categories: {', '.join(cats)}")
            
            if trend.get('trend_breakdown'):
                related = trend['trend_breakdown'][:3]
                print(f"   Related: {', '.join(related)}")
        
        print()
        print("=" * 60)
        print(f"📊 Full data structure sample:")
        print(json.dumps(result['trends'][0], indent=2))
    else:
        print(f"❌ Failed: {result['error']}")
        print(f"   Message: {result['message']}")
