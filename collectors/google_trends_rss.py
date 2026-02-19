#!/usr/bin/env python3
"""
Google Trends RSS Collector for makethiscontent.com
Uses official Google Trends RSS feed (no blocking, no API needed)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime
import json
import re

class GoogleTrendsRSS:
    def __init__(self):
        self.base_url = "https://trends.google.com/trending/rss"
    
    def fetch_trends(self, geo='US'):
        """
        Fetch trending searches from Google Trends RSS feed
        
        Args:
            geo: Country code (US, GB, CA, IN, etc.)
        
        Returns:
            List of trending topics with metadata
        """
        try:
            # Fetch RSS feed
            url = f"{self.base_url}?geo={geo}"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}',
                    'message': response.text[:200]
                }
            
            # Parse XML
            root = ET.fromstring(response.content)
            
            # Extract trends
            trends = []
            
            # RSS structure: channel -> item
            # Define namespace for Google Trends custom tags
            ns = {'ht': 'https://trends.google.com/trending/rss'}
            
            for idx, item in enumerate(root.findall('.//item')):
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                
                # Get traffic from ht:approx_traffic tag
                traffic_elem = item.find('ht:approx_traffic', ns)
                traffic = traffic_elem.text if traffic_elem is not None else None
                
                # Get related news articles
                news_items = item.findall('ht:news_item', ns)
                related_articles = []
                for news in news_items[:3]:  # Top 3 articles
                    title_elem = news.find('ht:news_item_title', ns)
                    url_elem = news.find('ht:news_item_url', ns)
                    source_elem = news.find('ht:news_item_source', ns)
                    
                    if title_elem is not None and url_elem is not None:
                        related_articles.append({
                            'title': title_elem.text,
                            'url': url_elem.text,
                            'source': source_elem.text if source_elem is not None else None
                        })
                
                trend_data = {
                    'rank': idx + 1,
                    'topic': title.text if title is not None else 'Unknown',
                    'url': link.text if link is not None else None,
                    'pub_date': pub_date.text if pub_date is not None else None,
                    'approximate_traffic': traffic,
                    'related_articles': related_articles,
                    'source': 'google_trends_rss',
                    'geo': geo,
                    'timestamp': datetime.utcnow().isoformat()
                }
                
                trends.append(trend_data)
            
            return {
                'success': True,
                'count': len(trends),
                'trends': trends,
                'geo': geo,
                'fetched_at': datetime.utcnow().isoformat()
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': 'Network error',
                'message': str(e)
            }
        except ET.ParseError as e:
            return {
                'success': False,
                'error': 'XML parse error',
                'message': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'error': 'Unknown error',
                'message': str(e)
            }
    
    def fetch_multiple_regions(self, regions=['US', 'GB', 'CA']):
        """
        Fetch trends from multiple regions
        
        Args:
            regions: List of country codes
        
        Returns:
            Combined trends from all regions
        """
        all_trends = []
        
        for geo in regions:
            result = self.fetch_trends(geo=geo)
            
            if result['success']:
                all_trends.extend(result['trends'])
        
        # Sort by rank (lower is better) and geo
        all_trends.sort(key=lambda x: (x['rank'], x['geo']))
        
        return {
            'success': True,
            'count': len(all_trends),
            'trends': all_trends,
            'regions': regions,
            'fetched_at': datetime.utcnow().isoformat()
        }


def main():
    """Test the Google Trends RSS collector"""
    
    collector = GoogleTrendsRSS()
    
    print("🌐 Fetching Google Trends (RSS feed)...")
    print("   Region: United States\n")
    
    result = collector.fetch_trends(geo='US')
    
    if result['success']:
        print(f"✅ Found {result['count']} trending topics:\n")
        
        for trend in result['trends'][:15]:
            traffic = f"({trend['approximate_traffic']}+ searches)" if trend['approximate_traffic'] else "(traffic unknown)"
            print(f"  #{trend['rank']} - {trend['topic']}")
            print(f"      {traffic}")
        
        # Save to file
        output_file = '/root/clawd/mtc-backend/google-trends-rss-latest.json'
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        
        print(f"\n💾 Saved to: {output_file}")
        
    else:
        print(f"❌ Error: {result.get('error', 'Unknown')}")
        print(f"   Message: {result.get('message', 'No details')}")


if __name__ == '__main__':
    main()
