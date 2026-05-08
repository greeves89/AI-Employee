"""trading_agent_template — 6 trading skills + Trading Analyst template

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-05-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

revision = "e6f7g8h9i0j1"
down_revision = "d5e6f7g8h9i0"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Skill content
# ---------------------------------------------------------------------------

SKILL_MARKET_SCANNER = """# Trading Skill: Market Scanner

Scan Polymarket for active prediction markets with the highest volume and most interesting odds.

## API Endpoints (no auth required)

```
# Active markets sorted by volume
GET https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=50&order=volume&ascending=false

# CLOB price data for a specific market
GET https://clob.polymarket.com/prices-history?market={condition_id}&interval=1d
```

## Workflow

1. Fetch top markets from Gamma API
2. Filter by minimum volume (default: $10,000)
3. Group by category (Politics, Crypto, Sports, Tech, Science, Business)
4. For each top market: fetch current Yes/No odds from CLOB API
5. Calculate implied probability: `implied_prob = yes_price / (yes_price + no_price)`

## Output Format

For each market, report:
```
📊 [MARKET NAME]
   Category: [category]
   Volume: $[volume]
   Yes: [yes_price]¢ / No: [no_price]¢
   Closes: [end_date]
   URL: https://polymarket.com/event/[slug]
```

## Python Example

```python
import requests

def scan_markets(min_volume=10000, limit=20, category=None):
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume",
        "ascending": "false",
    }
    if category:
        params["category"] = category

    resp = requests.get(url, params=params, timeout=15)
    markets = resp.json()

    results = []
    for m in markets:
        vol = float(m.get("volume", 0))
        if vol < min_volume:
            continue
        results.append({
            "id": m.get("conditionId"),
            "question": m.get("question"),
            "category": m.get("category", "Other"),
            "volume": vol,
            "yes_price": float(m.get("outcomePrices", ["0.5"])[0]),
            "no_price": float(m.get("outcomePrices", ["0.5", "0.5"])[1]),
            "end_date": m.get("endDate"),
            "slug": m.get("slug"),
        })
    return results

markets = scan_markets(min_volume=10000, limit=20)
for m in markets:
    print(f"📊 {m['question']}")
    print(f"   Vol: ${m['volume']:,.0f} | Yes: {m['yes_price']*100:.1f}¢ | No: {m['no_price']*100:.1f}¢")
```

## Notes
- Polymarket prices are in dollars (0.0–1.0), multiply by 100 for cents
- `conditionId` is used in CLOB API; `slug` is used for market URL
- Rate limit: ~60 req/min on public endpoints
"""

SKILL_ODDS_ANALYZER = """# Trading Skill: Odds Analyzer

Research a specific prediction market, form an independent probability estimate, and calculate the edge vs. market odds.

## Inputs
- Market question (string)
- Current Yes price from market scanner (0.0–1.0)
- Optional: deadline / resolution date

## Workflow

### Step 1: News Research
Use WebSearch to gather recent information:
```
search("{market_question} latest news site:reuters.com OR site:apnews.com OR site:bbc.com")
search("{market_question} prediction forecast 2025")
search("{market_question} expert opinion probability")
```

### Step 2: Independent Probability Estimation
Based on gathered evidence, assign a probability. Consider:
- **Base rate**: Historical frequency of similar events
- **Current signals**: What evidence points Yes? What points No?
- **Expert consensus**: What do forecasters/analysts say?
- **Confidence level**: How much uncertainty remains?

Document reasoning:
```
Evidence FOR Yes:
- [signal 1] → weight: high/medium/low
- [signal 2] → weight: high/medium/low

Evidence FOR No:
- [signal 1] → weight: high/medium/low

Base rate: [X]%
My estimate: [Y]%
Confidence: [low/medium/high]
```

