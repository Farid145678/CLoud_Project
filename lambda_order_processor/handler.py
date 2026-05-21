"""
Lambda — SQS consumer for order processing.
Triggered by shopcloud-orders queue.
Updates order status to 'processing', sends SES confirmation.
"""
import json
import os
import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

ses = boto3.client("ses", region_name=os.environ["AWS_REGION"])
sm  = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])

def get_db_conn():
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
    for record in event["Records"]:
        body     = json.loads(record["body"])
        order_id = body["order_id"]
        print(f"Processing order {order_id}")

        conn = get_db_conn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get order + buyer info
                cur.execute("""
                    SELECT o.id, o.total_amount, o.shipping_addr,
                           o.shipping_name, u.email, u.full_name
                    FROM orders o
                    JOIN users u ON u.id = o.buyer_id
                    WHERE o.id = %s
                """, (order_id,))
                order = cur.fetchone()

                if not order:
                    print(f"Order {order_id} not found — skipping")
                    continue

                # Get order items
                cur.execute("""
                    SELECT p.name, oi.quantity, oi.unit_price
                    FROM order_items oi
                    JOIN products p ON p.id = oi.product_id
                    WHERE oi.order_id = %s
                """, (order_id,))
                items = cur.fetchall()

                # Update status to processing
                cur.execute(
                    "UPDATE orders SET status='processing' WHERE id=%s",
                    (order_id,)
                )
                conn.commit()

            # Send confirmation email via SES
            items_html = "".join(
                f"<tr><td>{i['name']}</td>"
                f"<td>{i['quantity']}</td>"
                f"<td>${float(i['unit_price']):.2f}</td>"
                f"<td>${float(i['unit_price']) * i['quantity']:.2f}</td></tr>"
                for i in items
            )
            ses.send_email(
                Source=os.environ["SES_SENDER_EMAIL"],
                Destination={"ToAddresses": [order["email"]]},
                Message={
                    "Subject": {"Data": f"ShopCloud — Order #{order_id} Confirmed"},
                    "Body": {"Html": {"Data": f"""
                    <h2>Order #{order_id} Confirmed</h2>
                    <p>Hi {order['full_name']}, your order is now being processed.</p>
                    <table border='1' cellpadding='6' style='border-collapse:collapse'>
                      <tr><th>Product</th><th>Qty</th><th>Price</th><th>Subtotal</th></tr>
                      {items_html}
                    </table>
                    <p><strong>Total: ${float(order['total_amount']):.2f}</strong></p>
                    <p>Shipping to: {order['shipping_addr']}</p>
                    <p>Thank you for shopping with ShopCloud!</p>
                    """}}
                }
            )
            print(f"Email sent for order {order_id} to {order['email']}")

        except Exception as e:
            print(f"Error processing order {order_id}: {e}")
            conn.rollback()
            raise  # Re-raise so SQS retries, eventually goes to DLQ
        finally:
            conn.close()

    return {"statusCode": 200}
