import streamlit as st
import yfinance as yf
from strategies import sma_crossover, rsi_strategy, macd_strategy
from notifier import send_telegram
from alpaca_trader import place_order

symbols = st.sidebar.multiselect("Watchlist", ["AAPL", "TSLA", "MSFT", "NVDA"], default=["AAPL"])
strategy = st.sidebar.selectbox("Strategy", ["SMA", "RSI", "MACD"])
live_trading = st.sidebar.checkbox("🔴 Enable Live Trading (Alpaca)")
qty = st.sidebar.number_input("Trade Quantity", 1, 100, 1)

for symbol in symbols:
    df = yf.download(symbol, period="90d", interval="1h")

    if strategy == "SMA":
        df = sma_crossover(df)
        buy_signal = df['Position'].iloc[-1] == 1
        sell_signal = df['Position'].iloc[-1] == -1
    elif strategy == "RSI":
        df = rsi_strategy(df)
        buy_signal = df['Signal'].iloc[-1] == 1
        sell_signal = df['Signal'].iloc[-1] == -1
    elif strategy == "MACD":
        df = macd_strategy(df)
        buy_signal = df['Position'].iloc[-1] == 1
        sell_signal = df['Position'].iloc[-1] == -1

    # Display
    st.subheader(symbol)
    st.line_chart(df['Close'])

    # Alert + Execute
    if buy_signal:
        msg = f"📈 BUY SIGNAL: {symbol} - Strategy: {strategy}"
        send_telegram(msg)
        if live_trading:
            st.success(place_order(symbol, qty, "buy"))
    elif sell_signal:
        msg = f"📉 SELL SIGNAL: {symbol} - Strategy: {strategy}"
        send_telegram(msg)
        if live_trading:
            st.warning(place_order(symbol, qty, "sell"))
