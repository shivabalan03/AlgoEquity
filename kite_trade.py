import csv
import io
import datetime as dt
from typing import Any, Dict, List, Optional

import pandas as pd
import requests


class KiteApp:
    """Minimal Zerodha OMS client using enctoken auth.

    This mirrors the subset of methods/constants used by this project.
    """

    VARIETY_REGULAR = "regular"
    EXCHANGE_NSE = "NSE"
    PRODUCT_MIS = "MIS"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_MARKET = "MARKET"

    _OMS_BASE = "https://kite.zerodha.com/oms"
    _API_BASE = "https://api.kite.trade"

    def __init__(self, enctoken: str):
        if not enctoken:
            raise ValueError("enctoken is required")
        self.enctoken = enctoken
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"enctoken {enctoken}",
                "X-Kite-Version": "3",
                "User-Agent": "Mozilla/5.0",
            }
        )

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self._OMS_BASE}{path}"
        response = self.session.request(method=method, url=url, params=params, data=data, timeout=20)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status and status != "success":
            raise RuntimeError(payload.get("message") or "Kite API request failed")
        return payload.get("data")

    def margins(self) -> Dict[str, Any]:
        data = self._request("GET", "/user/margins")
        return data or {}

    def positions(self) -> Dict[str, Any]:
        data = self._request("GET", "/portfolio/positions")
        return data or {"day": [], "net": []}

    def historical_data(
        self,
        instrument_token: int,
        from_datetime: dt.datetime,
        to_datetime: dt.datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> List[Dict[str, Any]]:
        params = {
            "from": from_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "to": to_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "continuous": 1 if continuous else 0,
            "oi": 1 if oi else 0,
        }
        data = self._request(
            "GET",
            f"/instruments/historical/{instrument_token}/{interval}",
            params=params,
        )
        candles = (data or {}).get("candles", [])
        rows: List[Dict[str, Any]] = []
        for candle in candles:
            # [date, open, high, low, close, volume] (+oi optional)
            rows.append(
                {
                    "date": candle[0],
                    "open": candle[1],
                    "high": candle[2],
                    "low": candle[3],
                    "close": candle[4],
                    "volume": candle[5] if len(candle) > 5 else None,
                }
            )
        return rows

    def instruments(self, exchange: str, tradingsymbol: str) -> List[Dict[str, Any]]:
        # With enctoken auth, OMS endpoint is reliable. Keep API fallback for compatibility.
        urls = [
            f"{self._OMS_BASE}/instruments/{exchange}",
            f"{self._API_BASE}/instruments/{exchange}",
            f"{self._API_BASE}/instruments",
        ]

        last_error = None
        csv_text = None
        for url in urls:
            try:
                response = self.session.get(url, timeout=25)
                response.raise_for_status()
                csv_text = response.text
                break
            except requests.HTTPError as exc:
                last_error = exc
                continue

        if not csv_text:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Unable to fetch instruments list")

        reader = csv.DictReader(io.StringIO(csv_text))
        result = []
        for row in reader:
            if row.get("tradingsymbol") == tradingsymbol and row.get("exchange") == exchange:
                try:
                    row["instrument_token"] = int(row.get("instrument_token", 0))
                except Exception:
                    pass
                result.append(row)
        return result

    def place_order(self, variety: str, **kwargs: Any) -> str:
        data = self._request("POST", f"/orders/{variety}", data=kwargs)
        order_id = (data or {}).get("order_id")
        if not order_id:
            raise RuntimeError("Order placement failed: missing order_id")
        return order_id

    def order_history(self, order_id: str) -> List[Dict[str, Any]]:
        data = self._request("GET", f"/orders/{order_id}")
        return data or []

    def cancel_order(self, variety: str, order_id: str) -> Dict[str, Any]:
        data = self._request("DELETE", f"/orders/{variety}/{order_id}")
        return data or {}


class ZerodhaKiteTrade:
    """Indicator helpers used by the trading flow."""

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = (high - low).abs()
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / period, adjust=False).mean()

    def SuperTrend(self, df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
        if df is None or len(df) == 0:
            raise ValueError("Input dataframe is empty")
        for col in ("high", "low", "close"):
            if col not in df.columns:
                raise ValueError(f"Missing required column: {col}")

        out = df.copy().reset_index(drop=True)

        atr_col = f"ATR_{period}"
        out[atr_col] = self._atr(out, period)

        hl2 = (out["high"] + out["low"]) / 2.0
        out["basic_ub"] = hl2 + multiplier * out[atr_col]
        out["basic_lb"] = hl2 - multiplier * out[atr_col]

        final_ub = [0.0] * len(out)
        final_lb = [0.0] * len(out)
        st = [0.0] * len(out)
        stx = [False] * len(out)

        if len(out) > 0:
            final_ub[0] = float(out.loc[0, "basic_ub"])
            final_lb[0] = float(out.loc[0, "basic_lb"])
            st[0] = final_ub[0]
            stx[0] = bool(out.loc[0, "close"] > st[0])

        for i in range(1, len(out)):
            prev_close = float(out.loc[i - 1, "close"])

            curr_basic_ub = float(out.loc[i, "basic_ub"])
            curr_basic_lb = float(out.loc[i, "basic_lb"])

            prev_final_ub = final_ub[i - 1]
            prev_final_lb = final_lb[i - 1]

            final_ub[i] = curr_basic_ub if (curr_basic_ub < prev_final_ub or prev_close > prev_final_ub) else prev_final_ub
            final_lb[i] = curr_basic_lb if (curr_basic_lb > prev_final_lb or prev_close < prev_final_lb) else prev_final_lb

            prev_st = st[i - 1]
            close_i = float(out.loc[i, "close"])

            if prev_st == prev_final_ub:
                st[i] = final_ub[i] if close_i <= final_ub[i] else final_lb[i]
            else:
                st[i] = final_lb[i] if close_i >= final_lb[i] else final_ub[i]

            stx[i] = bool(close_i > st[i])

        out["final_ub"] = final_ub
        out["final_lb"] = final_lb
        out["ST"] = st
        out["STX"] = stx
        return out
