import os


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        # Railway / Heroku style URL; SQLAlchemy 2 requires the postgresql:// scheme
        url = url.replace("postgres://", "postgresql://", 1)
    if not url:
        os.makedirs(os.path.join(os.path.dirname(__file__), "..", "instance"), exist_ok=True)
        url = "sqlite:///" + os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "instance", "pda.sqlite")
        )
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SITE_NAME = os.environ.get("SITE_NAME", "Patriot Daily Alerts")
    SITE_URL = os.environ.get("SITE_URL", "https://patriotdailyalerts.com").rstrip("/")
    SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "Real American news, delivered daily.")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "news@patriotdailyalerts.com")

    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    PUBLISH_TOKEN = os.environ.get("PUBLISH_TOKEN", "")  # bearer token for the content pipeline
    AUTO_SEED = os.environ.get("AUTO_SEED", "1") == "1"

    # Optional: push new subscribers to BigMailer (same integration as RetireBrief)
    BIGMAILER_API_KEY = os.environ.get("BIGMAILER_API_KEY", "")
    BIGMAILER_BRAND_ID = os.environ.get("BIGMAILER_BRAND_ID", "")
    BIGMAILER_LIST_ID = os.environ.get("BIGMAILER_LIST_ID", "")

    GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "G-7J6WNSF6DJ")  # GA4 property 371513253

    POSTS_PER_PAGE = 12
    # optional offer shown under poll results (same shape as content/newsletter/config.json "sponsor")
    POLL_SPONSOR = None
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV", "production") == "production"