### Step 3: Edge Calculation
```python
market_price = 0.45  # Yes price from scanner
my_estimate = 0.60   # Your probability estimate

edge = my_estimate - market_price
# Positive edge = market underpricing Yes
# Negative edge = market overpricing Yes (bet No instead)

print(f"Edge: {edge*100:+.1f}%")
if abs(edge) < 0.05:
    print("No significant edge — skip")
elif abs(edge) < 0.10:
    print("Small edge — small position")
else:
    print("Strong edge — worth analysis")
```

### Step 4: Kelly Criterion Position Sizing
```python
def kelly(p_win, odds_decimal):
    \"\"\"Kelly Criterion for binary prediction markets.
    p_win: your estimated probability (0.0-1.0)
    odds_decimal: payout if correct (e.g. Yes at 0.45 → payout = 1/0.45 = 2.22x)
    \"\"\"
    b = odds_decimal - 1  # net odds
    q = 1 - p_win
    fraction = (b * p_win - q) / b
    return max(0, fraction)  # never negative

yes_price = 0.45
my_prob = 0.60
decimal_odds = 1 / yes_price  # = 2.22x
full_kelly = kelly(my_prob, decimal_odds)
quarter_kelly = full_kelly * 0.25  # use 1/4 Kelly for safety

print(f"Full Kelly: {full_kelly*100:.1f}% of bankroll")
print(f"Recommended (1/4 Kelly): {quarter_kelly*100:.1f}% of bankroll")
```

## Output Format

```
🎯 ANALYSIS: [Market Question]

📊 Market Odds: Yes {yes_price*100:.1f}¢ (implied {market_prob:.0%})
🧠 My Estimate: {my_estimate:.0%}
📈 Edge: {edge*100:+.1f}%

Evidence:
✅ [strongest Yes signal]
❌ [strongest No signal]

Recommendation: [BUY YES / BUY NO / SKIP]
Position size: {quarter_kelly*100:.1f}% of bankroll
Confidence: [LOW/MEDIUM/HIGH]

Reasoning: [1-2 sentence summary]
```
"""

SKILL_PAPER_PORTFOLIO = """# Trading Skill: Paper Portfolio Tracker

Track virtual (paper) trades in a JSON file. No real money involved — purely for tracking prediction accuracy.

## Portfolio File Location
`/workspace/data/trading/portfolio.json`

## Portfolio Schema

```json
{
  "bankroll": 1000.00,
  "positions": [
    {
      "id": "pos_001",
      "market": "Will X happen by Y?",
      "market_url": "https://polymarket.com/event/slug",
      "condition_id": "0x...",
      "side": "YES",
      "entry_price": 0.45,
      "shares": 50.0,
      "cost": 22.50,
      "my_estimate": 0.62,
      "edge": 0.17,
      "kelly_fraction": 0.025,
      "reasoning": "Strong evidence from recent polls",
      "opened_at": "2026-05-08T10:00:00Z",
      "resolved_at": null,
      "outcome": null,
      "pnl": null
    }
  ],
  "history": [],
  "metrics": {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "total_pnl": 0.0,
    "roi_pct": 0.0,
    "win_rate": 0.0,
    "avg_edge": 0.0,
    "brier_score": null
  }
}
```

## Operations

### Open a Position
```python
import json, os, uuid
from datetime import datetime, timezone

def open_position(market, url, condition_id, side, entry_price, shares, my_estimate, reasoning):
    path = "/workspace/data/trading/portfolio.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        with open(path) as f:
            portfolio = json.load(f)
    except FileNotFoundError:
        portfolio = {"bankroll": 1000.0, "positions": [], "history": [], "metrics": {}}

    cost = entry_price * shares
    if cost > portfolio["bankroll"]:
        raise ValueError(f"Insufficient bankroll: need {cost:.2f}, have {portfolio['bankroll']:.2f}")

    pos = {
        "id": f"pos_{uuid.uuid4().hex[:8]}",
        "market": market,
        "market_url": url,
        "condition_id": condition_id,
        "side": side.upper(),
        "entry_price": entry_price,
        "shares": shares,
        "cost": cost,
        "my_estimate": my_estimate,
        "edge": my_estimate - entry_price,
        "reasoning": reasoning,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "outcome": None,
        "pnl": None,
    }
    portfolio["positions"].append(pos)
    portfolio["bankroll"] -= cost

    with open(path, "w") as f:
        json.dump(portfolio, f, indent=2)

    print(f"✅ Opened {side} position on '{market}' — {shares} shares @ {entry_price*100:.1f}¢ (cost: ${cost:.2f})")
    return pos
```

