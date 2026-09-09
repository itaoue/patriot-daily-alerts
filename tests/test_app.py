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

    assert {c.slug for c in Category.query.all()} >= {"politics", "culture", "economy", "world", "border", "crime"}
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


def test_publish_api_partial_update_flips_status_and_section(client):
    from flask import current_app

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    h = {"Authorization": "Bearer t0k3n"}
    body = {"slug": "pipeline-story", "update": True, "status": "published", "category": "culture"}
    r = client.post("/api/publish", json=body, headers=h)
    assert r.status_code == 200 and r.get_json()["status"] == "published"
    post = Post.query.filter_by(slug="pipeline-story").first()
    assert post.status == "published" and post.category.slug == "culture" and post.body_html.startswith("<p>Hello</p>")
    assert client.get("/pipeline-story/").status_code == 200
    r = client.get("/api/posts/recent?days=1", headers=h).get_json()
    assert any(p["slug"] == "pipeline-story" and "editor_notes" in p for p in r["posts"])


def test_categories_exist_even_without_auto_seed():
    import os

    os.environ["AUTO_SEED"] = "0"
    try:
        from src.config import Config
        from src.models import Category

        class NoSeed(Config):
            AUTO_SEED = False
            SQLALCHEMY_DATABASE_URI = "sqlite://"

        app = create_app(NoSeed)
        with app.app_context():
            assert Category.query.count() == 6 and Post.query.count() == 0
    finally:
        os.environ.pop("AUTO_SEED", None)


def test_publish_api_can_delete(client):
    from flask import current_app

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    h = {"Authorization": "Bearer t0k3n"}
    assert client.post("/api/publish", json={"slug": "pipeline-story", "delete": True}, headers=h).get_json()["deleted"] == "pipeline-story"
    assert Post.query.filter_by(slug="pipeline-story").first() is None
    assert client.post("/api/publish", json={"slug": "pipeline-story", "delete": True}, headers=h).status_code == 404


def test_poll_create_vote_results(client):
    from flask import current_app

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    h = {"Authorization": "Bearer t0k3n"}
    r = client.post("/api/polls", json={"campaign": "2026-09-08-daily", "question": "Do you approve?", "options": ["Yes", "No"]}, headers=h)
    assert r.status_code == 200 and len(r.get_json()["vote_urls"]) == 2
    pid = r.get_json()["id"]
    # rebuilding the same issue reuses the poll
    again = client.post("/api/polls", json={"campaign": "2026-09-08-daily", "question": "x", "options": ["a", "b"]}, headers=h)
    assert again.get_json()["id"] == pid
    assert client.post("/api/polls", json={"question": "x", "options": ["only one"]}, headers=h).status_code == 400
    r = client.get(f"/poll/{pid}/vote/0/")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/poll/{pid}/?voted=1")
    client.get(f"/poll/{pid}/vote/1/")  # same browser: not counted again
    res = client.get(f"/api/polls/{pid}", headers=h).get_json()
    assert res["total"] == 1 and res["results"][0]["votes"] == 1 and res["results"][0]["pct"] == 100
    page = client.get(f"/poll/{pid}/?voted=1")
    assert page.status_code == 200 and b"Thank you for taking the poll" in page.data and b"Do you approve?" in page.data
    assert client.get(f"/poll/{pid}/vote/5/").status_code == 404
    assert client.post("/api/polls", json={"id": pid, "delete": True}, headers=h).get_json()["deleted"] == pid
    assert client.get(f"/poll/{pid}/").status_code == 404


def test_comments_flow(client):
    from flask import current_app

    from src.models import Comment

    post = Post.query.filter_by(status="published").order_by(Post.published_at.desc()).first()
    url = f"/{post.slug}/"
    assert b"0 Comments" in client.get(url).data
    r = client.post(f"{url}comment", data={"name": "Pat", "body": "Well said.", "email": ""})
    assert r.status_code == 302 and r.headers["Location"].endswith("?comment=pending#comments")
    assert b"awaiting review" in client.get(url + "?comment=pending").data and b"Well said." not in client.get(url).data
    assert client.post(f"{url}comment", data={"name": "x", "body": "hi"}).headers["Location"].endswith("?comment=invalid#comment-form")
    assert client.post(f"{url}comment", data={"name": "Bot", "body": "spam", "website": "x"}).status_code == 302
    assert Comment.query.filter_by(name="Bot").count() == 0
    c = Comment.query.filter_by(name="Pat").first()
    with client.session_transaction() as s:
        s["admin"] = True
        csrf = s["csrf"]
    assert b"Well said." in client.get("/admin/comments/?status=pending").data
    client.post(f"/admin/comments/{c.id}", data={"csrf": csrf, "action": "approved"})
    page = client.get(url).data
    assert b"Well said." in page and b"1 Comment<" in page
    current_app.config["COMMENTS_AUTO_APPROVE"] = True
    r = client.post(f"{url}comment", data={"name": "Sam", "body": "Auto approved"})
    assert r.headers["Location"].endswith("?comment=ok#comments") and b"Auto approved" in client.get(url).data
    current_app.config["COMMENTS_AUTO_APPROVE"] = False


