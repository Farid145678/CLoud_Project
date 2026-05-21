"""Push custom metrics to CloudWatch.
No-op locally (boto3 not configured), active on AWS.
"""
import logging
import os

log = logging.getLogger(__name__)

def put_metric(metric_name: str, value: float, unit: str = "Count") -> None:
    """Push a single metric to CloudWatch namespace ShopCloud/App."""
    if os.environ.get("STORAGE_BACKEND") != "s3":
        return  # Skip locally
    try:
        import boto3
        cw = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        cw.put_metric_data(
            Namespace="ShopCloud/App",
            MetricData=[{
                "MetricName": metric_name,
                "Value":      value,
                "Unit":       unit,
            }]
        )
    except Exception as e:
        log.warning("CloudWatch metric failed: %s", e)
