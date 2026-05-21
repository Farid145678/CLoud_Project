"""Seller routes — require seller or admin role."""
import os, uuid
from decimal import Decimal
from io import BytesIO

from flask import (
    Blueprint, abort, current_app, flash,
    redirect, render_template, request, url_for
)
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from sqlalchemy import select
from wtforms import (
    BooleanField, DecimalField, IntegerField,
    SelectField, StringField, SubmitField, TextAreaField
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models import Category, Order, OrderItem, Product, db
from app.cache import cache_delete_pattern
from app.storage import get_storage

bp = Blueprint("seller", __name__, url_prefix="/seller")


def seller_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_seller and not current_user.is_admin):
            abort(403)
        return fn(*args, **kwargs)
    return login_required(wrapper)


class ProductForm(FlaskForm):
    name        = StringField("Product name", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[Length(max=5000)])
    price       = DecimalField("Price ($)", validators=[DataRequired(), NumberRange(min=0.01)], places=2)
    stock       = IntegerField("Stock quantity", validators=[DataRequired(), NumberRange(min=0)], default=1)
    category_id = SelectField("Category", coerce=int, validators=[Optional()])
    image       = FileField("Product image", validators=[
        FileAllowed(["jpg", "jpeg", "png", "webp"], "Images only")
    ])
    is_active   = BooleanField("Active (visible to buyers)", default=True)
    submit      = SubmitField("Save product")


def _category_choices():
    cats = db.session.scalars(select(Category)).all()
    return [(0, "— No category —")] + [(c.id, c.name) for c in cats]


def _save_image(file_storage) -> str | None:
    """Upload original to images/original/ — Lambda will generate the thumbnail."""
    if not file_storage or not file_storage.filename:
        return None
    try:
        from PIL import Image
        img = Image.open(file_storage.stream)
        # Keep original resolution for Lambda to work with
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        key = f"images/original/{uuid.uuid4().hex}.jpg"
        get_storage().put(key, buf.read(), content_type="image/jpeg")
        return key
    except Exception as e:
        current_app.logger.warning("Image save failed: %s", e)
        return None


@bp.route("/")
@seller_required
def dashboard():
    products = db.session.scalars(
        select(Product).where(Product.seller_id == current_user.id)
        .order_by(Product.created_at.desc())
    ).all()
    storage = get_storage()
    return render_template("seller/dashboard.html", products=products, storage=storage)


@bp.route("/orders")
@seller_required
def seller_orders():
    """All orders that contain at least one of this seller's products."""
    order_ids = db.session.scalars(
        select(OrderItem.order_id)
        .join(Product)
        .where(Product.seller_id == current_user.id)
        .distinct()
    ).all()
    orders = db.session.scalars(
        select(Order).where(Order.id.in_(order_ids))
        .order_by(Order.created_at.desc())
    ).all()
    return render_template("seller/orders.html", orders=orders)


@bp.route("/orders/<int:order_id>/status", methods=["POST"])
@seller_required
def update_order_status(order_id: int):
    """Seller updates status: pending → shipped → in_transit → delivered."""
    # Verify this order contains a product owned by this seller
    item = db.session.scalar(
        select(OrderItem)
        .join(Product)
        .where(OrderItem.order_id == order_id, Product.seller_id == current_user.id)
    )
    if not item:
        abort(403)
    order = db.session.get(Order, order_id)
    status = request.form.get("status", "")
    valid = {"pending", "shipped", "in_transit", "delivered"}
    if status in valid:
        order.status = status
        db.session.commit()
        flash(f"Order #{order_id} updated to '{status.replace('_', ' ')}'.", "success")
    return redirect(url_for("seller.seller_orders"))


@bp.route("/products/new", methods=["GET", "POST"])
@seller_required
def new_product():
    form = ProductForm()
    form.category_id.choices = _category_choices()
    if form.validate_on_submit():
        image_key = _save_image(request.files.get("image"))
        product = Product(
            seller_id   = current_user.id,
            name        = form.name.data.strip(),
            description = form.description.data.strip(),
            price       = form.price.data,
            stock       = form.stock.data,
            category_id = form.category_id.data or None,
            image_key   = image_key,
            is_active   = form.is_active.data,
        )
        db.session.add(product)
        db.session.commit()
        flash("Product created.", "success")
        return redirect(url_for("seller.dashboard"))
    return render_template("seller/product_form.html", form=form, product=None)


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@seller_required
def edit_product(product_id: int):
    product = db.session.get(Product, product_id)
    if not product or (product.seller_id != current_user.id and not current_user.is_admin):
        abort(404)
    form = ProductForm(obj=product)
    form.category_id.choices = _category_choices()
    if form.validate_on_submit():
        new_image = _save_image(request.files.get("image"))
        if new_image and product.image_key:
            try: get_storage().delete(product.image_key)
            except Exception: pass
        product.name        = form.name.data.strip()
        product.description = form.description.data.strip()
        product.price       = form.price.data
        product.stock       = form.stock.data
        product.category_id = form.category_id.data or None
        product.is_active   = form.is_active.data
        if new_image:
            product.image_key = new_image
        db.session.commit()
        flash("Product updated.", "success")
        return redirect(url_for("seller.dashboard"))
    return render_template("seller/product_form.html", form=form, product=product)


@bp.route("/products/<int:product_id>/delete", methods=["POST"])
@seller_required
def delete_product(product_id: int):
    product = db.session.get(Product, product_id)
    if not product or (product.seller_id != current_user.id and not current_user.is_admin):
        abort(404)
    if product.image_key:
        try: get_storage().delete(product.image_key)
        except Exception: pass
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted.", "info")
    return redirect(url_for("seller.dashboard"))
