# algo-trader-bot

## Setup

1. Clone or unzip this repo.

2. Install dependencies:

```
pip install -r requirements.txt
```

3. Fill your API keys in `config.py`:

```python
TELEGRAM_BOT_TOKEN = "your_telegram_bot_token_here"
TELEGRAM_CHAT_ID = "your_telegram_chat_id_here"

ALPACA_KEY = "your_alpaca_key_here"
ALPACA_SECRET = "your_alpaca_secret_here"
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
```

4. Run the dashboard:

```
streamlit run main.py
```

---

## Features

- Multi-stock watchlist  
- Choose strategy: SMA, RSI, MACD  
- Live trading with Alpaca  
- Real-time Telegram alerts  

---

## Notes

- Use Alpaca paper trading API for testing  
- Telegram bot must be created and chat ID retrieved to receive alerts  
- Customize strategies in `strategies.py`
