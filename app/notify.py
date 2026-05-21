"""Notification helpers.

Locally these are no-ops (just log). On AWS, SES sends emails and
SQS receives orders for async processing. Same Flask code either way.
"""
from __future__ import annotations
import json, logging, os
from flask import current_app

log = logging.getLogger(__name__)


def send_order_confirmation(order) -> None:
    """Send buyer an order confirmation email via SES."""
    sender = os.environ.get("SES_SENDER_EMAIL", "")
    if not sender:
        log.info("SES not configured — skipping email for order %s", order.id)
        return
    try:
        import boto3
        client = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        items_html = "".join(
            f"<tr><td>{item.product.name}</td>"
            f"<td>{item.quantity}</td>"
            f"<td>${item.unit_price}</td>"
            f"<td>${item.subtotal}</td></tr>"
            for item in order.items
        )
        body = f"""
        <h2>Order Confirmed — #{order.id}</h2>
        <p>Hi {order.buyer.full_name}, your order has been received.</p>
        <table border="1" cellpadding="6">
          <tr><th>Product</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr>
          {items_html}
        </table>
        <p><strong>Total: ${order.total_amount}</strong></p>
        <p>Shipping to: {order.shipping_addr}</p>
        """
        client.send_email(
            Source=sender,
            Destination={"ToAddresses": [order.buyer.email]},
            Message={
                "Subject": {"Data": f"ShopCloud — Order #{order.id} Confirmed"},
                "Body":    {"Html": {"Data": body}},
            },
        )
        log.info("Confirmation email sent for order %s", order.id)
    except Exception as e:
        log.warning("SES send failed for order %s: %s", order.id, e)


def enqueue_order(order_id: int) -> None:
    """Push order ID to SQS for async processing."""
    queue_url = os.environ.get("SQS_ORDER_QUEUE_URL", "")
    if not queue_url:
        log.info("SQS not configured — order %s will not be queued", order_id)
        return
    try:
        import boto3
        sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"order_id": order_id}),
        )
        log.info("Order %s enqueued to SQS", order_id)
    except Exception as e:
        log.warning("SQS enqueue failed for order %s: %s", order_id, e)
