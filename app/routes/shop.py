"""Buyer-facing shop routes — with DynamoDB cart, Redis cache, SNS events."""
from decimal import Decimal
from flask import (
    Blueprint, abort, flash, redirect, render_template,
    request, session, url_for, current_app
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import select, or_
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length

from app.models import Category, Order, OrderItem, Product, db
from app.storage import get_storage
from app.notify import enqueue_order
from app.events import publish_order_placed, publish_low_stock
from app.metrics import put_metric
import app.cart as cart_store
from app.cache import cache_get, cache_set, cache_delete_pattern

bp = Blueprint("shop", __name__)

_CATALOG_TTL = 300   # 5 minutes cache on catalog
_LOW_STOCK   = 5     # alert seller below this


class CheckoutForm(FlaskForm):
    shipping_name = StringField("Full name",         validators=[DataRequired(), Length(max=200)])
    shipping_addr = TextAreaField("Shipping address", validators=[DataRequired(), Length(max=500)])
    submit        = SubmitField("Place order")


# ── Helpers ────────────────────────────────────────────────────────────────

def _uid() -> str:
    """Cart key — user id if logged in, else anonymous session id."""
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    if "anon_id" not in session:
        import uuid
        session["anon_id"] = str(uuid.uuid4())
    return f"anon:{session['anon_id']}"


def _is_seller() -> bool:
    return current_user.is_authenticated and (
        current_user.is_seller or current_user.is_admin
    )


def _seller_id() -> int | None:
    return current_user.id if _is_seller() else None


# ── Pages ──────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    # Try cache first
    cache_key = f"homepage:featured:{_seller_id()}"
    featured  = cache_get(cache_key)

    if featured is None:
        sid   = _seller_id()
        query = select(Product).where(Product.is_active == True, Product.stock > 0)
        if sid:
            query = query.where(Product.seller_id != sid)
        rows     = db.session.scalars(query.order_by(Product.created_at.desc()).limit(8)).all()
        featured = [p.id for p in rows]
        cache_set(cache_key, featured, ttl=_CATALOG_TTL)

    # Reload from DB to get full objects (cache stores IDs only)
    products   = [db.session.get(Product, pid) for pid in featured]
    products   = [p for p in products if p]
    categories = db.session.scalars(select(Category)).all()
    storage    = get_storage()
    return render_template("shop/index.html",
                           products=products, categories=categories, storage=storage)


@bp.route("/products")
def catalog():
    q        = request.args.get("q", "").strip()
    cat_slug = request.args.get("category", "")
    sid      = _seller_id()

    # Cache key encodes all filter params
    cache_key = f"catalog:{sid}:{q}:{cat_slug}"
    product_ids = cache_get(cache_key)

    if product_ids is None:
        query = select(Product).where(Product.is_active == True, Product.stock > 0)
        if sid:
            query = query.where(Product.seller_id != sid)
        if q:
            query = query.where(
                or_(Product.name.ilike(f"%{q}%"), Product.description.ilike(f"%{q}%"))
            )
        if cat_slug:
            cat = db.session.scalar(select(Category).where(Category.slug == cat_slug))
            if cat:
                query = query.where(Product.category_id == cat.id)
        rows        = db.session.scalars(query.order_by(Product.created_at.desc())).all()
        product_ids = [p.id for p in rows]
        cache_set(cache_key, product_ids, ttl=_CATALOG_TTL)

    products   = [db.session.get(Product, pid) for pid in product_ids]
    products   = [p for p in products if p]
    categories = db.session.scalars(select(Category)).all()
    storage    = get_storage()
    return render_template("shop/catalog.html",
                           products=products, categories=categories,
                           q=q, cat_slug=cat_slug, storage=storage)


@bp.route("/products/<int:product_id>")
def product_detail(product_id: int):
    product = db.session.get(Product, product_id)
    if not product or not product.is_active:
        abort(404)
    is_own  = (current_user.is_authenticated and product.seller_id == current_user.id)
    storage = get_storage()
    return render_template("shop/product_detail.html",
                           product=product, storage=storage, is_own=is_own)


# ── Cart ───────────────────────────────────────────────────────────────────

@bp.route("/cart")
def cart():
    data  = cart_store.get_cart(_uid())
    total = cart_store.cart_total(data)
    return render_template("shop/cart.html", cart=data, total=total)


@bp.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id: int):
    product = db.session.get(Product, product_id)
    if not product or not product.is_active or product.stock < 1:
        flash("Product not available.", "danger")
        return redirect(url_for("shop.catalog"))

    if current_user.is_authenticated and product.seller_id == current_user.id:
        flash("You cannot purchase your own product.", "warning")
        return redirect(url_for("shop.product_detail", product_id=product_id))

    cart_store.add_item(
        user_id    = _uid(),
        product_id = str(product_id),
        name       = product.name,
        price      = product.price,
        qty        = 1,
        image_key  = product.image_key or "",
    )
    flash(f"'{product.name}' added to cart.", "success")
    return redirect(request.referrer or url_for("shop.catalog"))


