import argparse
import datetime as dt
import getpass
import os
from pathlib import Path

import pandas as pd

from kite_trade import KiteApp, ZerodhaKiteTrade


TOKEN_FILE = Path(__file__).resolve().parent / "zerodha.enctoken"


def load_token(refresh=False):
	if not refresh:
		token = os.environ.get("ZERODHA_ENCTOKEN", "").strip()
		if token:
			return token
		if TOKEN_FILE.exists():
			token = TOKEN_FILE.read_text(encoding="utf-8").strip()
			if token:
				return token

	token = getpass.getpass("Zerodha enctoken (input hidden): ").strip()
	if not token:
		raise ValueError("Zerodha enctoken must not be empty")
	TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
	with open(TOKEN_FILE, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as token_file:
		token_file.write(token)
	print(f"Token cached locally in plaintext: {TOKEN_FILE}")
	return token


def download_candles(symbol, start_date, end_date, refresh_token=False):
	token = load_token(refresh=refresh_token)
	kite = KiteApp(enctoken=token)
	instruments = kite.instruments("NSE", symbol.upper())
	if not instruments:
		raise ValueError(f"No NSE instrument found for {symbol}")

	warmup_start = start_date - dt.timedelta(days=20)
	candles = kite.historical_data(
		instruments[0]["instrument_token"],
		dt.datetime.combine(warmup_start, dt.time.min),
		dt.datetime.combine(end_date + dt.timedelta(days=1), dt.time.min),
		"minute",
		continuous=False,
		oi=False,
	)
	if not candles:
		raise ValueError("Kite returned no one-minute candles for the requested dates")
	return pd.DataFrame(candles)


def build_signal_frame(candles, start_date, end_date, period=7, multiplier=3.0):
	frame = candles.copy()
	frame.columns = [column.strip().lower() for column in frame.columns]
	if "date" not in frame.columns and "timestamp" in frame.columns:
		frame = frame.rename(columns={"timestamp": "date"})

	required = {"date", "open", "high", "low", "close"}
	missing = required.difference(frame.columns)
	if missing:
		raise ValueError(f"Candles are missing columns: {', '.join(sorted(missing))}")
	frame["date"] = pd.to_datetime(frame["date"], errors="raise")
	for column in ("open", "high", "low", "close"):
		frame[column] = pd.to_numeric(frame[column], errors="raise")
	frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
	if frame.empty:
		raise ValueError("No one-minute candles were provided")

	frame["five_minute_bucket"] = frame["date"].dt.floor("5min")
	five_minute = (
		frame.groupby("five_minute_bucket", sort=True)
		.agg(
			open=("open", "first"),
			high=("high", "max"),
			low=("low", "min"),
			close=("close", "last"),
		)
		.reset_index()
	)
	indicator = ZerodhaKiteTrade().SuperTrend(five_minute, period, multiplier)
	atr_column = f"ATR_{period}"
	bucket_indices = {
		bucket: index for index, bucket in enumerate(five_minute["five_minute_bucket"])
	}

	def update_supertrend(bucket_index, candle):
		high = candle["high"]
		low = candle["low"]
		close = candle["close"]
		midpoint = (high + low) / 2.0

		if bucket_index == 0:
			atr = high - low
			upper = midpoint + multiplier * atr
			lower = midpoint - multiplier * atr
			supertrend = upper
		else:
			previous = indicator.iloc[bucket_index - 1]
			previous_close = float(previous["close"])
			true_range = max(
				high - low,
				abs(high - previous_close),
				abs(low - previous_close),
			)
			previous_atr = float(previous[atr_column])
			atr = previous_atr + (true_range - previous_atr) / period
			basic_upper = midpoint + multiplier * atr
			basic_lower = midpoint - multiplier * atr
			previous_upper = float(previous["final_ub"])
			previous_lower = float(previous["final_lb"])
			upper = (
				basic_upper
				if basic_upper < previous_upper or previous_close > previous_upper
				else previous_upper
			)
			lower = (
				basic_lower
				if basic_lower > previous_lower or previous_close < previous_lower
				else previous_lower
			)

			if float(previous["ST"]) == previous_upper:
				supertrend = upper if close <= upper else lower
			else:
				supertrend = lower if close >= lower else upper

		return supertrend, close > supertrend

	rows = []
	current_bucket = None
	forming = None
	for _, row in frame.iterrows():
		timestamp = row["date"]
		bucket = row["five_minute_bucket"]
		minute_open = float(row["open"])
		minute_high = float(row["high"])
		minute_low = float(row["low"])
		minute_close = float(row["close"])

		if bucket != current_bucket:
			current_bucket = bucket
			forming = {
				"open": minute_open,
				"high": minute_high,
				"low": minute_low,
				"close": minute_close,
			}
		else:
			forming["high"] = max(forming["high"], minute_high)
			forming["low"] = min(forming["low"], minute_low)
			forming["close"] = minute_close

		supertrend, is_uptrend = update_supertrend(bucket_indices[bucket], forming)
		if start_date <= timestamp.date() <= end_date:
			rows.append({
				"timestamp": timestamp + dt.timedelta(minutes=1),
				"source_timestamp": timestamp,
				"five_minute_bucket": bucket,
				"minute_open": minute_open,
				"minute_high": minute_high,
				"minute_low": minute_low,
				"minute_close": minute_close,
				"chart_open": forming["open"],
				"chart_high": forming["high"],
				"chart_low": forming["low"],
				"chart_close": forming["close"],
				"supertrend": supertrend,
				"signal": "UP" if is_uptrend else "DOWN",
				"signal_bool": is_uptrend,
				"period": period,
				"multiplier": multiplier,
			})

	signals = pd.DataFrame(rows)
	if signals.empty:
		raise ValueError("No candles fell within the requested date range")
	return signals



def load_signal_frame(path):
	signals = pd.read_csv(path)
	signals.columns = [column.strip().lower() for column in signals.columns]
	required = {"timestamp", "minute_close"}
	missing = required.difference(signals.columns)
	if missing:
		raise ValueError(f"Signal CSV is missing columns: {', '.join(sorted(missing))}")
	if "signal_bool" not in signals.columns:
		if "signal" not in signals.columns:
			raise ValueError("Signal CSV must contain signal_bool or signal")
		signals["signal_bool"] = signals["signal"].astype(str).str.upper().eq("UP")
	else:
		def parse_signal(value):
			if isinstance(value, bool):
				return value
			text = str(value).strip().lower()
			if text in {"true", "1", "up"}:
				return True
			if text in {"false", "0", "down"}:
				return False
			raise ValueError(f"Invalid signal value: {value}")

		signals["signal_bool"] = signals["signal_bool"].map(parse_signal)

	signals["timestamp"] = pd.to_datetime(signals["timestamp"], errors="raise")
	signals["minute_close"] = pd.to_numeric(signals["minute_close"], errors="raise")
	return signals.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def build_order_frame(signals, quantity=1, entry_time=dt.time(9, 15), exit_time=dt.time(15, 10), symbol=""):
	if quantity <= 0:
		raise ValueError("quantity must be greater than zero")

	columns = [
		"order_no", "symbol", "timestamp", "order_side", "quantity", "price",
		"entry_price", "exit_price", "net_p&l",
		"signal", "confirmation_count", "position_before", "position_after", "reason",
	]
	orders = []
	order_number = 0

	for _, session in signals.groupby(signals["timestamp"].dt.date, sort=True):
		session = session.sort_values("timestamp")
		position = 0
		position_entry_price = None
		recent_signals = []
		closed_for_day = False

		for _, row in session.iterrows():
			timestamp = row["timestamp"]
			if timestamp.time() < entry_time:
				continue

			if timestamp.time() >= exit_time:
				if position:
					order_number += 1
					orders.append({
						"order_no": order_number,
						"symbol": symbol,
						"timestamp": timestamp,
						"order_side": "SELL" if position > 0 else "BUY",
						"quantity": abs(position),
						"price": float(row["minute_close"]),
						"entry_price": position_entry_price,
						"exit_price": float(row["minute_close"]),
						"net_p&l": (float(row["minute_close"]) - position_entry_price) * (1 if position > 0 else -1) * abs(position),
						"signal": "UP" if bool(row["signal_bool"]) else "DOWN",
						"confirmation_count": 0,
						"position_before": position,
						"position_after": 0,
						"reason": "end_of_day",
					})
					position = 0
					position_entry_price = None
				closed_for_day = True
				break

			recent_signals.append(bool(row["signal_bool"]))
			if len(recent_signals) > 3:
				recent_signals.pop(0)

			if len(recent_signals) < 3 or not (all(recent_signals) or not any(recent_signals)):
				continue

			target_position = quantity if recent_signals[-1] else -quantity
			if target_position == position:
				continue

			position_before = position
			order_delta = target_position - position
			order_number += 1
			orders.append({
				"order_no": order_number,
				"symbol": symbol,
				"timestamp": timestamp,
				"order_side": "BUY" if order_delta > 0 else "SELL",
				"quantity": abs(order_delta),
				"price": float(row["minute_close"]),
				"entry_price": position_entry_price if position_before else float(row["minute_close"]),
				"exit_price": float(row["minute_close"]) if position_before else None,
				"net_p&l": (float(row["minute_close"]) - position_entry_price) * (1 if position_before > 0 else -1) * abs(position_before) if position_before else None,
				"signal": "UP" if target_position > 0 else "DOWN",
				"confirmation_count": 3,
				"position_before": position_before,
				"position_after": target_position,
				"reason": "entry" if position_before == 0 else "reversal",
			})
			position = target_position
			position_entry_price = float(row["minute_close"])

		if position and not closed_for_day:
			eligible = session[session["timestamp"].dt.time < exit_time]
			if not eligible.empty:
				last = eligible.iloc[-1]
				order_number += 1
				orders.append({
					"order_no": order_number,
					"symbol": symbol,
					"timestamp": last["timestamp"],
					"order_side": "SELL" if position > 0 else "BUY",
					"quantity": abs(position),
					"price": float(last["minute_close"]),
					"entry_price": position_entry_price,
					"exit_price": float(last["minute_close"]),
					"net_p&l": (float(last["minute_close"]) - position_entry_price) * (1 if position > 0 else -1) * abs(position),
					"signal": "UP" if bool(last["signal_bool"]) else "DOWN",
					"confirmation_count": 0,
					"position_before": position,
					"position_after": 0,
					"reason": "last_available_signal",
				})

	return pd.DataFrame(orders, columns=columns)


def main():
	parser = argparse.ArgumentParser(description="Export SuperTrend signals or simulated order events.")
	parser.add_argument("--symbol", help="NSE trading symbol, for example LLOYDSME")
	parser.add_argument("--from-date", type=dt.date.fromisoformat)
	parser.add_argument("--to-date", type=dt.date.fromisoformat)
	parser.add_argument("--output", type=Path, help="Signal CSV output path (default: backtest.trades.csv)")
	parser.add_argument("--signals-csv", type=Path, help="Build order events from an existing signal CSV")
	parser.add_argument("--orders-output", type=Path, help="Order CSV output path (default: backtest.orders.csv)")
	parser.add_argument("--quantity", type=int, default=1, help="Target position size per direction")
	parser.add_argument("--entry-time", default="09:15")
	parser.add_argument("--exit-time", default="15:10")
	parser.add_argument("--refresh-token", action="store_true", help="Prompt for and replace the cached Zerodha enctoken")
	args = parser.parse_args()

	if args.signals_csv:
		if args.from_date or args.to_date or args.output:
			parser.error("--signals-csv cannot be combined with date or signal-output arguments")
		try:
			signals = load_signal_frame(args.signals_csv)
			orders = build_order_frame(
				signals,
				quantity=args.quantity,
				entry_time=pd.Timestamp(args.entry_time).time(),
				exit_time=pd.Timestamp(args.exit_time).time(),
				symbol=args.symbol or "",
			)
		except ValueError as error:
			parser.error(str(error))
		orders_output = args.orders_output or Path("backtest.orders.csv")
		orders.to_csv(orders_output, index=False)
		print(f"Orders exported: {len(orders)}")
		print(f"Net P&L (before brokerage and taxes): {orders['net_p&l'].sum():.2f}")
		print(f"Order CSV saved: {orders_output}")
		return

	if not args.symbol or not args.from_date or not args.to_date:
		parser.error("provide --signals-csv or all of --symbol, --from-date, and --to-date")
	if args.from_date > args.to_date:
		parser.error("--from-date must be on or before --to-date")

	candles = download_candles(args.symbol, args.from_date, args.to_date, refresh_token=args.refresh_token)
	signals = build_signal_frame(candles, args.from_date, args.to_date)
	output = args.output or Path("backtest.trades.csv")
	signals.to_csv(output, index=False)
	orders = build_order_frame(
		signals,
		quantity=args.quantity,
		entry_time=pd.Timestamp(args.entry_time).time(),
		exit_time=pd.Timestamp(args.exit_time).time(),
		symbol=args.symbol,
	)
	orders_output = args.orders_output or Path("backtest.orders.csv")
	orders.to_csv(orders_output, index=False)
	print(f"Minute candles exported: {len(signals)}")
	print(f"Signal CSV saved: {output}")
	print(f"Orders exported: {len(orders)}")
	print(f"Net P&L (before brokerage and taxes): {orders['net_p&l'].sum():.2f}")
	print(f"Order CSV saved: {orders_output}")


if __name__ == "__main__":
	main()
