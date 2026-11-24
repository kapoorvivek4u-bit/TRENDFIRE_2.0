# TradeReady

NSE F&O and equity scanner at **[tradeready.in](https://tradeready.in)**: market weather, sector rotation, setups (Squeeze Break, Swing Pullback, Trend Pullback, Overspill, chart patterns), Monthly/Weekly/Daily trend states, futures OI, unusual activity, FII footprint, deals and star investors.

## How to use the site

The full illustrated guide is at **[tradeready.in/guide](https://tradeready.in/guide)** (source: [`site/guide.html`](site/guide.html), screenshots in [`site/guide/`](site/guide)). It covers:

1. Getting started (sign in, install on a phone, update times)
2. The 5-minute daily routine
3. The Today dashboard: market clock, heads-up, index cards, Big money (OI quadrants)
4. Market weather: score bands, the six parts, the market line
5. The 7 strategies: rules, entry and exit for each
6. Scanner: filters, setup cards, stages, grades, trend tags
7. Every setup explained
8. Impulse table and the trend states (Impulse, Bullish, Topping, Bottoming, Bearish)
9. Index pages
10. Market pages: Sectors, Strongest, Unusual, FII footprint, Deals, Stars, Search
11. Pro tools: trade levels, Top 5 positional, Intraday & BTST, Scorecard, My holdings
12. Short forms A to Z (OI, PCR, VK, EMA, DMA, ATR, R:R, RS, VIX, BTST and more)
13. Common questions

## How it is built

- `site/` – the web app (single-page `index.html`, the guide, PWA files), served by the Cloudflare Worker in `worker.js`
- `*_scan.py` – scans run by GitHub Actions; results are committed to `data/*.json`
- `site_data.py` – builds `data/site.json`, the file the dashboard reads
- The Worker's cron (every 15 minutes) starts the scans on the IST timetable

TradeReady is for study and tracking only. It is not investment advice and not a SEBI-registered research service.
