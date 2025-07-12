import alpaca_trade_api as tradeapi
from config import ALPACA_KEY, ALPACA_SECRET, ALPACA_BASE_URL

api = tradeapi.REST(ALPACA_KEY, ALPACA_SECRET, ALPACA_BASE_URL, api_version='v2')

def place_order(symbol: str, qty: int, side: str):
    api.submit_order(
        symbol=symbol,
        qty=qty,
        side=side,
        type='market',
        time_in_force='gtc'
    )
    return f"📤 {side.upper()} ORDER PLACED: {symbol} x{qty}"
