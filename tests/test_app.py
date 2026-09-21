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


def test_offer_cards_and_click_tracking(client):
    from flask import current_app

    from src.models import Offer, OfferClick, Poll, db

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    pid = client.post("/api/polls", json={"campaign": "offers-test", "question": "Offer poll?", "options": ["Yes", "No"]},
                      headers={"Authorization": "Bearer t0k3n"}).get_json()["id"]
    live = Offer(name="Gold kit", headline="Free gold guide", url="https://aff.example/c?sub={subid}", weight=5)
    paused = Offer(name="Old", headline="Paused offer", url="https://aff.example/old", active=False)
    db.session.add_all([live, paused])
    db.session.commit()

    ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"}
    page = client.get(f"/poll/{pid}/", headers=ua)
    assert b"Free gold guide" in page.data and b"Paused offer" not in page.data
    assert f"/go/{live.id}/?p={pid}".encode() in page.data
    client.get(f"/poll/{pid}/", headers={"User-Agent": "Mozilla/5.0 (compatible; Proofpoint URL Defense)"})
    db.session.refresh(live)
    assert live.impressions == 1  # scanner view not counted

    r = client.get(f"/go/{live.id}/?p={pid}", headers=ua)
    click = OfferClick.query.filter_by(offer_id=live.id).one()
    assert r.status_code == 302 and r.headers["Location"] == f"https://aff.example/c?sub=pda-o{live.id}-p{pid}-c{click.id}-snone"
    assert click.poll_id == pid and not click.is_bot
    client.get(f"/go/{live.id}/", headers={"User-Agent": "python-requests/2.31"})
    assert OfferClick.query.filter_by(offer_id=live.id, is_bot=True).count() == 1
    assert client.get(f"/go/{paused.id}/", headers=ua).headers["Location"].endswith("/")
    assert client.get("/go/99999/").status_code == 404
    assert b"Disallow: /go/" in client.get("/robots.txt").data

    client.post("/admin/login", data={"password": "ci-password"})
    with client.session_transaction() as s:
        csrf = s["csrf"]
    listing = client.get("/admin/offers/")
    assert listing.status_code == 200 and b"Gold kit" in listing.data and b"100.0%" in listing.data
    r = client.post("/admin/offers/new", data={"csrf": csrf, "headline": "Bad", "url": "javascript:alert(1)", "active": "1"})
    assert b"must start with https" in r.data and Offer.query.filter_by(headline="Bad").count() == 0
    r = client.post("/admin/offers/new", data={"csrf": csrf, "headline": "Medicare guide", "url": "https://m.example/", "active": "1"})
    assert r.status_code == 302 and Offer.query.filter_by(headline="Medicare guide").one().name == "Medicare guide"
    client.post(f"/admin/offers/{live.id}", data={"csrf": csrf, "action": "delete"})
    assert db.session.get(Offer, live.id) is None and OfferClick.query.filter_by(offer_id=live.id).count() == 0
    db.session.delete(db.session.get(Poll, pid))
    db.session.commit()


