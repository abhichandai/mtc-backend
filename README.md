# makethiscontent.com - Trends API Backend

**Status:** ✅ Working - Built Feb 4, 2026

## What This Is

Real-time trending topics API powered by Twitter Search. Detects what's trending RIGHT NOW by analyzing tweet engagement velocity.

## API Endpoints

**Base URL:** `http://143.198.46.229:5000`

### Get Trends
```bash
GET /trends?limit=20
```

Returns top 20 trending topics with:
- Hashtag
- Number of mentions
- Total engagement (likes + retweets + replies)
- Velocity score (trend strength)

**Example:**
```bash
curl http://143.198.46.229:5000/trends?limit=10
```

### Force Refresh
```bash
POST /trends/refresh
```

Forces immediate fetch of fresh trends (bypasses 1-hour cache).

### Health Check
```bash
GET /health
```

Returns API status.

## How It Works

1. **Twitter Search Collector** (`collectors/twitter_search.py`)
   - Searches Twitter for trending/viral content
   - Extracts hashtags from tweets
   - Scores by engagement velocity (mentions × avg engagement)
   - Ranks by velocity score

2. **Caching**
   - Trends cached for 1 hour
   - Auto-refreshes on first request after cache expires
   - Manual refresh via `/trends/refresh`

3. **Data Structure**
```json
{
  "success": true,
  "count": 135,
  "trends": [
    {
      "rank": 1,
      "hashtag": "#breaking",
      "mentions": 105,
      "total_engagement": 327,
      "velocity_score": 327,
      "source": "twitter_search",
      "timestamp": "2026-02-04T22:48:00.123Z"
    }
  ],
  "fetched_at": "2026-02-04T22:48:00.123Z",
  "cached": false
}
```

## Running the API

### Development (Manual)
```bash
cd /root/clawd/mtc-backend
source ../mtc-env/bin/activate
python3 api.py
```

### Production (Systemd Service)
```bash
# Start service
sudo systemctl start mtc-trends-api

# Enable on boot
sudo systemctl enable mtc-trends-api

# Check status
sudo systemctl status mtc-trends-api

# View logs
sudo journalctl -u mtc-trends-api -f
```

## Files Structure

```
/root/clawd/mtc-backend/
├── api.py                        # Flask API server
├── collectors/
│   ├── twitter_search.py         # Twitter trends collector
│   └── google_trends.py          # Google Trends (blocked - not working)
├── twitter-trends-latest.json    # Cached trends data
└── README.md                     # This file

/root/clawd/mtc-env/              # Python virtual environment
/root/clawd/credentials/twitter-api.json  # Twitter API credentials
```

## Next Steps

### Phase 1: Backend (✅ DONE TODAY)
- [x] Twitter Search collector
- [x] Trend ranking algorithm
- [x] REST API with caching
- [x] Health check endpoint

### Phase 2: Production Setup (TODO)
- [ ] Create systemd service
- [ ] Set up automatic trend refresh (cron or celery)
- [ ] Add error monitoring
- [ ] Set up production WSGI server (gunicorn)

### Phase 3: Frontend (TODO)
- [ ] Simple dashboard showing trends
- [ ] Real-time updates
- [ ] Trend history graphs
- [ ] Export functionality

### Phase 4: Additional Features (FUTURE)
- [ ] Add Reddit trends collector
- [ ] Google Trends RSS feed (bypass blocking)
- [ ] Trend categories/filtering
- [ ] Historical trend tracking
- [ ] Email alerts for specific keywords

## API Credentials

**Twitter API:** `/root/clawd/credentials/twitter-api.json`
- Bearer token authentication
- Search API access (7-day lookback)
- Cost: $0.005 per request

## Testing

Test the collector directly:
```bash
cd /root/clawd/mtc-backend
source ../mtc-env/bin/activate
python3 collectors/twitter_search.py
```

Test the API:
```bash
# Get trends
curl http://localhost:5000/trends?limit=5

# Health check
curl http://localhost:5000/health

# Force refresh
curl -X POST http://localhost:5000/trends/refresh
```

## Notes

- **Cache Duration:** 1 hour (configurable in api.py)
- **Rate Limiting:** Twitter API has rate limits - be mindful of refresh frequency
- **Cost:** ~$0.005 per trend fetch (100 tweets per category, 3 categories)
- **Server:** Running on clawdbot-server-01 (143.198.46.229)

---

**Built:** February 4, 2026  
**Status:** MVP Ready - Backend Complete ✅
