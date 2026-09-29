import time


def place_order(kite, data1, positions_callback, transactionType, additionalQty=0):
    """
    Places an order with retry logic.

    Args:
        kite: KiteApp client instance.
        data1: Shared state dictionary.
        positions_callback: Callable that refreshes data1 positions (e.g. equity.currentPostions).
        transactionType: 'BUY' or 'SELL'.
        additionalQty: Extra quantity on top of plannedQuantity.

    Returns:
        dict with order_id and status, or None on failure.
    """
    avail_qty = data1["common"]["availableQuantity"]
    planned_qty = data1["common"]["plannedQuantity"]

    if avail_qty == 0 or avail_qty == -planned_qty or avail_qty == planned_qty:
        print(f"Placing {transactionType} order. Current available quantity: {avail_qty}, "
              f"planned quantity: {planned_qty}, additional quantity: {additionalQty}")
        try:
            limit_price = data1["trendInfo"]["l"] if transactionType == "BUY" else data1["trendInfo"]["h"]
        except Exception as e:
            print(f"Error determining limit price from trendInfo, falling back to MARKET order: {e}")
            limit_price = None

        max_retries = 10
        retry_count = 0
        order_id = None
        completed = False

        while retry_count < max_retries and not completed:
            try:
                print(f"Attempt {retry_count + 1}: Placing {transactionType} order at limit price: {limit_price}")

                order_id = kite.place_order(
                    variety=kite.VARIETY_REGULAR,
                    exchange=kite.EXCHANGE_NSE,
                    tradingsymbol=data1["stock"]["stockName"],
                    transaction_type=transactionType,
                    quantity=(planned_qty + additionalQty),
                    product=kite.PRODUCT_MIS,
                    order_type=kite.ORDER_TYPE_LIMIT if limit_price else kite.ORDER_TYPE_MARKET,
                    price=limit_price,
                    validity=None,
                    disclosed_quantity=None,
                    trigger_price=None,
                    squareoff=None,
                    stoploss=None,
                    trailing_stoploss=None,
                    tag="SuperTrendAlgo"
                )
                print(f"Order placed. ID: {order_id}")

                max_status_polls = 20
                poll_count = 0
                order_status = None
                while poll_count < max_status_polls:
                    time.sleep(2)
                    try:
                        order_history = kite.order_history(order_id)
                        order_status = order_history[-1].get("status", "").upper() if order_history else None
                    except Exception as poll_err:
                        print(f"Error polling order status: {poll_err}")
                        order_status = None
                    print(f"Order {order_id} status: {order_status} (poll {poll_count + 1}/{max_status_polls})")

                    if order_status == "SUCCESS":
                        completed = True
                        print(f"Order {order_id} completed successfully.")
                        break
                    elif order_status in ("REJECTED", "CANCELLED", "OPEN"):
                        print(f"Order {order_id} {order_status}. Will retry with adjusted price.")
                        break
                    else:
                        poll_count += 1

                if not completed:
                    if order_status not in ("REJECTED", "CANCELLED", "OPEN", None):
                        try:
                            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
                            print(f"Cancelled open order {order_id}. Retrying as MARKET order.")
                        except Exception as cancel_err:
                            print(f"Error cancelling order {order_id}: {cancel_err}")
                    order_id = None
                    limit_price = None
                    retry_count += 1

            except Exception as e:
                print(f"Order placement error: {e}")
                order_id = None
                limit_price = None
                retry_count += 1
                time.sleep(2)

        if not completed:
            print("Failed to place order after retries")
            data1["common"]["message"] = "Order placement failed after retries"
            return None

        positions_callback()
        print(f"availableQuantity after order placement: {data1['common']['availableQuantity']}")
        return {"order_id": order_id, "status": "success"}
    else:
        data1["common"]["message"] = "Quantity limit has been Reached Please check"
        print("Quantity limit has been Reached Please check")
        return None


def place_order_market(kite, data1, positions_callback, transactionType, additionalQty=0):
    """
    Places a MARKET order with retry logic (no limit price).

    Args:
        kite: KiteApp client instance.
        data1: Shared state dictionary.
        positions_callback: Callable that refreshes data1 positions (e.g. equity.currentPostions).
        transactionType: 'BUY' or 'SELL'.
        additionalQty: Extra quantity on top of plannedQuantity.

    Returns:
        dict with order_id and status, or None on failure.
    """
    avail_qty = data1["common"]["availableQuantity"]
    planned_qty = data1["common"]["plannedQuantity"]

    if avail_qty == 0 or avail_qty == -planned_qty or avail_qty == planned_qty:
        print(f"Placing MARKET {transactionType} order. Available qty: {avail_qty}, "
              f"planned qty: {planned_qty}, additional qty: {additionalQty}")
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=data1["stock"]["stockName"],
                transaction_type=transactionType,
                quantity=(planned_qty + additionalQty),
                product=kite.PRODUCT_MIS,
                order_type=kite.ORDER_TYPE_MARKET,
                price=None,
                validity=None,
                disclosed_quantity=None,
                trigger_price=None,
                squareoff=None,
                stoploss=None,
                trailing_stoploss=None,
                tag="SuperTrendAlgo"
            )
            print(f"Market order placed. ID: {order_id}")
        except Exception as e:
            print(f"Market order placement error: {e}")
            data1["common"]["message"] = "Market order placement failed"
            return None

        positions_callback()
        print(f"availableQuantity after market order: {data1['common']['availableQuantity']}")
        return {"order_id": order_id, "status": "success"}
    else:
        data1["common"]["message"] = "Quantity limit has been Reached Please check"
        print("Quantity limit has been Reached Please check")
        return None
