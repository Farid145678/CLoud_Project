"""
Lambda — REST API handler behind API Gateway.
Routes:
  GET /api/products          → paginated product list
  GET /api/products/{id}     → single product
  GET /api/categories        → all categories
  GET /api/health            → health check

Uses pg8000 (pure Python) instead of psycopg2 so the package
works on Lambda's Linux runtime no matter where it was zipped.
"""
from __future__ import annotations
import json
import os
import boto3
import pg8000.native

sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
_CF_DOMAIN = os.environ.get("CLOUDFRONT_DOMAIN", "")
_secret_cache = None


def get_secret():
    global _secret_cache
    if _secret_cache is None:
        _secret_cache = json.loads(
            sm.get_secret_value(SecretId="shopcloud/prod")["SecretString"]
        )
    return _secret_cache


def get_db():
    secret = get_secret()
    return pg8000.native.Connection(
        host=secret["RDS_PROXY_HOST"],
        database="shopcloud",
        user=secret["DB_USER"],
        password=secret["DB_PASSWORD"],
        port=5432,
        timeout=5,
    )


def response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def rows_as_dicts(conn):
    """Turn the last query's rows into dicts using conn.columns."""
    cols = [c["name"] for c in conn.columns]
    return cols


def lambda_handler(event, context):
    path = event.get("rawPath") or event.get("path", "/")
    method = event.get("requestContext", {}).get("http", {}).get("method") \
             or event.get("httpMethod", "GET")
    params = event.get("queryStringParameters") or {}

    # HTTP API includes the stage in rawPath (e.g. "/prod/api/products").
    # Normalize so route matching works regardless of stage name.
    stage = event.get("requestContext", {}).get("stage")
    if stage and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1:]
    # Also strip a trailing slash so "/api/products/" matches "/api/products"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Health — no DB needed
    if path == "/api/health":
        return response(200, {"status": "ok"})

    conn = get_db()
    try:
        # GET /api/categories
        if path == "/api/categories" and method == "GET":
            rows = conn.run("SELECT id, name, slug FROM categories ORDER BY name")
            cols = [c["name"] for c in conn.columns]
            cats = [dict(zip(cols, r)) for r in rows]
            return response(200, {"categories": cats})

        # GET /api/products
        if path == "/api/products" and method == "GET":
            page = max(1, int(params.get("page", 1)))
            per_page = min(50, int(params.get("per_page", 20)))
            offset = (page - 1) * per_page
            q = params.get("q", "")
            category = params.get("category", "")

            where = ["p.is_active = TRUE", "p.stock > 0"]
            args = {}
            if q:
                where.append("(p.name ILIKE :q OR p.description ILIKE :q)")
                args["q"] = f"%{q}%"
            if category:
                where.append("c.slug = :category")
                args["category"] = category
            where_sql = " AND ".join(where)

            list_sql = f"""
                SELECT p.id, p.name, p.description,
                       p.price, p.stock, p.image_key,
                       c.name AS category, c.slug AS category_slug,
                       u.full_name AS seller
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN users      u ON u.id = p.seller_id
                WHERE {where_sql}
                ORDER BY p.created_at DESC
                LIMIT :per_page OFFSET :offset
            """
            rows = conn.run(list_sql, per_page=per_page, offset=offset, **args)
            cols = [c["name"] for c in conn.columns]
            products = [dict(zip(cols, r)) for r in rows]

            for p in products:
                if p.get("image_key"):
                    thumb = p["image_key"].replace("images/original/", "images/thumb/")
                    p["image_url"] = f"https://{_CF_DOMAIN}/{thumb}" if _CF_DOMAIN else ""
                else:
                    p["image_url"] = ""

            count_sql = f"""
                SELECT COUNT(*) AS count FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE {where_sql}
            """
            count_rows = conn.run(count_sql, **args)
            total = count_rows[0][0]

            return response(200, {
                "products": products,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": -(-total // per_page),
            })

        # GET /api/products/{id}
        if path.startswith("/api/products/") and method == "GET":
            pid = path.split("/")[-1]
            rows = conn.run("""
                SELECT p.id, p.name, p.description,
                       p.price, p.stock, p.image_key,
                       c.name AS category,
                       u.full_name AS seller
                FROM products p
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN users      u ON u.id = p.seller_id
                WHERE p.id = :pid AND p.is_active = TRUE
            """, pid=pid)
            cols = [c["name"] for c in conn.columns]
            if not rows:
                return response(404, {"error": "Product not found"})
            product = dict(zip(cols, rows[0]))
            if product.get("image_key"):
                thumb = product["image_key"].replace("images/original/", "images/thumb/")
                product["image_url"] = f"https://{_CF_DOMAIN}/{thumb}" if _CF_DOMAIN else ""
            return response(200, {"product": product})

        return response(404, {"error": "Not found"})

    except Exception as e:
        print(f"API error: {e}")
        return response(500, {"error": "Internal server error"})
    finally:
        conn.close()
