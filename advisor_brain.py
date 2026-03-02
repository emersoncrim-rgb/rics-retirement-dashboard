import yfinance as yf
import google.generativeai as genai
import json
from datetime import datetime, timedelta

def get_market_context(tickers):
    """Pulls recent performance and news for the given tickers."""
    context_data = {}
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            # Get 1 year of historical data to calculate trends
            hist = t.history(period="1y")
            if hist.empty:
                continue
                
            current_price = hist['Close'].iloc[-1]
            price_1d_ago = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
            price_1m_ago = hist['Close'].iloc[-21] if len(hist) > 21 else hist['Close'].iloc[0] # Approx 21 trading days
            price_1y_ago = hist['Close'].iloc[0]
            
            pct_change_1d = ((current_price - price_1d_ago) / price_1d_ago) * 100
            pct_change_1m = ((current_price - price_1m_ago) / price_1m_ago) * 100
            pct_change_1y = ((current_price - price_1y_ago) / price_1y_ago) * 100
            
            # Get recent news headlines (limit to top 3)
            news = t.news[:3] if hasattr(t, 'news') else []
            headlines = [n.get('title', '') for n in news]
            
            context_data[ticker] = {
                "1_day_change_pct": round(pct_change_1d, 2),
                "1_month_change_pct": round(pct_change_1m, 2),
                "1_year_change_pct": round(pct_change_1y, 2),
                "recent_headlines": headlines
            }
        except Exception:
            pass # Skip ticker if yfinance fails
            
    return context_data

def generate_advisor_briefing(holdings, api_key):
    """Feeds market data to Gemini to get a fiduciary synthesis."""
    if not api_key:
        return []
        
    # Extract unique tickers from his holdings (excluding cash/MMF if possible)
    tickers = list(set([h.get("ticker", "").upper() for h in holdings if h.get("ticker") and h.get("asset_class") != "mmf"]))
    if not tickers:
        return []
        
    market_data = get_market_context(tickers)
    
    if not market_data:
        return []

    genai.configure(api_key=api_key)
    # Using the fast, cost-effective flash model
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = f"""
    You are a calm, reassuring fiduciary financial advisor speaking to your 71-year-old retired client. 
    Review the following market data (daily changes, 1-month trends, 1-year trends, and recent news) for the specific assets in his portfolio.
    
    Market Data:
    {json.dumps(market_data, indent=2)}
    
    Identify 1 to 3 of the most important things he should know today. 
    Focus on:
    1. Major daily drops or spikes (>5%).
    2. Significant long-term trends (e.g., "X has been up 20% this year").
    3. How recent news specifically impacts his holdings.
    
    Do NOT give generic market advice. Only talk about the tickers listed.
    Reassure him if something is down (e.g., "Dividends are still secure", "Bonds provide a cushion").
    
    Respond STRICTLY in JSON format as a list of dictionaries with the following keys:
    - "severity": "high" (for urgent >5% drops), "medium" (for news/monthly trends), or "low" (for positive long-term growth).
    - "ticker": The stock or ETF symbol.
    - "title": A short, plain-English headline (e.g., "Apple dips on supply chain news").
    - "advisor_note": 1-2 sentences explaining the impact and why he shouldn't worry or what it means for his retirement.
    
    Return ONLY the JSON array.
    """
    
    try:
        response = model.generate_content(prompt)
        # Clean up the markdown formatting Gemini sometimes adds to JSON outputs
        raw_text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw_text)
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return []

def get_latest_news(holdings, limit=5):
    """Fetches the latest raw news articles and URLs for the portfolio."""
    import yfinance as yf
    tickers = list(set([h.get("ticker", "").upper() for h in holdings if h.get("ticker") and h.get("asset_class") != "mmf"]))
    all_news = []
    for t in tickers:
        try:
            tick = yf.Ticker(t)
            news = tick.news
            for n in news:
                n['related_ticker'] = t
                all_news.append(n)
        except Exception:
            pass
            
    all_news.sort(key=lambda x: x.get('providerPublishTime', 0), reverse=True)
    
    seen_titles = set()
    unique_news = []
    for n in all_news:
        # Handle Yahoo Finance's recent API structure change
        if "content" in n and isinstance(n["content"], dict):
            title = n["content"].get("title", "")
            link = n["content"].get("clickThroughUrl", {}).get("url", "")
            publisher = n["content"].get("provider", {}).get("displayName", "Financial News")
        else:
            title = n.get("title", "")
            link = n.get("link", "")
            publisher = n.get("publisher", "Financial News")
            
        if not title or title in seen_titles:
            continue
            
        seen_titles.add(title)
        n["parsed_title"] = title
        n["parsed_link"] = link
        n["parsed_publisher"] = publisher
        unique_news.append(n)
        
        if len(unique_news) >= limit:
            break
    return unique_news