@bp.route("/cart/remove/<int:product_id>", methods=["POST"])
def remove_from_cart(product_id: int):
    cart_store.remove_item(_uid(), str(product_id))
    return redirect(url_for("shop.cart"))


@bp.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart(product_id: int):
    qty = request.form.get("qty", 1, type=int)
    cart_store.update_item(_uid(), str(product_id), qty)
    return redirect(url_for("shop.cart"))


# ── Checkout ───────────────────────────────────────────────────────────────

@bp.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    data = cart_store.get_cart(_uid())
    if not data:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("shop.catalog"))

    form = CheckoutForm()
    if form.validate_on_submit():
        total = cart_store.cart_total(data)
        order = Order(
            buyer_id     = current_user.id,
            status       = "pending",
            total_amount = total,
            shipping_name= form.shipping_name.data,
            shipping_addr= form.shipping_addr.data,
        )
        db.session.add(order)
        db.session.flush()  # get order.id

        seller_ids = []
        for pid_str, item in data.items():
            product = db.session.get(Product, int(pid_str))
            if not product:
                continue
            qty = item["qty"]
            product.stock = max(0, product.stock - qty)
            db.session.add(OrderItem(
                order_id   = order.id,
                product_id = product.id,
                quantity   = qty,
                unit_price = Decimal(str(item["price"])),
            ))
            if product.seller_id not in seller_ids:
                seller_ids.append(product.seller_id)

            # Low stock alert via SNS
            if product.stock < _LOW_STOCK:
                publish_low_stock(
                    product_id   = product.id,
                    product_name = product.name,
                    stock        = product.stock,
                    seller_email = product.seller.email,
                )

        db.session.commit()
        cart_store.clear_cart(_uid())

        # Invalidate catalog cache — stock changed
        cache_delete_pattern("catalog:*")
        cache_delete_pattern("homepage:*")

        # Publish OrderPlaced to SNS (fans out to SQS → Lambda → SES)
        publish_order_placed(
            order_id    = order.id,
            buyer_email = current_user.email,
            total       = float(total),
            seller_ids  = seller_ids,
        )

        # Also enqueue directly to SQS (belt-and-suspenders)
        enqueue_order(order.id)

        # Custom CloudWatch metric
        put_metric("OrdersPlaced", 1)
        put_metric("OrderRevenue", float(total), unit="None")

        flash(f"Order #{order.id} placed! Confirmation email on its way.", "success")
        return redirect(url_for("shop.order_detail", order_id=order.id))

    total = cart_store.cart_total(data)
    return render_template("shop/checkout.html", form=form, cart=data, total=total)


# ── Orders ─────────────────────────────────────────────────────────────────

@bp.route("/orders")
@login_required
def my_orders():
    orders = db.session.scalars(
        select(Order).where(Order.buyer_id == current_user.id)
        .order_by(Order.created_at.desc())
    ).all()
    return render_template("shop/orders.html", orders=orders)


@bp.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id: int):
    order = db.session.get(Order, order_id)
    if not order or order.buyer_id != current_user.id:
        abort(404)
    storage = get_storage()
    return render_template("shop/order_detail.html", order=order, storage=storage)
