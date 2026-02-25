#!/usr/bin/env python3
"""
Google Trends RSS Collector for makethiscontent.com
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime


class GoogleTrendsRSS:
    def __init__(self):
        self.base_url = "https://trends.google.com/trending/rss"

    def fetch_trends(self, geo="US"):
        try:
            url = f"{self.base_url}?geo={geo}"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return {"success": False, "error": f"HTTP {response.status_code}"}

            root = ET.fromstring(response.content)
            ns = {"ht": "https://trends.google.com/trending/rss"}
            trends = []

            for idx, item in enumerate(root.findall(".//item")):
                title    = item.find("title")
                link     = item.find("link")
                pub_date = item.find("pubDate")
                traffic_elem = item.find("ht:approx_traffic", ns)
                traffic = traffic_elem.text if traffic_elem is not None else None

                news_items = item.findall("ht:news_item", ns)
                related_articles = []
                for news in news_items[:3]:
                    t_elem = news.find("ht:news_item_title", ns)
                    u_elem = news.find("ht:news_item_url", ns)
                    s_elem = news.find("ht:news_item_source", ns)
                    if t_elem is not None and u_elem is not None:
                        related_articles.append({
                            "title": t_elem.text,
                            "url": u_elem.text,
                            "source": s_elem.text if s_elem is not None else None,
                        })

                trends.append({
                    "rank": idx + 1,
                    "topic": title.text if title is not None else "Unknown",
                    "url": link.text if link is not None else None,
                    "pub_date": pub_date.text if pub_date is not None else None,
                    "approximate_traffic": traffic,
                    "related_articles": related_articles,
                    "source": "google_trends_rss",
                    "geo": geo,
                    "timestamp": datetime.utcnow().isoformat(),
                })

            return {
                "success": True,
                "count": len(trends),
                "trends": trends,
                "geo": geo,
                "fetched_at": datetime.utcnow().isoformat(),
            }

        except requests.exceptions.RequestException as e:
            return {"success": False, "error": "Network error", "message": str(e)}
        except ET.ParseError as e:
            return {"success": False, "error": "XML parse error", "message": str(e)}
        except Exception as e:
            return {"success": False, "error": "Unknown error", "message": str(e)}
