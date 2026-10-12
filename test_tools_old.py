from tools import *
print(stock_quote.invoke({"symbol": "RELIANCE.NS"}))
print(stock_quote.invoke({"symbol": "^NSEI"}))
print(commodity_price.invoke({"name": "gold"}))
print(forex_rate.invoke({"base": "USD", "quote": "INR"}))
print(mutual_fund_nav.invoke({"name": "parag parikh flexi cap direct growth"}))
print(financial_news.invoke({"query": "Sensex Nifty", "source": "economictimes"}))
print(calculator.invoke({"expression": "10000*(1+0.12)**5"}))