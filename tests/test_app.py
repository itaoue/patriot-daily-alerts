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


def test_admin_import_page_and_status(client):
    r = client.get("/admin/import")
    assert r.status_code == 200 and b"Start import" in r.data
    s = client.get("/admin/import/status").get_json()
    assert s["state"] == "idle" and s["posts_in_db"] >= 20


def test_legacy_upload_route_serves_mirrored_images(client, tmp_path):
    import os

    from flask import current_app

    folder = os.path.join(current_app.static_folder, "uploads", "2099", "01")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "probe.txt"), "w") as fh:
        fh.write("img")
    try:
        assert client.get("/wp-content/uploads/2099/01/probe.txt").data == b"img"
        assert client.get("/wp-content/uploads/2099/01/missing.jpg").status_code == 404
        assert client.get("/wp-content/uploads/../../main.py").status_code == 404
    finally:
        os.remove(os.path.join(folder, "probe.txt"))
        os.removedirs(folder)


def test_article_with_tweet_loads_twitter_widget(client):
    post = Post.query.filter(Post.body_html.contains("twitter-tweet")).first()
    assert post is not None
    r = client.get(f"/{post.slug}/")
    assert b"platform.twitter.com/widgets.js" in r.data
    other = Post.query.filter(~Post.body_html.contains("twitter-tweet")).first()
    assert b"platform.twitter.com/widgets.js" not in client.get(f"/{other.slug}/").data


def test_www_redirects_to_bare_domain(client):
    r = client.get("/latest/?page=2", headers={"Host": "www.patriotdailyalerts.com"})
    assert r.status_code == 301
    assert r.headers["Location"] == "https://patriotdailyalerts.com/latest/?page=2"
    assert client.get("/", headers={"Host": "patriotdailyalerts.com"}).status_code == 200


def test_five_sections_exist(client):
    from src.models import Category

    assert {c.slug for c in Category.query.all()} >= {"politics", "culture", "economy", "world", "border"}
    assert b"/category/border/" in client.get("/").data


def test_publish_api_requires_token_and_creates_draft(client):
    from flask import current_app

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    body = {"title": "Pipeline Story", "body_html": "<p>Hello</p><script>x()</script>", "category": "economy",
            "sources": [{"label": "Newsmax", "url": "https://www.newsmax.com/x"}], "editor_notes": "looks fine"}
    assert client.post("/api/publish", json=body).status_code == 401
    r = client.post("/api/publish", json=body, headers={"Authorization": "Bearer t0k3n"})
    assert r.status_code == 201
    j = r.get_json()
    assert j["status"] == "draft" and j["slug"] == "pipeline-story"
    assert client.get("/pipeline-story/").status_code == 404  # drafts are not public
    with client.session_transaction() as sess:
        was_admin = sess.get("admin")
        sess["admin"] = False
    assert client.get("/pipeline-story/?preview=1").status_code == 404  # preview needs an editor session
    with client.session_transaction() as sess:
        sess["admin"] = True
    assert client.get("/pipeline-story/?preview=1").status_code == 200
    with client.session_transaction() as sess:
        sess["admin"] = was_admin
    post = Post.query.filter_by(slug="pipeline-story").first()
    assert post.category.slug == "economy" and "<script>" not in post.body_html and post.source_list[0]["label"] == "Newsmax"
    assert client.post("/api/publish", json=body, headers={"Authorization": "Bearer t0k3n"}).status_code == 409
    r = client.get("/api/posts/recent?days=3", headers={"Authorization": "Bearer t0k3n"})
    assert any(p["slug"] == "pipeline-story" for p in r.get_json()["posts"])
    assert client.get("/api/posts/recent").status_code == 401