def test_post_vote_savings_question_targets_offers(client):
    from flask import current_app

    from src.models import Offer, OfferClick, Poll, QualifierAnswer, db, offer_visible

    assert offer_visible("all", "") and offer_visible("not_low", "") and not offer_visible("not_low", "lt20k")
    assert not offer_visible("50k", "") and not offer_visible("50k", "na") and not offer_visible("50k", "20k")
    assert offer_visible("50k", "50k") and offer_visible("50k", "250k") and offer_visible("20k", "20k")

    current_app.config["PUBLISH_TOKEN"] = "t0k3n"
    pid = client.post("/api/polls", json={"campaign": "qualifier-test", "question": "Q poll?", "options": ["Yes", "No"]},
                      headers={"Authorization": "Bearer t0k3n"}).get_json()["id"]
    for o in Offer.query.all():
        o.active = False
    gold = Offer(name="Goldco", headline="Free gold IRA kit", url="https://g.example/?s={subid}", audience="50k", weight=9)
    news = Offer(name="Newsletter", headline="Try our partner newsletter", url="https://n.example/", audience="all")
    db.session.add_all([gold, news])
    db.session.commit()
    ua = {"User-Agent": "Mozilla/5.0 Safari"}
    client.delete_cookie("q_sav")
    client.delete_cookie("rid")

    page = client.get(f"/poll/{pid}/", headers=ua).data
    assert b"saved for retirement" in page and b"Try our partner newsletter" in page and b"Free gold IRA kit" not in page

    client.post(f"/poll/{pid}/qualify", data={"savings": "bogus"}, headers=ua)
    assert QualifierAnswer.query.count() == 0
    client.post(f"/poll/{pid}/qualify", data={"savings": "250k", "website": "bot"}, headers=ua)  # honeypot
    assert QualifierAnswer.query.count() == 0

    r = client.post(f"/poll/{pid}/qualify", data={"savings": "lt20k"}, headers=ua)
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/poll/{pid}/?answered=1#offers")
    page = client.get(f"/poll/{pid}/?answered=1", headers=ua).data
    assert b"saved for retirement" not in page and b"Thanks for answering" in page and b"Free gold IRA kit" not in page

    client.post(f"/poll/{pid}/qualify", data={"savings": "50k"}, headers=ua)  # changing the answer updates the same row
    rows = QualifierAnswer.query.all()
    assert len(rows) == 1 and rows[0].answer == "50k" and rows[0].poll_id == pid
    page = client.get(f"/poll/{pid}/", headers=ua).data
    assert page.index(b"Free gold IRA kit") < page.index(b"Try our partner newsletter")

    r = client.get(f"/go/{gold.id}/?p={pid}", headers=ua)
    click = OfferClick.query.filter_by(offer_id=gold.id).one()
    assert click.segment == "50k" and r.headers["Location"].endswith(f"-c{click.id}-s50k")

    client.post("/admin/login", data={"password": "ci-password"})
    with client.session_transaction() as s:
        csrf = s["csrf"]
    polls_page = client.get("/admin/polls/").data
    assert b"retirement savings" in polls_page and b"1 answers" in polls_page and b"0 linked" in polls_page
    assert b"Clicks by savings answer" in client.get("/admin/offers/").data
    form = {"csrf": csrf, "headline": news.headline, "url": news.url, "active": "1"}
    client.post(f"/admin/offers/{news.id}", data={**form, "audience": "not_low"})
    assert db.session.get(Offer, news.id).audience == "not_low"
    client.post(f"/admin/offers/{news.id}", data={**form, "audience": "nope"})
    assert db.session.get(Offer, news.id).audience == "all"

    db.session.delete(db.session.get(Poll, pid))
    db.session.commit()


def test_vote_links_bind_bigmailer_contact(client, monkeypatch):
    from flask import current_app

    import src.routes.api as api
    from src.models import Poll, PollVote, QualifierAnswer, db

    calls = []

    class FakeResp:
        status_code, text = 200, "{}"

    monkeypatch.setattr(api.requests, "patch", lambda url, **kw: calls.append((url, kw)) or FakeResp())
    current_app.config.update(PUBLISH_TOKEN="t0k3n", BIGMAILER_API_KEY="k", BIGMAILER_BRAND_ID="brand")
    try:
        pid = client.post("/api/polls", json={"campaign": "contact-test", "question": "Contact poll?", "options": ["A", "B"]},
                          headers={"Authorization": "Bearer t0k3n"}).get_json()["id"]
        cid = "3f2b8c1e-9a7d-4e21-b5c3-0d6f1a2b3c4d"
        for name in ("q_sav", "rid", "bmc", f"pv{pid}"):
            client.delete_cookie(name)
        ua = {"User-Agent": "Mozilla/5.0 Safari"}

        client.get(f"/poll/{pid}/vote/0/?c={cid}", headers={"User-Agent": "Mozilla/5.0 (compatible; Proofpoint scanner)"})
        assert PollVote.query.filter_by(poll_id=pid).count() == 0  # scanners don't vote
        client.delete_cookie(f"pv{pid}")
        client.get(f"/poll/{pid}/vote/0/?c={cid.upper()}", headers=ua)
        client.delete_cookie(f"pv{pid}")  # same subscriber on another device changes their mind: last click wins
        client.get(f"/poll/{pid}/vote/1/?c={cid}", headers={"User-Agent": "Mozilla/5.0 Android"})
        votes = PollVote.query.filter_by(poll_id=pid).all()
        assert len(votes) == 1 and votes[0].choice == 1 and votes[0].contact_id == cid

        client.get(f"/poll/{pid}/vote/0/?c=*|_ID|*", headers={"User-Agent": "Mozilla/5.0 Firefox"})  # web copy: raw tag
        assert PollVote.query.filter_by(poll_id=pid).count() == 1  # bmc cookie from the earlier click still identifies them

        r = client.post(f"/poll/{pid}/qualify", data={"savings": "250k"}, headers=ua)  # contact comes from the bmc cookie
        row = QualifierAnswer.query.filter_by(contact_id=cid).one()
        assert r.status_code == 302 and row.answer == "250k"
        assert calls[-1][0].endswith(f"/brands/brand/contacts/{cid}") and calls[-1][1]["params"] == {"field_values_op": "add"}
        assert calls[-1][1]["json"] == {"field_values": [{"name": "PDA_SAVINGS", "string": "250k"}]}

        for name in ("q_sav", "rid", "bmc"):  # new browser, arriving from the email link: answer is remembered
            client.delete_cookie(name)
        r = client.get(f"/poll/{pid}/?c={cid}&utm_source=newsletter", headers=ua)
        assert r.status_code == 302 and r.headers["Location"].endswith(f"/poll/{pid}/?utm_source=newsletter")
        page = client.get(f"/poll/{pid}/", headers=ua)
        assert b"saved for retirement" not in page.data
        assert any("q_sav=250k" in h for h in page.headers.getlist("Set-Cookie"))

        client.delete_cookie("q_sav")
        client.delete_cookie("bmc")
        client.post(f"/poll/{pid}/qualify", data={"savings": "20k"}, headers=ua)  # anonymous reader: no BigMailer call
        assert len(calls) == 1
    finally:
        current_app.config.update(BIGMAILER_API_KEY="", BIGMAILER_BRAND_ID="")
        for name in ("q_sav", "rid", "bmc"):
            client.delete_cookie(name)
        db.session.delete(db.session.get(Poll, pid))
        QualifierAnswer.query.delete()
        db.session.commit()