### Resolve a Position
```python
def resolve_position(pos_id, outcome_bool):
    \"\"\"outcome_bool: True if YES won, False if NO won\"\"\"
    path = "/workspace/data/trading/portfolio.json"
    with open(path) as f:
        portfolio = json.load(f)

    pos = next((p for p in portfolio["positions"] if p["id"] == pos_id), None)
    if not pos:
        raise ValueError(f"Position {pos_id} not found")

    won = (pos["side"] == "YES" and outcome_bool) or (pos["side"] == "NO" and not outcome_bool)
    pnl = pos["shares"] * (1 - pos["entry_price"]) if won else -pos["cost"]
    portfolio["bankroll"] += pos["cost"] + (pnl if won else 0)

    pos.update({"resolved_at": datetime.now(timezone.utc).isoformat(), "outcome": outcome_bool, "pnl": pnl})
    portfolio["history"].append(pos)
    portfolio["positions"] = [p for p in portfolio["positions"] if p["id"] != pos_id]

    # Update metrics
    m = portfolio["metrics"]
    m["total_trades"] = m.get("total_trades", 0) + 1
    m["wins"] = m.get("wins", 0) + (1 if won else 0)
    m["losses"] = m.get("losses", 0) + (0 if won else 1)
    m["total_pnl"] = m.get("total_pnl", 0) + pnl
    m["win_rate"] = m["wins"] / m["total_trades"]
    m["roi_pct"] = (m["total_pnl"] / 1000) * 100

    with open(path, "w") as f:
        json.dump(portfolio, f, indent=2)

    print(f"{'✅ WIN' if won else '❌ LOSS'}: {pos['market']} | P&L: ${pnl:+.2f} | Bankroll: ${portfolio['bankroll']:.2f}")
```

### Show Portfolio Status
```python
def show_portfolio():
    path = "/workspace/data/trading/portfolio.json"
    try:
        with open(path) as f:
            p = json.load(f)
    except FileNotFoundError:
        print("No portfolio yet. Use open_position() to start.")
        return

    print(f"💰 Bankroll: ${p['bankroll']:.2f}")
    print(f"📊 Open positions: {len(p['positions'])}")
    for pos in p["positions"]:
        print(f"  • {pos['side']} {pos['market'][:50]}… @ {pos['entry_price']*100:.1f}¢")

    m = p.get("metrics", {})
    if m.get("total_trades", 0) > 0:
        print(f"\\n📈 Performance:")
        print(f"  Trades: {m['total_trades']} | Win rate: {m.get('win_rate',0)*100:.0f}%")
        print(f"  Total P&L: ${m.get('total_pnl',0):+.2f} | ROI: {m.get('roi_pct',0):+.1f}%")
```
"""

SKILL_MARKET_REPORT = """# Trading Skill: Market Report

Generate a structured daily/bi-daily report of top prediction market opportunities and send it via Telegram notification.

## When to Run
- Morning scan: 08:00 CET
- Evening scan: 20:00 CET
- Can also be triggered manually

## Workflow

### Step 1: Scan Markets
Use `market-scanner` skill to fetch top 30 markets by volume.

### Step 2: Filter to Best Opportunities
For each top market, make a quick assessment:
- Is this resolvable soon (within 30 days)? Prefer shorter horizons.
- Is there likely inefficiency? Newer markets, less-covered events.
- Skip markets with >$1M volume (too efficient, sharp money already in).

