"""Database models for ShopCloud."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import bcrypt
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer,
    Numeric, String, Text, Enum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

db = SQLAlchemy()


def _utcnow():
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    buyer  = "buyer"
    seller = "seller"
    admin  = "admin"


class OrderStatus(str, enum.Enum):
    pending    = "pending"
    processing = "processing"
    shipped    = "shipped"
    delivered  = "delivered"
    cancelled  = "cancelled"


# ── Users ──────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id:            Mapped[int]      = mapped_column(Integer, primary_key=True)
    email:         Mapped[str]      = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str]      = mapped_column(String(255), nullable=False)
    full_name:     Mapped[str]      = mapped_column(String(200), nullable=False, default="")
    role:          Mapped[str]      = mapped_column(String(20),  nullable=False, default="buyer")
    is_active:     Mapped[bool]     = mapped_column(Boolean, default=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="seller", cascade="all, delete-orphan")
    orders:   Mapped[list["Order"]]   = relationship("Order",   back_populates="buyer",  cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt(rounds=12)
        ).decode()

    def check_password(self, password: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode(), self.password_hash.encode())
        except Exception:
            return False

    @property
    def is_seller(self):  return self.role == "seller"
    @property
    def is_admin(self):   return self.role == "admin"
    @property
    def is_buyer(self):   return self.role == "buyer"


# ── Products ───────────────────────────────────────────────────────────────

class Category(db.Model):
    __tablename__ = "categories"

    id:   Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    products: Mapped[list["Product"]] = relationship("Product", back_populates="category")


class Product(db.Model):
    __tablename__ = "products"

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True)
    seller_id:   Mapped[int]            = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id: Mapped[int | None]     = mapped_column(ForeignKey("categories.id"), nullable=True)
    name:        Mapped[str]            = mapped_column(String(200), nullable=False)
    description: Mapped[str]            = mapped_column(Text, default="")
    price:       Mapped[Decimal]        = mapped_column(Numeric(10, 2), nullable=False)
    stock:       Mapped[int]            = mapped_column(Integer, default=0)
    image_key:   Mapped[str | None]     = mapped_column(String(500), nullable=True)
    is_active:   Mapped[bool]           = mapped_column(Boolean, default=True)
    created_at:  Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at:  Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    seller:   Mapped["User"]          = relationship("User",     back_populates="products")
    category: Mapped["Category|None"] = relationship("Category", back_populates="products")
    order_items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="product")

    def image_url(self, storage) -> str:
        if not self.image_key:
            return "/static/img/placeholder.png"
        # Serve thumbnail if Lambda has generated it, else fall back to original
        thumb_key = self.image_key.replace("images/original/", "images/thumb/", 1)
        try:
            if storage.exists(thumb_key):
                return storage.url(thumb_key)
        except Exception:
            pass
        return storage.url(self.image_key)


# ── Orders ─────────────────────────────────────────────────────────────────

class Order(db.Model):
    __tablename__ = "orders"

    id:              Mapped[int]      = mapped_column(Integer, primary_key=True)
    buyer_id:        Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status:          Mapped[str]      = mapped_column(String(20), default="pending", nullable=False)
    total_amount:    Mapped[Decimal]  = mapped_column(Numeric(10, 2), nullable=False, default=0)
    shipping_name:   Mapped[str]      = mapped_column(String(200), default="")
    shipping_addr:   Mapped[str]      = mapped_column(String(500), default="")
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    buyer: Mapped["User"]            = relationship("User",      back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id:         Mapped[int]     = mapped_column(Integer, primary_key=True)
    order_id:   Mapped[int]     = mapped_column(ForeignKey("orders.id",   ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int]     = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity:   Mapped[int]     = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order:   Mapped["Order"]   = relationship("Order",   back_populates="items")
    product: Mapped["Product"] = relationship("Product", back_populates="order_items")

    @property
    def subtotal(self):
        return self.unit_price * self.quantity