def test_newsletter_poll_links_carry_contact_tag():
    import tools.build_newsletter as nl

    poll = {"question": "Q?", "options": ["Yes", "No"], "results_url": "https://x/poll/1/",
            "vote_urls": ["https://x/poll/1/vote/0/", "https://x/poll/1/vote/1/"]}
    html = nl.poll_block(poll)
    assert 'href="https://x/poll/1/vote/0/?c=*|_ID|*&amp;utm_source=newsletter' in html or \
        'href="https://x/poll/1/vote/0/?c=*|_ID|*&utm_source=newsletter' in html
    assert 'https://x/poll/1/?c=*|_ID|*' in html


def test_newsletter_sponsor_slots_rotate():
    import datetime as dt

    import tools.build_newsletter as nl

    banners = [{"name": "a1", "group": "a", "image": "/static/img/sponsors/a1.jpg", "url": "https://trk.example/x/", "alt": "A one"},
               {"name": "b", "image": "/static/img/sponsors/b.jpg", "url": "https://trk.example/x/?o=1", "alt": "B"},
               {"name": "a2", "group": "a", "image": "/static/img/sponsors/a2.jpg", "url": "https://trk.example/x/", "active": False}]
    saved = nl.CONFIG.get("sponsors")
    nl.CONFIG["sponsors"] = {"enabled": True, "banners": banners}
    try:
        days = [nl.pick_sponsors(dt.datetime(2026, 9, d)) for d in (21, 22)]
        assert [d["top"]["name"] for d in days] in (["a1", "b"], ["b", "a1"])  # advances daily, paused banner never runs
        assert all(d["top"]["name"] != d["mid"]["name"] for d in days)
        banners[2]["active"] = True
        for d in range(21, 27):  # look-alikes sharing a group never share an issue
            p = nl.pick_sponsors(dt.datetime(2026, 9, d))
            assert {p["top"]["name"], p["mid"]["name"]} != {"a1", "a2"}
        html = nl.sponsor_block(banners[1], "mid", "2026-09-21-daily")
        assert "SPONSORED" in html and "?o=1&amp;sub1=2026-09-21-daily&amp;sub2=mid&amp;sub3=b" in html
        assert f'{nl.SITE}/static/img/sponsors/b.jpg' in html
        nl.CONFIG["sponsors"]["enabled"] = False
        assert nl.pick_sponsors(dt.datetime(2026, 9, 21)) == {}
    finally:
        nl.CONFIG["sponsors"] = saved


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
    monkeypatch.setattr(notify, "_send_now", lambda cfg, to, subject, text, html, headers=None: sent.append((subject, text)))
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


