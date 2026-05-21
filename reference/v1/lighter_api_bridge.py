#!/usr/bin/env python3
"""
Lighter API Bridge Server
Provides REST API for TypeScript trading bot to access Lighter data

Endpoints:
- GET  /api/markets           - List all markets
- GET  /api/ticker/:symbol    - Get latest price for symbol
- GET  /api/candles/:symbol/:interval  - Get candles (built from trades)
- GET  /api/health            - Health check
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import asyncio
import lighter
import logging
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
import os

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for TypeScript client

# Lighter client (global)
lighter_client = None
order_api = None
candlestick_api = None

# Cache for market metadata (symbol -> market_id mapping)
markets_cache = {}
last_cache_update = 0
CACHE_TTL = 300  # 5 minutes

def get_lighter_client():
    """Get or create Lighter API client (creates new event loop)"""
    import asyncio
    import ssl
    
    # Create new event loop for this thread
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    config = lighter.Configuration(
        host="https://mainnet.zklighter.elliot.ai"
    )
    
    # Disable SSL verification
    config.verify_ssl = False
    config.ssl_ca_cert = None
    
    client = lighter.ApiClient(configuration=config)
    
    return client

async def update_markets_cache():
    """Update markets cache"""
    global markets_cache, last_cache_update
    
    try:
        client = get_lighter_client()
        order_api_local = lighter.OrderApi(client)
        orderbooks = await order_api_local.order_books()
        
        markets_cache = {}
        for book in orderbooks.order_books:
            markets_cache[book.symbol.upper()] = {
                'market_id': book.market_id,
                'symbol': book.symbol,
                'market_type': book.market_type,
                'status': book.status
            }
        
        last_cache_update = datetime.now().timestamp()
        logger.info(f"✅ Markets cache updated: {len(markets_cache)} markets")
        await client.close()
        
    except Exception as e:
        logger.error(f"❌ Failed to update markets cache: {e}")
        try:
            await client.close()
        except:
            pass

def get_market_id(symbol: str) -> int:
    """Get market_id for symbol"""
    symbol = symbol.upper()
    
    # Update cache if stale
    if datetime.now().timestamp() - last_cache_update > CACHE_TTL:
        asyncio.run(update_markets_cache())
    
    if symbol in markets_cache:
        return markets_cache[symbol]['market_id']
    
    # Fallback: update cache and try again
    asyncio.run(update_markets_cache())
    
    if symbol in markets_cache:
        return markets_cache[symbol]['market_id']
    
    raise ValueError(f"Symbol {symbol} not found")

async def build_candles_from_trades(market_id: int, interval: str, limit: int = 250):
    """
    Try to get candles from Lighter API first
    If OHLC is None, fallback to building from recent trades
    """
    client = get_lighter_client()
    order_api_local = lighter.OrderApi(client)
    candlestick_api_local = lighter.CandlestickApi(client)
    
    # Try to get historical candles first
    import datetime
    now = int(datetime.datetime.now().timestamp())
    
    # Calculate start time based on interval
    interval_minutes = {
        '1m': 1,
        '5m': 5,
        '15m': 15,
        '1h': 60,
        '4h': 240,
        '1d': 1440
    }.get(interval, 1)
    
    start_time = now - (interval_minutes * 60 * limit)
    
    try:
        candles_response = await candlestick_api_local.candles(
            market_id=market_id,
            resolution=interval,
            start_timestamp=start_time,
            end_timestamp=now,
            count_back=limit
        )
        
        # Check if we have valid OHLC data
        if candles_response.c and len(candles_response.c) > 0:
            # Check first candle - if OHLC is not None, use API data
            first_candle = candles_response.c[0]
            if first_candle.o is not None:
                # API has OHLC - use it!
                result_candles = []
                for c in candles_response.c:
                    if c.o is not None and c.h is not None and c.l is not None and c.c is not None:
                        result_candles.append({
                            'timestamp': c.t,
                            'open': float(c.o),
                            'high': float(c.h),
                            'low': float(c.l),
                            'close': float(c.c),
                            'volume': float(c.v)
                        })
                
                await client.close()
                return result_candles
    except Exception as e:
        logger.warning(f"Candles API failed, fallback to trades: {e}")
    
    # Fallback: Build from recent trades
    trades_limit = min(100, limit * 5)
    trades_response = await order_api_local.recent_trades(market_id=market_id, limit=trades_limit)
    
    if not trades_response.trades:
        return []
    
    # Parse interval
    interval_minutes = {
        '1m': 1,
        '5m': 5,
        '15m': 15,
        '1h': 60,
        '4h': 240,
        '1d': 1440
    }.get(interval, 1)
    
    # Group trades by time bucket
    buckets = defaultdict(list)
    
    for trade in trades_response.trades:
        # Convert to minute bucket
        bucket_timestamp = (trade.timestamp // (interval_minutes * 60000)) * (interval_minutes * 60000)
        buckets[bucket_timestamp].append({
            'price': float(trade.price),
            'volume': float(trade.size),
            'timestamp': trade.timestamp
        })
    
    # Build candles
    candles = []
    
    for bucket_timestamp in sorted(buckets.keys()):
        trades_in_bucket = buckets[bucket_timestamp]
        
        # Sort by timestamp
        trades_in_bucket.sort(key=lambda x: x['timestamp'])
        
        prices = [t['price'] for t in trades_in_bucket]
        volumes = [t['volume'] for t in trades_in_bucket]
        
        candle = {
            'timestamp': bucket_timestamp,
            'open': prices[0],
            'high': max(prices),
            'low': min(prices),
            'close': prices[-1],
            'volume': sum(volumes)
        }
        
        candles.append(candle)
    
    await client.close()
    
    # Return latest 'limit' candles
    return candles[-limit:]

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'service': 'Lighter API Bridge',
        'timestamp': int(datetime.now().timestamp() * 1000),
        'markets_cached': len(markets_cache)
    })

@app.route('/api/markets', methods=['GET'])
def get_markets():
    """Get all available markets"""
    try:
        # Update cache if needed
        if datetime.now().timestamp() - last_cache_update > CACHE_TTL:
            asyncio.run(update_markets_cache())
        
        markets_list = list(markets_cache.values())
        
        return jsonify({
            'success': True,
            'data': markets_list,
            'count': len(markets_list)
        })
        
    except Exception as e:
        logger.error(f"❌ /api/markets error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/ticker/<symbol>', methods=['GET'])
def get_ticker(symbol: str):
    """Get latest ticker for symbol"""
    try:
        market_id = get_market_id(symbol)
        
        async def fetch_ticker():
            client = get_lighter_client()
            order_api_local = lighter.OrderApi(client)
            details_response = await order_api_local.order_book_details(market_id=market_id)
            detail = details_response.order_book_details[0]
            
            result = {
                'symbol': detail.symbol,
                'price': float(detail.last_trade_price),
                'volume24h': float(detail.daily_quote_token_volume),
                'change24h': float(detail.daily_price_change),
                'high24h': float(detail.daily_price_high),
                'low24h': float(detail.daily_price_low),
                'timestamp': int(datetime.now().timestamp() * 1000)
            }
            
            await client.close()
            return result
        
        ticker = asyncio.run(fetch_ticker())
        
        return jsonify({
            'success': True,
            'data': ticker
        })
        
    except Exception as e:
        logger.error(f"❌ /api/ticker/{symbol} error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/candles/<symbol>/<interval>', methods=['GET'])
def get_candles(symbol: str, interval: str):
    """
    Get candles for symbol
    Built from recent trades since Lighter API returns None for OHLC
    
    Query params:
    - limit: number of candles (default: 250)
    """
    try:
        market_id = get_market_id(symbol)
        limit = int(request.args.get('limit', 250))
        
        # Validate interval
        valid_intervals = ['1m', '5m', '15m', '1h', '4h', '1d']
        if interval not in valid_intervals:
            return jsonify({
                'success': False,
                'error': f'Invalid interval. Must be one of: {valid_intervals}'
            }), 400
        
        async def fetch_candles():
            return await build_candles_from_trades(market_id, interval, limit)
        
        candles = asyncio.run(fetch_candles())
        
        return jsonify({
            'success': True,
            'data': candles,
            'count': len(candles),
            'interval': interval,
            'symbol': symbol
        })
        
    except Exception as e:
        logger.error(f"❌ /api/candles/{symbol}/{interval} error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/recent-trades/<symbol>', methods=['GET'])
def get_recent_trades(symbol: str):
    """Get recent trades for symbol"""
    try:
        market_id = get_market_id(symbol)
        limit = int(request.args.get('limit', 100))
        
        async def fetch_trades():
            client = get_lighter_client()
            order_api_local = lighter.OrderApi(client)
            trades_response = await order_api_local.recent_trades(market_id=market_id, limit=limit)
            
            result = [{
                'price': float(trade.price),
                'size': float(trade.size),
                'side': 'SELL' if trade.is_maker_ask else 'BUY',
                'timestamp': trade.timestamp
            } for trade in trades_response.trades]
            
            await client.close()
            return result
        
        trades = asyncio.run(fetch_trades())
        
        return jsonify({
            'success': True,
            'data': trades,
            'count': len(trades)
        })
        
    except Exception as e:
        logger.error(f"❌ /api/recent-trades/{symbol} error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def main():
    """Start the API bridge server"""
    port = int(os.getenv('LIGHTER_BRIDGE_PORT', 5000))
    
    logger.info("🚀 Starting Lighter API Bridge Server")
    logger.info(f"   Port: {port}")
    logger.info(f"   Lighter API: https://mainnet.zklighter.elliot.ai")
    logger.info("")
    
    # Update markets cache on startup
    asyncio.run(update_markets_cache())
    
    logger.info("✅ Server ready!")
    logger.info("")
    logger.info("Available endpoints:")
    logger.info("  GET  /api/health")
    logger.info("  GET  /api/markets")
    logger.info("  GET  /api/ticker/:symbol")
    logger.info("  GET  /api/candles/:symbol/:interval")
    logger.info("  GET  /api/recent-trades/:symbol")
    logger.info("")
    
    # Start server
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True
    )

if __name__ == '__main__':
    main()
