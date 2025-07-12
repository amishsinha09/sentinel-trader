def sma_crossover(df, short=20, long=50):
    df['SMA_Short'] = df['Close'].rolling(short).mean()
    df['SMA_Long'] = df['Close'].rolling(long).mean()
    df['Signal'] = (df['SMA_Short'] > df['SMA_Long']).astype(int)
    df['Position'] = df['Signal'].diff()
    return df

def rsi_strategy(df, period=14, lower=30, upper=70):
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['Signal'] = 0
    df.loc[df['RSI'] < lower, 'Signal'] = 1
    df.loc[df['RSI'] > upper, 'Signal'] = -1
    return df

def macd_strategy(df):
    ema12 = df['Close'].ewm(span=12).mean()
    ema26 = df['Close'].ewm(span=26).mean()
    df['MACD'] = ema12 - ema26
    df['Signal_Line'] = df['MACD'].ewm(span=9).mean()
    df['Signal'] = (df['MACD'] > df['Signal_Line']).astype(int)
    df['Position'] = df['Signal'].diff()
    return df