def test_welcome_email_sent_once_on_signup(client, monkeypatch):
    from flask import current_app

    from src import notify

    sent = []
    monkeypatch.setattr(notify, "_send_now",
                        lambda cfg, to, subject, text, html, headers=None: sent.append((to, subject, text, html, headers)))
    current_app.config.update(SMTP_HOST="smtp.example.com", SMTP_USER="u", SMTP_PASSWORD="p")

    client.post("/api/subscribe", json={"email": "newreader@example.com", "source": "landing"})
    assert len(sent) == 1
    to, subject, text, html, headers = sent[0]
    assert to == ["newreader@example.com"] and "Welcome" in subject
    assert "drag it to Primary" in text and "drag it to Primary" in html
    assert "utm_source=welcome" in html  # story and poll links are attributed to the welcome email
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    client.post("/api/subscribe", json={"email": "newreader@example.com"})  # already active: no second welcome
    assert len(sent) == 1

    client.post("/api/unsubscribe", data={"email": "newreader@example.com"})
    client.post("/api/subscribe", json={"email": "newreader@example.com"})  # re-joining does get one
    assert len(sent) == 2


def test_welcome_unsubscribe_token_needs_a_post(client, monkeypatch):
    from flask import current_app

    from src import notify, welcome_email

    sent = []
    monkeypatch.setattr(notify, "_send_now",
                        lambda cfg, to, subject, text, html, headers=None: sent.append(headers))
    current_app.config.update(SMTP_HOST="smtp.example.com", SMTP_USER="u", SMTP_PASSWORD="p")
    client.post("/api/subscribe", json={"email": "oneclick@example.com"})
    path = sent[0]["List-Unsubscribe"].strip("<>").replace(current_app.config["SITE_URL"], "")

    # A link scanner following the GET must not unsubscribe anyone; it only pre-fills the confirmation form.
    assert client.get(path).status_code == 200
    assert Subscriber.query.filter_by(email="oneclick@example.com").one().unsubscribed_at is None

    assert client.post(path).status_code == 200
    assert Subscriber.query.filter_by(email="oneclick@example.com").one().unsubscribed_at is not None
    # A dead token must never dead-end a reader: the GET falls back to the form they can fill in themselves.
    r = client.get("/remove-from-our-email-list/not-a-real-token/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/remove-from-our-email-list/")
    assert client.get(r.headers["Location"]).status_code == 200
    # A one-click POST cannot guess whom to remove, so it fails rather than reporting a success that did not happen.
    assert client.post("/remove-from-our-email-list/not-a-real-token/").status_code == 400

    with current_app.test_request_context():
        assert welcome_email.verify_unsubscribe_token(welcome_email.unsubscribe_token("a@b.com")) == "a@b.com"


def test_welcome_email_skipped_without_a_transport(client, monkeypatch):
    from flask import current_app

    from src import notify

    sent = []
    monkeypatch.setattr(notify, "_send_now",
                        lambda cfg, to, subject, text, html, headers=None: sent.append(to))
    current_app.config.update(RESEND_API_KEY="", SMTP_HOST="", SMTP_USER="", SMTP_PASSWORD="")
    client.post("/api/subscribe", json={"email": "notransport@example.com"})
    assert sent == []
    assert Subscriber.query.filter_by(email="notransport@example.com").count() == 1  # signup still succeeds


def test_scheduler_claims_once_and_dispatches_when_due(client, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src import scheduler
    from src.main import app as live_app

    calls = []
    monkeypatch.setattr(scheduler, "dispatch", lambda job, config: calls.append(job["name"]) or 204)
    live_app.config.update(GITHUB_DISPATCH_TOKEN="x", SCHEDULE_STORIES="19:00", SCHEDULE_NEWSLETTER="20:00")
    tz = ZoneInfo(live_app.config["SCHEDULE_TZ"])
    assert scheduler.tick(live_app, datetime(2026, 9, 12, 18, 59, tzinfo=tz)) == []      # not yet due
    assert scheduler.tick(live_app, datetime(2026, 9, 12, 19, 0, 30, tzinfo=tz)) == ["stories"]
    assert scheduler.tick(live_app, datetime(2026, 9, 12, 19, 5, tzinfo=tz)) == []       # already claimed today
    assert scheduler.tick(live_app, datetime(2026, 9, 12, 20, 1, tzinfo=tz)) == ["newsletter"]
    assert scheduler.tick(live_app, datetime(2026, 9, 12, 23, 30, tzinfo=tz)) == []      # past the catch-up window
    assert calls == ["stories", "newsletter"]
    with live_app.app_context():
        st = scheduler.status(live_app)
    assert st["enabled"] and "dispatch:stories:2026-09-12" in st["recent"]


def test_scheduler_status_endpoint_requires_token(client):
    assert client.get("/api/scheduler").status_code == 401
