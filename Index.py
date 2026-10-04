import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import datetime
import json
from flask import *
from equity import Equity
from equity_state import data1
import equity_state as _state

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="IST")

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('Index.html')

@app.route('/getDataEQ2', methods=['POST'])
def mainMethod():
    global payload
    payload = request.get_json(force=False, silent=True)
    print('Received data:', json.dumps(payload))

    if payload is None:
        return json.dumps({"error": "invalid or missing JSON payload"})

    data1["stock"]["stockName"] = payload.get('stock')
    data1["common"]["plannedQuantity"] = int(payload.get('quantity'))
    data1["stock"]["superTrend"] = payload.get('sprTrend')
    data1["stock"]["stoploss"] = -abs(int(payload.get('stoploss')))

    equity = Equity()
    if _state.enctoken == "" and payload.get('encToken'):
        equity.setEnctoken(payload.get('encToken'))
        print('SET ENCTOKEN ONE TIME')

    try:                        
        equity.setAllData() 
        now_ist = datetime.datetime.now(IST)
        data1["common"]["dateTime"] = now_ist.strftime("%Y-%m-%d %I:%M:%S %p")
        data1["common"]["message"] = ""
        data1["common"]["transactionType"] = ""

        # Trading flag is TRUE only during market hours and when stop-loss is not hit.
        current_time = now_ist.time()
        exit_time = datetime.time(15, 10)  # 3:10 PM
        enter_time = datetime.time(9, 15)  # 9:15 AM

        stop_loss_triggered = data1["common"]["currentPnl"] < data1["stock"]["stoploss"]
        is_market_hours = enter_time <= current_time < exit_time
        trade_flag = is_market_hours and not stop_loss_triggered

        # Keep existing behavior: StopAlgo=True means do not trade.
        data1["StopAlgo"] = not trade_flag
        time_exit_triggered = current_time >= exit_time
        time_entry_triggered = current_time < enter_time

        print("trend Info:", data1["trendInfo"])
        print(f"Flags - Trade: {trade_flag}, Stop Loss: {stop_loss_triggered}, Time Exit: {time_exit_triggered}, Time Entry: {time_entry_triggered}")
        if data1["StopAlgo"]:
            if stop_loss_triggered:
                exit_reason = "Stop Loss Reached"
            elif time_exit_triggered:
                exit_reason = "3:10 PM - Market Exit Time"
            elif time_entry_triggered:
                exit_reason = "Before 9:15 AM - Market Not Open"
            else:
                exit_reason = "Position Active / Still Trading"  
            data1["exit_reason"] = exit_reason       
            if data1["common"]["availableQuantity"] != 0:
                if data1["common"]["availableQuantity"] > 0:
                    equity.placeOrder("SELL")
                    data1["common"]["transactionType"] = "SELL"
                    data1["common"]["message"] = f"{exit_reason} - Closing Long Position"
                else:
                    equity.placeOrder("BUY")
                    data1["common"]["transactionType"] = "BUY"
                    data1["common"]["message"] = f"{exit_reason} - Closing Short Position"

        # use decide_action to centralize decision logic
        if not data1["StopAlgo"]:
            decision = equity.decide_action()
        else:
            decision = None
        print('Decision:', decision)
        if decision:
            # place order according to decision
            if decision.get("reverse"):
                # additionalQty will add plannedQuantity (keeps existing placeOrder signature)  
                equity.placeOrder(decision["action"], additionalQty=data1["common"]["plannedQuantity"])                
            else:
                equity.placeOrder(decision["action"])                

            data1["trendInfo"]["previousT"] = data1["trendInfo"]["currentT"]
            data1["common"]["message"] = f"{decision['action']} Order Placed ({decision.get('reason')})"
            data1["common"]["transactionType"] = decision["action"]
        else:
            data1["common"]["message"] = "No action (no confirmed signal or insufficient state)"
            print("No Trend Change or insufficient confirmation")
    except Exception as inst:
        print(type(inst))    # the exception type
        print(inst.args)     # arguments stored in .args
        print(inst)          # __str__ allows args to be printed directly,
    finally:        
        print("------------------------------------------------------------")
        report = {"dateTime": data1["common"]["dateTime"],
                      "stock": data1["stock"]["stockName"],
                      "trend": data1["trendInfo"]["currentT"],
                      "open": data1["trendInfo"]["o"],
                      "high": data1["trendInfo"]["h"],
                      "low": data1["trendInfo"]["l"],
                      "close": data1["trendInfo"]["c"],
                      "average": data1["trendInfo"]["a"],
                      "stopLoss": data1["trendInfo"]["sl"],
                      "transactionType": data1["common"]["transactionType"],
                      "message": data1["common"]["message"],
                      "availableQty": data1["common"]["availableQuantity"],
                      "currentPnl": data1["common"]["currentPnl"],
                      "stopAlgo": data1["StopAlgo"],
                      "exitReason": data1["exit_reason"]
                      }
        equity.writeExcel(report)
        return json.dumps(data1, default=str)

if __name__ == '__main__':    
    app.run(debug=True, port=5002)