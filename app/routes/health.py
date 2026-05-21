from flask import Blueprint
from sqlalchemy import text
from app.models import db

bp = Blueprint("health", __name__)

@bp.route("/healthz")
def healthz():
    return {"status": "ok"}, 200

@bp.route("/readyz")
def readyz():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ready"}, 200
    except Exception as e:
        return {"status": "unready", "error": str(e)}, 503