### Step 3: Quick Edge Estimate (lightweight version)
Without full research, use a heuristic:
```python
def quick_edge_estimate(yes_price, category):
    \"\"\"Heuristics for markets that might be mispriced.\"\"\"
    # Markets near 0.1 or 0.9 often have fat tails underpriced
    if yes_price < 0.12 or yes_price > 0.88:
        return "possible long-tail play"
    # Markets near 0.5 are most competitive
    if 0.45 <= yes_price <= 0.55:
        return "highly contested, skip unless strong view"
    return "normal range"
```

### Step 4: Format Report

```python
def format_report(markets, top_n=5):
    lines = ["📊 *Polymarket Daily Report*", ""]
    lines.append(f"🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')} CET")
    lines.append(f"📈 Scanning top {len(markets)} markets\\n")

    for i, m in enumerate(markets[:top_n], 1):
        yes_pct = m['yes_price'] * 100
        no_pct = m['no_price'] * 100
        lines.append(
            f"{i}. *{m['question'][:60]}*\\n"
            f"   Yes: {yes_pct:.1f}¢ | No: {no_pct:.1f}¢ | Vol: ${m['volume']:,.0f}\\n"
            f"   🔗 {m['market_url']}"
        )

    return "\\n".join(lines)
```

### Step 5: Send Notification
Use the `notify_user` tool to send the report:
```
notify_user(
    title="📊 Polymarket Daily Report",
    message=report_text,
    priority="normal"
)
```

## Output Example
```
📊 Polymarket Daily Report

🕐 08.05.2026 08:00 CET
📈 Scanning top 30 markets

1. Will the Fed cut rates in June 2026?
   Yes: 34.2¢ | No: 65.8¢ | Vol: $892,450
   🔗 https://polymarket.com/event/fed-june-2026

2. Bitcoin above $100k by July 2026?
   Yes: 28.1¢ | No: 71.9¢ | Vol: $1,240,000
   🔗 https://polymarket.com/event/btc-100k-july

[...]
```
"""

SKILL_CRYPTO_SENTIMENT = """# Trading Skill: Crypto Sentiment Analyzer

Combine CoinGecko price data, Reddit sentiment, and Polymarket crypto prediction markets to identify divergences.

## Data Sources

### CoinGecko (no API key needed for basic tier)
```python
import requests

def get_crypto_prices(coins=["bitcoin", "ethereum", "solana"]):
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": ",".join(coins),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_7d_change": "true",
    }
    resp = requests.get(url, params=params, timeout=10)
    return resp.json()

prices = get_crypto_prices()
# Example: {"bitcoin": {"usd": 67500, "usd_24h_change": 2.3, "usd_7d_change": -5.1}}
```

### Reddit Sentiment (via Pushshift/public search)
```python
def get_reddit_sentiment(subreddit, keyword, limit=25):
    \"\"\"Use WebSearch as fallback if direct API unavailable.\"\"\"
    # Search recent Reddit posts for sentiment signals
    query = f"site:reddit.com/r/{subreddit} {keyword}"
    # Use the web_search tool or requests to fetch posts
    # Count positive vs negative signals in titles/comments
    pass
```

## Workflow

1. **Fetch crypto prediction markets** from Polymarket:
   ```python
   url = "https://gamma-api.polymarket.com/markets"
   params = {"active": "true", "category": "Crypto", "limit": 20, "order": "volume"}
   ```

2. **Fetch current prices** from CoinGecko

3. **Cross-reference**:
   - Is BTC up 5% this week but Polymarket BTC prediction markets pricing it bearish?
   - That's a potential divergence worth analyzing

4. **Sentiment signals to look for**:
   - Fear & Greed Index extremes (search "crypto fear greed index today")
   - Large funding rate spikes (search "bitcoin funding rate")
   - Exchange flow data (search "bitcoin exchange inflow outflow")

## Output Format

