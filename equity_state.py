import datetime

enctoken = ""
kite = None
kiteNew = None

data1 = {
    "stock": {
        "stockName": "GLENMARK",  # Need to Set
        "exchange": "NSE",
        "instrumentToken": "",
        "superTrend": "103",
        "stoploss": 100,
    },
    "common": {
        "dateTime": datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "message": "",
        "transactionType": "",
        "availableFund": 0,
        "availableQuantity": 0,
        "plannedQuantity": 1,  # Need to Set
        "currentPnl": 0,
    },
    "trendInfo": {
        "o": 0,
        "h": 0,
        "l": 0,
        "c": 0,
        "a": 0,
        "sl": 0,
        "recentT": [],
        "previousT": "",
        "currentT": "",
    },
    "StopAlgo": False,
    "exit_reason": "",
}
