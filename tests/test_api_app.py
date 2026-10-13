from fastapi.testclient import TestClient

from reddit_signal import config
from reddit_signal.api.main import create_app


def cfg(**kw):
    return config.Config(database_url="postgresql://u:p@localhost/db", **kw)


def test_app_exposes_only_public_routes():
    paths = {r.path for r in create_app(cfg()).routes}
    assert "/api/public/snapshot" in paths and "/healthz" in paths
    assert not any(p.startswith("/api/v2") for p in paths)


def test_serves_seedcast_from_the_frontend_build(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "seedcast.html").write_text("<html>seedcast</html>")
    client = TestClient(create_app(cfg(static_dir=str(tmp_path))))
    assert "seedcast" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}


def test_large_responses_are_gzipped(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1);" * 500)
    (tmp_path / "seedcast.html").write_text("<html>seedcast</html>")
    client = TestClient(create_app(cfg(static_dir=str(tmp_path))))
    r = client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
