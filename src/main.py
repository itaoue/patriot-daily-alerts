import os
import sys

from flask import Flask, g, redirect, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import Config  # noqa: E402
from src.models import Category, db  # noqa: E402
from src.utils import format_date, format_datetime, time_ago  # noqa: E402


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(config_object)
    db.init_app(app)

    from src.routes.admin import admin_bp
    from src.routes.api import api_bp
    from src.routes.public import public_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)

    app.jinja_env.filters["date"] = format_date
    app.jinja_env.filters["datetime"] = format_datetime
    app.jinja_env.filters["ago"] = time_ago

    @app.context_processor
    def inject_globals():
        return {
            "site_name": app.config["SITE_NAME"],
            "site_url": app.config["SITE_URL"],
            "site_tagline": app.config["SITE_TAGLINE"],
            "contact_email": app.config["CONTACT_EMAIL"],
            "ga_id": app.config["GA_MEASUREMENT_ID"],
            "nav_categories": getattr(g, "nav_categories", []),
            "current_path": request.path,
        }

    @app.before_request
    def redirect_www():
        # www.example.com -> https://example.com (permanent), keeping path and query string
        host = request.host.split(":")[0].lower()
        if host.startswith("www."):
            target = app.config["SITE_URL"] + request.full_path.rstrip("?")
            return redirect(target, 301)

    @app.before_request
    def load_nav():
        if request.path.startswith("/static/"):
            return
        g.nav_categories = Category.query.order_by(Category.id).all()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    with app.app_context():
        db.create_all()
        if app.config["AUTO_SEED"]:
            from src.seed import seed_if_empty

            seed_if_empty()

    @app.cli.command("seed")
    def seed_cmd():
        """Seed the database from tools/seed_data.json if it is empty."""
        from src.seed import seed_if_empty

        print(f"seeded {seed_if_empty()} posts")

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_ENV") == "development")
