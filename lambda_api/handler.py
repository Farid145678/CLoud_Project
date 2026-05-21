"""
Lambda — REST API handler behind API Gateway.

Routes:
  GET /api/products          → paginated product list
  GET /api/products/{id}     → single product
  GET /api/categories        → all categories
  GET /api/health            → health check

API Gateway proxy integration: all routes come into one Lambda.
We dispatch on event['resource'] + event['httpMethod'].

On AWS:
  API Gateway HTTP API → Lambda proxy → this function
  Authorizer: none (public read-only API)
"""
from __future__ import annotations
import json
import os

import boto3
import psycopg2
from psycopg2.extras import RealDictCursor

sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
_CF_DOMAIN = os.environ.get("CLOUDFRONT_DOMAIN", "")


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


def response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    path   = event.get("rawPath") or event.get("path", "/")
    method = event.get("requestContext", {}).get("http", {}).get("method") \
             or event.get("httpMethod", "GET")
    params = event.get("queryStringParameters") or {}

    # Health
    if path == "/api/health":
        return response(200, {"status": "ok"})

    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # GET /api/categories
            if path == "/api/categories" and method == "GET":
                cur.execute("SELECT id, name, slug FROM categories ORDER BY name")
                return response(200, {"categories": cur.fetchall()})

            # GET /api/products
            if path == "/api/products" and method == "GET":
                page     = max(1, int(params.get("page", 1)))
                per_page = min(50, int(params.get("per_page", 20)))
                offset   = (page - 1) * per_page
                q        = params.get("q", "")
                category = params.get("category", "")

                where = ["p.is_active = TRUE", "p.stock > 0"]
                args  = []
                if q:
                    where.append("(p.name ILIKE %s OR p.description ILIKE %s)")
                    args += [f"%{q}%", f"%{q}%"]
                if category:
                    where.append("c.slug = %s")
                    args.append(category)

                where_sql = " AND ".join(where)
                cur.execute(f"""
                    SELECT p.id, p.name, p.description,
                           p.price, p.stock, p.image_key,
                           c.name AS category, c.slug AS category_slug,
                           u.full_name AS seller
                    FROM products p
                    LEFT JOIN categories c ON c.id = p.category_id
                    LEFT JOIN users      u ON u.id = p.seller_id
                    WHERE {where_sql}
                    ORDER BY p.created_at DESC
                    LIMIT %s OFFSET %s
                """, args + [per_page, offset])
                products = cur.fetchall()

                # Add image URLs
                for p in products:
                    if p["image_key"]:
                        thumb = p["image_key"].replace("images/original/", "images/thumb/")
                        p["image_url"] = f"https://{_CF_DOMAIN}/{thumb}" if _CF_DOMAIN else ""
                    else:
                        p["image_url"] = ""

                cur.execute(f"""
                    SELECT COUNT(*) FROM products p
                    LEFT JOIN categories c ON c.id = p.category_id
                    WHERE {where_sql}
                """, args)
                total = cur.fetchone()["count"]

                return response(200, {
                    "products":   products,
                    "page":       page,
                    "per_page":   per_page,
                    "total":      total,
                    "total_pages": -(-total // per_page),
                })

            # GET /api/products/{id}
            if path.startswith("/api/products/") and method == "GET":
                pid = path.split("/")[-1]
                cur.execute("""
                    SELECT p.id, p.name, p.description,
                           p.price, p.stock, p.image_key,
                           c.name AS category,
                           u.full_name AS seller
                    FROM products p
                    LEFT JOIN categories c ON c.id = p.category_id
                    LEFT JOIN users      u ON u.id = p.seller_id
                    WHERE p.id = %s AND p.is_active = TRUE
                """, (pid,))
                product = cur.fetchone()
                if not product:
                    return response(404, {"error": "Product not found"})
                if product["image_key"]:
                    thumb = product["image_key"].replace("images/original/", "images/thumb/")
                    product["image_url"] = f"https://{_CF_DOMAIN}/{thumb}" if _CF_DOMAIN else ""
                return response(200, {"product": product})

            return response(404, {"error": "Not found"})

    except Exception as e:
        print(f"API error: {e}")
        return response(500, {"error": "Internal server error"})
    finally:
        conn.close()