```
🪙 CRYPTO MARKET PULSE

Bitcoin: $67,500 (+2.3% 24h | -5.1% 7d)
Ethereum: $3,240 (+1.1% 24h | -3.8% 7d)

📊 Polymarket Crypto Markets:
• BTC above $75k by June? → Yes: 32¢ (implied 32%)
  Spot momentum: slightly bearish
  → DIVERGENCE: Spot moving up but market pricing lower
  → Worth deeper analysis

📰 Sentiment signals:
• Fear & Greed: 45 (Neutral)
• Funding rates: Normal
• Exchange flows: Slight outflow (bullish signal)

🎯 Watchlist for deeper analysis:
1. BTC $75k June — possible underpricing if momentum continues
```
"""

SKILL_BACKTEST_ANALYZER = """# Trading Skill: Backtest Analyzer

Evaluate the quality of past predictions using Brier Score and calibration analysis.

## What is Brier Score?
Brier Score measures prediction accuracy: `BS = (forecast - outcome)²`
- 0.0 = perfect
- 0.25 = random (like always guessing 50%)
- Lower is better

## Where to Get Historical Data

### Resolved Polymarket markets:
```python
import requests

def get_resolved_markets(limit=100):
    url = "https://gamma-api.polymarket.com/markets"
    params = {"closed": "true", "limit": limit, "order": "volume", "ascending": "false"}
    resp = requests.get(url, params=params, timeout=15)
    return [m for m in resp.json() if m.get("resolved")]
```

## Brier Score from Portfolio History

```python
import json, math

def analyze_portfolio_performance():
    path = "/workspace/data/trading/portfolio.json"
    with open(path) as f:
        portfolio = json.load(f)

    history = portfolio.get("history", [])
    if not history:
        print("No resolved trades yet.")
        return

    brier_scores = []
    calibration_buckets = {
        "50-59": {"count": 0, "wins": 0},
        "60-69": {"count": 0, "wins": 0},
        "70-79": {"count": 0, "wins": 0},
        "80-89": {"count": 0, "wins": 0},
        "90-99": {"count": 0, "wins": 0},
    }

    for trade in history:
        my_est = trade.get("my_estimate", 0.5)
        outcome = 1.0 if trade.get("outcome") else 0.0
        bs = (my_est - outcome) ** 2
        brier_scores.append(bs)

        # Calibration buckets
        est_pct = int(my_est * 100)
        for bucket in ["50-59", "60-69", "70-79", "80-89", "90-99"]:
            low, high = map(int, bucket.split("-"))
            if low <= est_pct <= high:
                calibration_buckets[bucket]["count"] += 1
                if trade.get("outcome"):
                    calibration_buckets[bucket]["wins"] += 1

    avg_brier = sum(brier_scores) / len(brier_scores)
    m = portfolio.get("metrics", {})

    print(f"📊 BACKTEST ANALYSIS ({len(history)} resolved trades)")
    print(f"Brier Score: {avg_brier:.4f} (random=0.25, perfect=0.00)")
    print(f"Win Rate: {m.get('win_rate', 0)*100:.1f}%")
    print(f"Total P&L: ${m.get('total_pnl', 0):+.2f} | ROI: {m.get('roi_pct', 0):+.1f}%")

    print("\\n📈 Calibration (when I said X%, did X% happen?):")
    for bucket, data in calibration_buckets.items():
        if data["count"] > 0:
            actual = data["wins"] / data["count"]
            print(f"  {bucket}%: predicted {bucket}% → actual {actual*100:.0f}% ({data['count']} trades)")
        else:
            print(f"  {bucket}%: no data")

    # Category performance
    cat_perf = {}
    for trade in history:
        cat = trade.get("category", "Unknown")
        if cat not in cat_perf:
            cat_perf[cat] = {"trades": 0, "pnl": 0}
        cat_perf[cat]["trades"] += 1
        cat_perf[cat]["pnl"] += trade.get("pnl", 0)

    print("\\n🏆 Performance by category:")
    for cat, data in sorted(cat_perf.items(), key=lambda x: -x[1]["pnl"]):
        print(f"  {cat}: {data['trades']} trades | P&L: ${data['pnl']:+.2f}")
```