def test_comment_notification_and_one_click_moderation(client, monkeypatch):
    from flask import current_app

    from src import notify
    from src.models import Comment

    sent = []
    monkeypatch.setattr(notify, "_send_now", lambda cfg, subject, text, html: sent.append((subject, text)))
    current_app.config.update(NOTIFY_EMAIL="editor@example.com", SMTP_HOST="smtp.example.com", SMTP_USER="u", SMTP_PASSWORD="p")
    post = Post.query.filter_by(status="published").order_by(Post.published_at.desc()).first()
    client.post(f"/{post.slug}/comment", data={"name": "Notifier", "body": "Ping the editor"})
    assert sent and sent[0][0].startswith("New comment on:") and "Ping the editor" in sent[0][1]
    approve_url = [ln.split(": ", 1)[1] for ln in sent[0][1].splitlines() if ln.startswith("Approve: ")][0]
    path = approve_url.replace(current_app.config["SITE_URL"], "")
    with client.session_transaction() as s:
        s["admin"] = False
    r = client.get(path)
    assert r.status_code == 200 and b"Approved" in r.data
    assert Comment.query.filter_by(name="Notifier").first().status == "approved"
    assert client.get("/admin/moderate/not-a-valid-token").status_code == 404
    client.post("/api/contact", data={"name": "Reader", "email": "r@example.com", "subject": "Tip", "message": "Look into this"})
    assert sent[-1][0] == "Contact form: Tip"
    current_app.config.update(NOTIFY_EMAIL="", SMTP_HOST="", SMTP_USER="", SMTP_PASSWORD="")


def test_landing_page_variants_and_utm_passthrough(client):
    r = client.get("/join/")
    assert r.status_code == 200
    assert b"Send Me the Morning Brief" in r.data
    assert b'name="next" value="/welcome/"' in r.data
    assert b"mh-main-nav" not in r.data  # standalone page: no site navigation to leak clicks
    r = client.get("/join/?h=border&utm_source=taboola&utm_campaign=sept")
    assert b"border and immigration" in r.data
    assert b'name="utm_source" value="taboola"' in r.data
    assert b'name="utm_content" value="border"' in r.data
    assert client.get("/join/?h=nonsense").status_code == 200  # unknown variant falls back to default copy


def test_landing_signup_tags_source_and_redirects_to_welcome(client):
    r = client.post("/api/subscribe", data={
        "email": "lp@example.com", "source": "landing", "utm_source": "Taboola", "utm_campaign": "Sept 09", "next": "/welcome/",
    })
    assert r.status_code == 302 and r.headers["Location"].endswith("/welcome/")
    assert Subscriber.query.filter_by(email="lp@example.com").one().source == "landing:taboola:sept09"
    # JSON clients get the redirect target back instead of a 302
    j = client.post("/api/subscribe", json={"email": "lp2@example.com", "source": "landing", "next": "/welcome/"}).get_json()
    assert j == {"ok": True, "next": "/welcome/"}
    # open-redirect attempts are ignored
    r = client.post("/api/subscribe", data={"email": "lp3@example.com", "next": "https://evil.example"})
    assert r.status_code == 200 and b"on the list" in r.data
    r = client.post("/api/subscribe", data={"email": "lp4@example.com", "next": "//evil.example"})
    assert r.status_code == 200
    assert client.post("/api/subscribe", json={"email": "plain@example.com"}).get_json() == {"ok": True, "next": ""}


def test_welcome_page(client):
    r = client.get("/welcome/")
    assert r.status_code == 200
    assert b"noindex" in r.data
    assert b"truthsocial.com/share" in r.data
    assert b"utm_source%3Dreferral" in r.data


def test_admin_subscribers_page_breaks_down_signups_by_source(client):
    client.post("/admin/login", data={"password": "ci-password"})
    r = client.get("/admin/subscribers/")
    assert r.status_code == 200
    assert b"Signups by source" in r.data
    assert b"landing:taboola:sept09" in r.data
