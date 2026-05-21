"""
Event publishing via SNS.

When a buyer places an order, we publish to an SNS topic.
SNS fans out to:
  - SQS order queue    (Lambda picks up → processes order → sends SES email)
  - seller email subs  (sellers get notified their product was bought)
  - CloudWatch metrics (order count for dashboards)

Locally this is a no-op. On AWS configure:
  SNS_ORDER_TOPIC_ARN = arn:aws:sns:eu-west-1:xxxx:shopcloud-orders
"""
from __future__ import annotations
import json
import logging
import os

log = logging.getLogger(__name__)

_ORDER_TOPIC = os.environ.get("SNS_ORDER_TOPIC_ARN", "")


def publish_order_placed(order_id: int, buyer_email: str,
                         total: float, seller_ids: list[int]) -> None:
    """
    Publish OrderPlaced event to SNS.
    SNS delivers to:
      1. SQS queue  → Lambda order processor (already built)
      2. Email subs → one per seller whose product was bought
    """
    if not _ORDER_TOPIC:
        log.info("SNS not configured — skipping event for order %s", order_id)
        return
    try:
        import boto3
        sns = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        message = {
            "order_id":    order_id,
            "buyer_email": buyer_email,
            "total":       total,
            "seller_ids":  seller_ids,
            "event":       "OrderPlaced",
        }
        sns.publish(
            TopicArn=_ORDER_TOPIC,
            Subject=f"New order #{order_id} placed on ShopCloud",
            Message=json.dumps(message),
            MessageAttributes={
                "event_type": {
                    "DataType":    "String",
                    "StringValue": "OrderPlaced",
                }
            }
        )
        log.info("SNS event published for order %s", order_id)
    except Exception as e:
        log.warning("SNS publish failed for order %s: %s", order_id, e)


def publish_low_stock(product_id: int, product_name: str,
                      stock: int, seller_email: str) -> None:
    """
    Alert seller when their product stock drops below 5.
    Published to same SNS topic with different event_type
    so we can filter with subscription filter policies.
    """
    if not _ORDER_TOPIC:
        return
    try:
        import boto3
        sns = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        sns.publish(
            TopicArn=_ORDER_TOPIC,
            Subject=f"Low stock alert: {product_name}",
            Message=json.dumps({
                "event":        "LowStock",
                "product_id":   product_id,
                "product_name": product_name,
                "stock":        stock,
                "seller_email": seller_email,
            }),
            MessageAttributes={
                "event_type": {
                    "DataType":    "String",
                    "StringValue": "LowStock",
                }
            }
        )
    except Exception as e:
        log.warning("SNS low-stock publish failed: %s", e)