## Weekly Report Format

```
📊 WEEKLY BACKTEST REPORT

Period: 01.05.2026 – 08.05.2026

🎯 Accuracy:
  Brier Score: 0.18 (better than random!)
  Win Rate: 62%
  Resolved trades: 8

💰 Returns:
  Total P&L: +$47.20
  ROI: +4.7%

📈 Calibration:
  When I said 60-69%: happened 65% of the time ✅
  When I said 70-79%: happened 58% of the time ⚠️ (overconfident)
  When I said 80-89%: happened 82% of the time ✅

💡 Insight: Slightly overconfident in 70-79% range. Adjust estimates down by ~5%.
```
"""

# ---------------------------------------------------------------------------
# Agent Template CLAUDE.md and knowledge
# ---------------------------------------------------------------------------

TRADING_CLAUDE_MD = """# Trading Analyst Agent

You are a specialized Prediction Market Analyst. Your job is to systematically scan prediction markets (primarily Polymarket), form independent probability estimates through research, identify pricing inefficiencies, track paper trades, and improve your forecasting accuracy over time.

## Your Skills

You have the following skills available — use them in this order for a full analysis cycle:

1. **market-scanner** — Scan Polymarket for active markets with significant volume
2. **odds-analyzer** — Research a specific market and calculate edge vs. market odds
3. **paper-portfolio** — Open/close/review virtual paper trades
4. **market-report** — Generate and send daily market reports via notification
5. **crypto-sentiment** — Crypto-specific analysis combining price data and sentiment
6. **backtest-analyzer** — Weekly performance review and calibration analysis

## Core Principles

### Independence First
Always form your OWN probability estimate BEFORE looking at the market price. Anchoring to market prices biases your analysis.

