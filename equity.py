import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from kite_trade import *
import datetime
import pandas as pd
from order_placer import place_order, place_order_market
from report_writer import write_excel

# shared state and kite clients - imported by Equity2.py and mutated here
import equity_state as _state


class Equity:
    def setEnctoken(self, encToken):
        _state.enctoken = encToken
        _state.kite = KiteApp(enctoken=encToken)
        _state.kiteNew = ZerodhaKiteTrade()

    def placeOrder(self, transactionType, additionalQty=0):
        print(f"Place Order Called {transactionType} with additional quantity {additionalQty}")
        return place_order_market(_state.kite, _state.data1, self.currentPostions, transactionType, additionalQty)

    def currentFund(self):
        _state.data1["common"]["availableFund"] = _state.kite.margins().get('equity').get('available').get("live_balance")

    def currentPostions(self):
        positions = _state.kite.positions()
        if positions is not None:
            currStk = next((item for item in positions['day']
                            if item['tradingsymbol'] == _state.data1["stock"]["stockName"]
                            and item['exchange'] == _state.data1["stock"]["exchange"]
                            and item['product'] == 'MIS'), None)
            if currStk is not None:
                _state.data1["common"]["availableQuantity"] = currStk['quantity']
                _state.data1["common"]["currentPnl"] = currStk['pnl']

    def setInstrumentToken(self):
        try:
            instrument_token = _state.kite.instruments(_state.data1["stock"]["exchange"], _state.data1["stock"]["stockName"])
            if not instrument_token:
                print("No instruments returned for", _state.data1["stock"]["stockName"])
                _state.data1["common"]["message"] = "Instrument not found"
                return

            token0 = instrument_token[0]
            if not isinstance(token0, dict) or "instrument_token" not in token0:
                print("Unexpected instrument format:", token0)
                _state.data1["common"]["message"] = "Instrument format error"
                return

            _state.data1["stock"]["instrumentToken"] = token0.get("instrument_token")

            from_datetime = datetime.datetime.now() - datetime.timedelta(days=5)
            to_datetime = datetime.datetime.now()

            history = _state.kite.historical_data(
                _state.data1["stock"]["instrumentToken"], from_datetime, to_datetime,
                "5minute", continuous=False, oi=False
            )

            if isinstance(history, list):
                history = pd.DataFrame(history)

            if history is None or len(history) == 0:
                print("No historical data returned for token", _state.data1["stock"]["instrumentToken"])
                _state.data1["common"]["message"] = "No history"
                return

            required_cols = {'open', 'high', 'low', 'close'}
            if not required_cols.issubset(set(history.columns)):
                print("History missing required columns:", history.columns)
                _state.data1["common"]["message"] = "History columns missing"
                return

            last = history.iloc[-1]
            _state.data1["trendInfo"]["a"] = (last['open'] + last['high'] + last['low'] + last['close']) / 4
            _state.data1["trendInfo"]["o"] = last['open']
            _state.data1["trendInfo"]["h"] = last['high']
            _state.data1["trendInfo"]["l"] = last['low']
            _state.data1["trendInfo"]["c"] = last['close']

            min_rows = 1
            if len(history) < min_rows:
                print("Not enough history rows for SuperTrend:", len(history))
                return

            length = 10
            factory = 3
            if _state.data1["stock"]["superTrend"] == "73":
                length = 7
                factory = 3

            trendInfo = _state.kiteNew.SuperTrend(history, length, factory)
            self.last_trend_df = trendInfo

            if 'STX' not in trendInfo.columns:
                print("SuperTrend result missing STX:", trendInfo.columns)
                _state.data1["common"]["message"] = "SuperTrend error"
                return

            if trendInfo["STX"].iloc[-1]:  # Uptrend
                if 'final_lb' in trendInfo.columns:
                    _state.data1["trendInfo"]["sl"] = trendInfo["final_lb"].iloc[-1]
                else:
                    print("final_lb missing in SuperTrend output")
            else:  # Downtrend
                if 'final_ub' in trendInfo.columns:
                    _state.data1["trendInfo"]["sl"] = trendInfo["final_ub"].iloc[-1]
                else:
                    print("final_ub missing in SuperTrend output")

            _state.data1["trendInfo"]["currentT"] = trendInfo["STX"].iloc[-1]

            if len(_state.data1["trendInfo"]["recentT"]) >= 3:
                _state.data1["trendInfo"]["recentT"].pop(0)
                _state.data1["trendInfo"]["recentT"].append(_state.data1["trendInfo"]["currentT"])
            else:
                _state.data1["trendInfo"]["recentT"].append(_state.data1["trendInfo"]["currentT"])

        except Exception as inst:
            print(type(inst))
            print(inst.args)
            print(inst)

    def setAllData(self):
        self.currentFund()
        self.currentPostions()
        self.setInstrumentToken()

    def decide_action(self, atr_multiplier=1.5, max_layers=2, max_risk_qty=None):
        """
        Decide next action based on data1 state and last_trend_df.
        Returns:
            dict: { action: 'BUY'|'SELL', reverse: bool, qty: int, stop: float, reason: str }
            or None when no action
        """
        trend_df = getattr(self, "last_trend_df", None)
        if trend_df is None:
            return None

        recent = _state.data1["trendInfo"]["recentT"]
        if len(recent) < 3:
            return None
        last3 = recent[-3:]
        if not (last3[0] == last3[1] == last3[2]):
            return None

        prev = _state.data1["trendInfo"]["previousT"]
        price = float(_state.data1["trendInfo"]["c"] or 0)
        planned = int(_state.data1["common"]["plannedQuantity"] or 1)
        avail_qty = int(_state.data1["common"]["availableQuantity"] or 0)

        atr_col = "ATR_10"
        atr_value = None
        if atr_col in trend_df.columns:
            try:
                atr_value = float(trend_df[atr_col].iloc[-1])
            except Exception:
                atr_value = None
        if atr_value is None and price > 0:
            atr_value = max(price * 0.002, 0.1)

        stop_dist = atr_value * atr_multiplier

        if avail_qty == 0 and (prev != last3[0] or prev == ""):
            if last3[0]:  # uptrend -> BUY
                stop = _state.data1["trendInfo"].get("sl") or max(price - stop_dist, 0)
                return {"action": "BUY", "reverse": False, "qty": planned, "stop": stop, "reason": "enter_long_confirmed"}
            else:  # downtrend -> SELL
                stop = _state.data1["trendInfo"].get("sl") or (price + stop_dist)
                return {"action": "SELL", "reverse": False, "qty": planned, "stop": stop, "reason": "enter_short_confirmed"}

        if avail_qty != 0 and last3[0] != prev:
            reverse_qty = min(abs(avail_qty) * 2, planned * max_layers)
            reverse_qty = max(int(reverse_qty), planned)
            if last3[0]:
                stop = _state.data1["trendInfo"].get("sl") or max(price - stop_dist, 0)
                return {"action": "BUY", "reverse": True, "qty": reverse_qty, "stop": stop, "reason": "reverse_to_long"}
            else:
                stop = _state.data1["trendInfo"].get("sl") or (price + stop_dist)
                return {"action": "SELL", "reverse": True, "qty": reverse_qty, "stop": stop, "reason": "reverse_to_short"}

        return None

    def writeExcel(self, dataObj):
        write_excel(dataObj, os.path.dirname(__file__))
