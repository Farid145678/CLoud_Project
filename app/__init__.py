"""ShopCloud — Flask application factory."""
from __future__ import annotations
import logging, os, sys
from dotenv import load_dotenv
from flask import Flask, render_template, send_from_directory
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from app.models import User, Category, db

load_dotenv()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
csrf = CSRFProtect()


def create_app() -> Flask:
    app = Flask(__name__)

    app.config.update(
        SECRET_KEY              = os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///shopcloud.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS = False,
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 1800},
        STORAGE_BACKEND   = os.environ.get("STORAGE_BACKEND", "local"),
        LOCAL_STORAGE_DIR = os.environ.get("LOCAL_STORAGE_DIR", "./local_storage"),
        S3_BUCKET         = os.environ.get("S3_BUCKET", ""),
        AWS_REGION        = os.environ.get("AWS_REGION", "eu-west-1"),
        PUBLIC_BASE_URL   = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000"),
        MAX_CONTENT_LENGTH = 8 * 1024 * 1024,  # 8 MB upload limit
    )

    _configure_logging(app)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(uid: str):
        return db.session.get(User, int(uid))

    # Serve locally-stored media files
    @app.route("/media/<path:key>")
    def serve_media(key):
        base = os.path.abspath(app.config["LOCAL_STORAGE_DIR"])
        return send_from_directory(base, key)

    # Blueprints
    from app.routes.auth    import bp as auth_bp
    from app.routes.shop    import bp as shop_bp
    from app.routes.seller  import bp as seller_bp
    from app.routes.admin   import bp as admin_bp
    from app.routes.health  import bp as health_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(shop_bp)
    app.register_blueprint(seller_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(health_bp)

    # Error handlers
    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="Access denied"), 403

    @app.errorhandler(500)
    def server_error(_e):
        app.logger.exception("Unhandled 500")
        return render_template("error.html", code=500, message="Server error"), 500

    with app.app_context():
        db.create_all()
        _seed(app)

    return app


def _seed(app: Flask) -> None:
    """Create default categories and admin account if they don't exist."""
    from app.models import Category, User

    default_categories = [
        ("Electronics", "electronics"), ("Clothing",   "clothing"),
        ("Books",        "books"),       ("Home",        "home"),
        ("Sports",       "sports"),      ("Beauty",      "beauty"),
        ("Toys",         "toys"),        ("Food",        "food"),
    ]
    for name, slug in default_categories:
        if not db.session.query(Category).filter_by(slug=slug).first():
            db.session.add(Category(name=name, slug=slug))

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@shopcloud.com")
    admin_pass  = os.environ.get("ADMIN_PASSWORD", "admin123")
    if not db.session.query(User).filter_by(email=admin_email).first():
        admin = User(email=admin_email, full_name="Admin", role="admin")
        admin.set_password(admin_pass)
        db.session.add(admin)
        app.logger.info("Admin account created: %s", admin_email)

    db.session.commit()


def _configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    app.logger.handlers = [handler]
    app.logger.setLevel(logging.INFO)