### Evidence-Based
Every prediction needs:
- At least 2-3 news sources checked
- A base rate (what's the historical frequency?)
- A confidence level (low/medium/high)

### Kelly Discipline
Never bet more than 1/4 Kelly. When uncertain, use 1/8 Kelly.
Maximum single position: 5% of bankroll.

### Track Everything
Log every prediction to the paper portfolio with full reasoning. The goal is calibration — knowing when you're right vs. wrong and why.

## Daily Routine

**08:00** — Run market-scanner, identify top 5 interesting markets, send morning report
**Ad-hoc** — When asked to analyze a specific market, run full odds-analyzer workflow
**20:00** — Evening scan, update any resolved positions in paper-portfolio
**Sunday** — Run backtest-analyzer for weekly performance review

## Workspace Structure

```
/workspace/data/trading/
  portfolio.json     — paper portfolio (positions + history + metrics)
  watchlist.json     — markets being monitored but not yet entered
  notes.md           — research notes and market observations
```

## Important Limits

- **No real money trading** — paper portfolio only
- **Polymarket public APIs only** — no authentication needed
- **No storing personal data** — only market data and your own analysis
- **Rate limits** — max 60 API calls/min on Polymarket public endpoints
"""

TRADING_KNOWLEDGE = """# Prediction Market Knowledge Base

## Key Polymarket APIs

### Gamma API (Market Metadata)
Base: `https://gamma-api.polymarket.com`

```
GET /markets?active=true&closed=false&limit=50&order=volume&ascending=false
GET /markets?category=Crypto&active=true
GET /markets/{condition_id}
```

Market object fields:
- `conditionId` — used in CLOB API
- `question` — market question text
- `category` — Politics, Crypto, Sports, Tech, Science, Business, Culture
- `volume` — total trading volume in USD
- `outcomePrices` — ["0.45", "0.55"] for [Yes, No]
- `endDate` — resolution deadline (ISO 8601)
- `resolved` — bool
- `resolutionSource` — how market is resolved

### CLOB API (Order Book)
Base: `https://clob.polymarket.com`

```
GET /prices-history?market={condition_id}&interval=1d&fidelity=60
GET /last-trade-price?token_id={token_id}
GET /orderbook?token_id={token_id}
```

## Kelly Criterion Reference

For binary prediction markets:
```
b = (1 / yes_price) - 1   # net odds if correct
p = your_estimated_probability
q = 1 - p
kelly_fraction = (b*p - q) / b
safe_fraction = kelly_fraction * 0.25  # use 1/4 Kelly
```

Example:
- Yes price: 0.35 (35 cents → pays $1 if correct)
- Your estimate: 50%
- b = (1/0.35) - 1 = 1.857
- Kelly = (1.857 * 0.50 - 0.50) / 1.857 = 0.231 (23% full Kelly)
- Safe (1/4 Kelly): 5.8% of bankroll

## Brier Score Reference

| Score | Interpretation |
|-------|---------------|
| 0.00  | Perfect prediction |
| 0.05  | Expert forecaster (superforecaster level) |
| 0.15  | Good forecaster |
| 0.20  | Above average |
| 0.25  | Random (always predicting 50%) |
| 0.33  | Below random |

## Calibration Reference

A calibrated forecaster's predictions match reality:
- When you say 70%, it should happen 70% of the time
- When you say 90%, it should happen 90% of the time

Common biases:
- **Overconfidence**: saying 80% but only right 65% of the time → reduce high estimates
- **Underconfidence**: saying 60% but right 75% of the time → increase estimates
- **Recency bias**: overweighting recent news → check base rates

## Market Categories and Base Rates

### Politics
- Incumbent parties win elections ~55% in stable democracies
- Polling averages are better than individual polls
- Markets often overreact to single events

### Crypto
- High volatility → wider confidence intervals
- On-chain data (exchange flows, funding rates) are leading indicators
- Crypto correlated with macro risk-on/risk-off

### Science/Tech
- Science replication: ~50-60% for surprising results
- Tech product launches: check company track record
"""


def upgrade() -> None:
    conn = op.get_bind()

    # Insert 6 trading skills
    now = "NOW()"

    skill_ids = []
    skills = [
        {
            "name": "trading-market-scanner",
            "description": "Scannt Polymarket auf aktive Prediction Markets mit hohem Volumen und interessanten Odds. Gibt Top-Märkte nach Kategorie sortiert zurück.",
            "content": SKILL_MARKET_SCANNER,
            "category": "TOOL",
            "roles": '["trading", "finance", "analyst"]',
            "manual_duration_seconds": 1800,
        },
        {
            "name": "trading-odds-analyzer",
            "description": "Recherchiert einen spezifischen Markt via News und WebSearch, schätzt eine eigene Wahrscheinlichkeit, berechnet Edge und Kelly-Position.",
            "content": SKILL_ODDS_ANALYZER,
            "category": "WORKFLOW",
            "roles": '["trading", "finance", "analyst"]',
            "manual_duration_seconds": 3600,
        },
        {
            "name": "trading-paper-portfolio",
            "description": "Verwaltet ein virtuelles Paper-Trading-Portfolio in einer JSON-Datei. Öffnet/schließt Positionen, berechnet P&L und zeigt Performance-Metriken.",
            "content": SKILL_PAPER_PORTFOLIO,
            "category": "TOOL",
            "roles": '["trading", "finance"]',
            "manual_duration_seconds": 900,
        },
        {
            "name": "trading-market-report",
            "description": "Generiert tägliche Market-Reports (morgens + abends) mit Top-5-Märkten und sendet sie per Telegram-Notification.",
            "content": SKILL_MARKET_REPORT,
            "category": "ROUTINE",
            "roles": '["trading", "finance", "analyst"]',
            "manual_duration_seconds": 2700,
        },
        {
            "name": "trading-crypto-sentiment",
            "description": "Kombiniert CoinGecko-Preisdaten, Reddit-Sentiment und Polymarket Crypto-Märkte um Divergenzen und Fehlpreisungen zu identifizieren.",
            "content": SKILL_CRYPTO_SENTIMENT,
            "category": "WORKFLOW",
            "roles": '["trading", "finance", "crypto"]',
            "manual_duration_seconds": 2700,
        },
        {
            "name": "trading-backtest-analyzer",
            "description": "Wöchentliche Performance-Analyse: Brier Score, Kalibrierung, P&L nach Kategorie. Hilft systematisch besser zu werden.",
            "content": SKILL_BACKTEST_ANALYZER,
            "category": "WORKFLOW",
            "roles": '["trading", "finance", "analyst"]',
            "manual_duration_seconds": 3600,
        },
    ]

    for skill in skills:
        result = conn.execute(
            text("""
                INSERT INTO skills (name, description, content, category, status, roles, paths,
                    manual_duration_seconds, usage_count, is_public, current_version,
                    improvement_status, created_at, updated_at)
                VALUES (:name, :description, :content, :category, 'ACTIVE', :roles, '[]',
                    :manual_duration_seconds, 0, true, 1,
                    'none', NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description,
                    content = EXCLUDED.content,
                    roles = EXCLUDED.roles,
                    manual_duration_seconds = EXCLUDED.manual_duration_seconds,
                    updated_at = NOW()
                RETURNING id
            """),
            {
                "name": skill["name"],
                "description": skill["description"],
                "content": skill["content"],
                "category": skill["category"],
                "roles": skill["roles"],
                "manual_duration_seconds": skill["manual_duration_seconds"],
            },
        )
        skill_ids.append(result.scalar())

    import json as _json
    skill_ids_json = _json.dumps(skill_ids)

    # Insert Trading Analyst template
    conn.execute(
        text("""
            INSERT INTO agent_templates (
                name, display_name, description, icon, category,
                model, role, permissions, integrations, mcp_server_ids, skill_ids,
                knowledge_template, claude_md, is_builtin, is_published, created_at, updated_at
            ) VALUES (
                'trading-analyst',
                'Trading Analyst',
                'Scannt Polymarket Prediction Markets, analysiert Odds, tracked Paper-Trades und verbessert Forecast-Genauigkeit. Kein Echtgeld-Trading — rein analytisch.',
                'TrendingUp',
                'finance',
                'claude-sonnet-4-6',
                'Du bist ein spezialisierter Prediction Market Analyst. Du scannst Märkte auf Polymarket, bildest eigene Wahrscheinlichkeitseinschätzungen durch Recherche, identifizierst Preisfehlstellungen (Edge) und trackst Paper-Trades. Dein Ziel ist kalibrierte Prognosefähigkeit — du willst systematisch besser werden. Kein Echtgeld-Trading.',
                '["package-install"]',
                '[]',
                '[]',
                :skill_ids,
                :knowledge_template,
                :claude_md,
                true,
                true,
                NOW(),
                NOW()
            )
            ON CONFLICT (name) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                skill_ids = EXCLUDED.skill_ids,
                knowledge_template = EXCLUDED.knowledge_template,
                claude_md = EXCLUDED.claude_md,
                is_published = EXCLUDED.is_published,
                updated_at = NOW()
        """),
        {
            "skill_ids": skill_ids_json,
            "knowledge_template": TRADING_KNOWLEDGE,
            "claude_md": TRADING_CLAUDE_MD,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DELETE FROM agent_templates WHERE name = 'trading-analyst'"))
    skill_names = [
        "trading-market-scanner", "trading-odds-analyzer", "trading-paper-portfolio",
        "trading-market-report", "trading-crypto-sentiment", "trading-backtest-analyzer",
    ]
    conn.execute(
        text("DELETE FROM skills WHERE name = ANY(:names)"),
        {"names": skill_names},
    )
