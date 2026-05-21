"""
Shopping cart backed by DynamoDB.

Table schema:
  PK: user_id (String) — anonymous users use session ID
  SK: product_id (String)
  Attributes: name, price, qty, image_key
  TTL: expires_at (epoch) — carts auto-expire after 7 days

Locally (STORAGE_BACKEND != s3) falls back to Flask session so
development works without AWS.
"""
from __future__ import annotations
import logging
import os
import time
from decimal import Decimal
from typing import Optional

from flask import session

log = logging.getLogger(__name__)

_TABLE_NAME = os.environ.get("DYNAMODB_CART_TABLE", "shopcloud-cart")
_TTL_DAYS   = 7
_USE_DYNAMO = os.environ.get("STORAGE_BACKEND") == "s3"


def _table():
    import boto3
    ddb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    return ddb.Table(_TABLE_NAME)


def _cart_key(user_id: str) -> str:
    return str(user_id)


def _ttl() -> int:
    return int(time.time()) + _TTL_DAYS * 86400


# ── Public API ──────────────────────────────────────────────────────────────

def get_cart(user_id: str) -> dict:
    """Return cart dict {product_id: {name, price, qty, image_key}}."""
    if not _USE_DYNAMO:
        return session.get("cart", {})
    try:
        table    = _table()
        response = table.query(
            KeyConditionExpression="user_id = :uid",
            ExpressionAttributeValues={":uid": _cart_key(user_id)},
        )
        cart = {}
        for item in response.get("Items", []):
            cart[item["product_id"]] = {
                "name":      item["name"],
                "price":     str(item["price"]),
                "qty":       int(item["qty"]),
                "image_key": item.get("image_key", ""),
            }
        return cart
    except Exception as e:
        log.warning("DynamoDB get_cart failed: %s", e)
        return session.get("cart", {})


def add_item(user_id: str, product_id: str, name: str,
             price: Decimal, qty: int = 1, image_key: str = "") -> None:
    if not _USE_DYNAMO:
        cart = session.get("cart", {})
        if product_id in cart:
            cart[product_id]["qty"] = min(cart[product_id]["qty"] + qty, 99)
        else:
            cart[product_id] = {"name": name, "price": str(price),
                                 "qty": qty, "image_key": image_key}
        session["cart"] = cart
        session.modified = True
        return
    try:
        _table().put_item(Item={
            "user_id":    _cart_key(user_id),
            "product_id": str(product_id),
            "name":       name,
            "price":      price,
            "qty":        qty,
            "image_key":  image_key,
            "expires_at": _ttl(),
        })
    except Exception as e:
        log.warning("DynamoDB add_item failed: %s", e)


def update_item(user_id: str, product_id: str, qty: int) -> None:
    if not _USE_DYNAMO:
        cart = session.get("cart", {})
        if qty < 1:
            cart.pop(str(product_id), None)
        elif str(product_id) in cart:
            cart[str(product_id)]["qty"] = qty
        session["cart"] = cart
        session.modified = True
        return
    try:
        if qty < 1:
            remove_item(user_id, product_id)
            return
        _table().update_item(
            Key={"user_id": _cart_key(user_id), "product_id": str(product_id)},
            UpdateExpression="SET qty = :q, expires_at = :t",
            ExpressionAttributeValues={":q": qty, ":t": _ttl()},
        )
    except Exception as e:
        log.warning("DynamoDB update_item failed: %s", e)


def remove_item(user_id: str, product_id: str) -> None:
    if not _USE_DYNAMO:
        cart = session.get("cart", {})
        cart.pop(str(product_id), None)
        session["cart"] = cart
        session.modified = True
        return
    try:
        _table().delete_item(
            Key={"user_id": _cart_key(user_id), "product_id": str(product_id)}
        )
    except Exception as e:
        log.warning("DynamoDB remove_item failed: %s", e)


def clear_cart(user_id: str) -> None:
    if not _USE_DYNAMO:
        session["cart"] = {}
        session.modified = True
        return
    try:
        cart  = get_cart(user_id)
        table = _table()
        with table.batch_writer() as batch:
            for pid in cart:
                batch.delete_item(
                    Key={"user_id": _cart_key(user_id), "product_id": pid}
                )
    except Exception as e:
        log.warning("DynamoDB clear_cart failed: %s", e)


def cart_total(cart: dict) -> Decimal:
    total = Decimal("0")
    for item in cart.values():
        total += Decimal(str(item["price"])) * int(item["qty"])
    return total
