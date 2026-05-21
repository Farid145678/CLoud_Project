"""
Lambda — triggered by EventBridge every day at 08:00 UTC.
Queries RDS for yesterday's sales and emails a summary to admin via SES.

EventBridge rule:
  Schedule: cron(0 8 * * ? *)
  Target:   this Lambda
"""
import json
import os
from datetime import datetime, timedelta, timezone

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

ses = boto3.client("ses",             region_name=os.environ["AWS_REGION"])
sm  = boto3.client("secretsmanager",  region_name=os.environ["AWS_REGION"])


def get_db():
    secret = json.loads(
        sm.get_secret_value(SecretId="shopcloud/prod")["SecretString"]
    )
    return psycopg2.connect(
        host     = secret["RDS_PROXY_HOST"],
        dbname   = "shopcloud",
        user     = secret["DB_USER"],
        password = secret["DB_PASSWORD"],
        connect_timeout = 5,
    )


def lambda_handler(event, context):
    today     = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Total orders and revenue yesterday
            cur.execute("""
                SELECT COUNT(*)           AS order_count,
                       COALESCE(SUM(total_amount), 0) AS revenue,
                       COUNT(DISTINCT buyer_id)  AS unique_buyers
                FROM orders
                WHERE DATE(created_at) = %s
                  AND status != 'cancelled'
            """, (yesterday,))
            summary = cur.fetchone()

            # Top 5 products yesterday
            cur.execute("""
                SELECT p.name,
                       SUM(oi.quantity)   AS units_sold,
                       SUM(oi.quantity * oi.unit_price) AS revenue
                FROM order_items oi
                JOIN orders  o ON o.id = oi.order_id
                JOIN products p ON p.id = oi.product_id
                WHERE DATE(o.created_at) = %s
                  AND o.status != 'cancelled'
                GROUP BY p.id, p.name
                ORDER BY revenue DESC
                LIMIT 5
            """, (yesterday,))
            top_products = cur.fetchall()

            # New users yesterday
            cur.execute("""
                SELECT COUNT(*) AS new_users
                FROM users
                WHERE DATE(created_at) = %s
            """, (yesterday,))
            new_users = cur.fetchone()["new_users"]

    finally:
        conn.close()

    # Build email
    top_rows = "".join(
        f"<tr><td>{p['name']}</td><td>{p['units_sold']}</td>"
        f"<td>${float(p['revenue']):.2f}</td></tr>"
        for p in top_products
    ) or "<tr><td colspan='3'>No sales yesterday</td></tr>"

    html = f"""
    <h2>ShopCloud Daily Report — {yesterday}</h2>
    <table border='1' cellpadding='8' style='border-collapse:collapse'>
      <tr><th>Orders</th><td>{summary['order_count']}</td></tr>
      <tr><th>Revenue</th><td>${float(summary['revenue']):.2f}</td></tr>
      <tr><th>Unique buyers</th><td>{summary['unique_buyers']}</td></tr>
      <tr><th>New users</th><td>{new_users}</td></tr>
    </table>

    <h3>Top Products</h3>
    <table border='1' cellpadding='8' style='border-collapse:collapse'>
      <tr><th>Product</th><th>Units sold</th><th>Revenue</th></tr>
      {top_rows}
    </table>
    <p><small>Sent automatically by ShopCloud · EventBridge cron(0 8 * * ? *)</small></p>
    """

    ses.send_email(
        Source      = os.environ["SES_SENDER_EMAIL"],
        Destination = {"ToAddresses": [os.environ["ADMIN_EMAIL"]]},
        Message     = {
            "Subject": {"Data": f"ShopCloud Daily Report — {yesterday}"},
            "Body":    {"Html": {"Data": html}},
        }
    )
    print(f"Daily report sent for {yesterday}: "
          f"{summary['order_count']} orders, ${float(summary['revenue']):.2f}")
    return {"statusCode": 200}
