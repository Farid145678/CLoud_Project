"""Admin routes — admin role only."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select, func

from app.models import Order, Product, User, db

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return login_required(wrapper)


@bp.route("/")
@admin_required
def overview():
    total_users    = db.session.scalar(select(func.count()).select_from(User))
    total_products = db.session.scalar(select(func.count()).select_from(Product))
    total_orders   = db.session.scalar(select(func.count()).select_from(Order))
    total_revenue  = db.session.scalar(
        select(func.sum(Order.total_amount)).where(Order.status != "cancelled")
    ) or 0
    recent_orders = db.session.scalars(
        select(Order).order_by(Order.created_at.desc()).limit(10)
    ).all()
    return render_template("admin/overview.html",
                           total_users=total_users, total_products=total_products,
                           total_orders=total_orders, total_revenue=total_revenue,
                           recent_orders=recent_orders)


@bp.route("/orders")
@admin_required
def orders():
    all_orders = db.session.scalars(
        select(Order).order_by(Order.created_at.desc())
    ).all()
    return render_template("admin/orders.html", orders=all_orders)


@bp.route("/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_order_status(order_id: int):
    order  = db.session.get(Order, order_id)
    if not order:
        abort(404)
    status = request.form.get("status", "")
    valid  = {"pending", "shipped", "in_transit", "delivered", "cancelled"}
    if status in valid:
        order.status = status
        db.session.commit()
        flash(f"Order #{order_id} status updated to {status}.", "success")
    return redirect(url_for("admin.orders"))


@bp.route("/users")
@admin_required
def users():
    all_users = db.session.scalars(select(User).order_by(User.created_at.desc())).all()
    return render_template("admin/users.html", users=all_users)


@bp.route("/products")
@admin_required
def products():
    all_products = db.session.scalars(
        select(Product).order_by(Product.created_at.desc())
    ).all()
    return render_template("admin/products.html", products=all_products)
