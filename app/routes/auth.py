"""Auth routes: register (buyer or seller), login, logout."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user, current_user
from flask_wtf import FlaskForm
from sqlalchemy import select
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length

from app.models import User, db

bp = Blueprint("auth", __name__, url_prefix="/auth")


class RegisterForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=200)])
    email     = StringField("Email",     validators=[DataRequired(), Email(), Length(max=255)])
    role      = SelectField("I want to", choices=[("buyer", "Shop (Buyer)"), ("seller", "Sell products (Seller)")])
    password  = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm   = PasswordField("Confirm",  validators=[DataRequired(), EqualTo("password")])
    submit    = SubmitField("Create account")


class LoginForm(FlaskForm):
    email    = StringField("Email",    validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit   = SubmitField("Log in")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("shop.index"))
    form = RegisterForm()
    if form.validate_on_submit():
        if db.session.scalar(select(User).where(User.email == form.email.data.lower())):
            flash("Email already registered.", "danger")
        else:
            user = User(
                email=form.email.data.lower(),
                full_name=form.full_name.data.strip(),
                role=form.role.data,
            )
            user.set_password(form.password.data)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f"Welcome, {user.full_name}!", "success")
            if user.is_seller:
                return redirect(url_for("seller.dashboard"))
            return redirect(url_for("shop.index"))
    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("shop.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(select(User).where(User.email == form.email.data.lower()))
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(request.args.get("next") or url_for("shop.index"))
        flash("Invalid email or password.", "danger")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for("auth.login"))
