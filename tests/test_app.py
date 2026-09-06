import os

import pytest

os.environ.setdefault("ADMIN_PASSWORD", "ci-password")
os.environ.setdefault("FLASK_ENV", "development")
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory

from src.main import create_app  # noqa: E402
from src.models import Post, Subscriber  # noqa: E402


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context(), app.test_client() as c:
        yield c


def test_home_lists_seeded_stories(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Top stories" in r.data
    assert Post.query.count() >= 20


def test_article_and_legacy_slug_urls(client):
    post = Post.query.order_by(Post.published_at.desc()).first()
    r = client.get(f"/{post.slug}/")
    assert r.status_code == 200
    assert post.title.encode() in r.data
    assert client.get("/category/politics/").status_code == 200
    assert client.get("/about-us/").status_code == 200
    assert client.get("/privacy-policy/").status_code == 200
    assert client.get("/does-not-exist/").status_code == 404


def test_feed_sitemap_robots_health(client):
    assert client.get("/feed/").status_code == 200
    assert b"<rss" in client.get("/feed/").data
    assert b"<urlset" in client.get("/sitemap.xml").data
    assert b"Sitemap:" in client.get("/robots.txt").data
    assert client.get("/api/health").get_json()["status"] == "ok"


def test_search(client):
    r = client.get("/search/?q=trump")
    assert r.status_code == 200
    assert b"stories found" in r.data


def test_subscribe_validation_and_unsubscribe(client):
    assert client.post("/api/subscribe", json={"email": "not-an-email"}).status_code == 400
    r = client.post("/api/subscribe", json={"email": "Reader@Example.com", "source": "test"})
    assert r.get_json()["ok"] is True
    assert Subscriber.query.filter_by(email="reader@example.com").count() == 1
    client.post("/api/subscribe", json={"email": "reader@example.com"})  # idempotent
    assert Subscriber.query.count() == 1
    r = client.post("/api/unsubscribe", data={"email": "reader@example.com"})
    assert r.status_code == 200
    assert Subscriber.query.first().unsubscribed_at is not None


def test_honeypot_blocks_bots(client):
    client.post("/api/subscribe", json={"email": "bot@example.com", "website": "spam"})
    assert Subscriber.query.filter_by(email="bot@example.com").count() == 0


def test_admin_requires_login_then_can_publish(client):
    assert client.get("/admin/").status_code == 302
    assert client.post("/admin/login", data={"password": "wrong"}).status_code == 200
    r = client.post("/admin/login", data={"password": "ci-password"})
    assert r.status_code == 302
    with client.session_transaction() as s:
        csrf = s["csrf"]
    r = client.post(
        "/admin/posts/new",
        data={"csrf": csrf, "title": "Hello Newsroom", "body_html": "<p>Body</p><script>x()</script>",
              "category_id": 1, "status": "published"},
    )
    assert r.status_code == 302
    post = Post.query.filter_by(title="Hello Newsroom").first()
    assert post and post.slug == "hello-newsroom" and "<script>" not in post.body_html
    assert b"Hello Newsroom" in client.get("/hello-newsroom/").data
    # CSRF token required
    assert client.post(f"/admin/posts/{post.id}", data={"title": "x", "category_id": 1}).status_code == 400
