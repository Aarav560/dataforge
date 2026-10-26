import pytest
from fastapi.testclient import TestClient

from app import auth
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Asset, DataNeed, DatasetVersion, Export, Project, ReviewItem, SearchResult


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_enabled", True)
    auth.throttle.failures.clear()


def signup(email, pw="correct horse battery"):
    c = TestClient(app)
    c.__enter__()
    r = c.post("/api/auth/signup", json={"email": email, "password": pw, "name": email.split("@")[0]})
    assert r.status_code == 201, r.text
    return c


def test_password_hashing():
    h = auth.hash_password("s3cret-password")
    assert h.startswith("scrypt$") and auth.verify_password("s3cret-password", h)
    assert not auth.verify_password("wrong", h) and not auth.verify_password("x", "garbage")


def test_signup_login_logout(auth_on):
    with TestClient(app) as c:
        assert c.get("/api/projects").status_code == 401
        assert c.post("/api/auth/signup", json={"email": "bad", "password": "correct horse battery"}).status_code == 422
        assert c.post("/api/auth/signup", json={"email": "a@x.io", "password": "short"}).status_code == 422
        assert c.post("/api/auth/signup", json={"email": "A@x.io", "password": "correct horse battery"}).status_code == 201
        assert c.get("/api/auth/me").json()["email"] == "a@x.io"
        assert c.post("/api/auth/signup", json={"email": "a@x.io", "password": "correct horse battery"}).status_code == 409
        c.post("/api/auth/logout")
        assert c.get("/api/auth/me").status_code == 401
        assert c.post("/api/auth/login", json={"email": "a@x.io", "password": "nope"}).status_code == 401
        r = c.post("/api/auth/login", json={"email": "a@x.io", "password": "correct horse battery"})
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/projects").status_code == 200


def test_login_throttle(auth_on):
    with TestClient(app) as c:
        c.post("/api/auth/signup", json={"email": "t@x.io", "password": "correct horse battery"})
        for _ in range(5):
            assert c.post("/api/auth/login", json={"email": "t@x.io", "password": "bad"}).status_code == 401
        assert c.post("/api/auth/login", json={"email": "t@x.io", "password": "correct horse battery"}).status_code == 429


def test_signup_closed(auth_on, monkeypatch):
    monkeypatch.setattr(get_settings(), "open_signup", False)
    signup("first@x.io")
    with TestClient(app) as c:
        assert c.post("/api/auth/signup", json={"email": "second@x.io", "password": "correct horse battery"}).status_code == 403


def _seed_children(project_id: int) -> dict:
    with SessionLocal() as db:
        need = db.query(DataNeed).filter_by(project_id=project_id).first()
        result = SearchResult(need_id=need.id, source="huggingface", kind="dataset", external_id="a/b", url="u", title="a/b")
        db.add(result)
        db.flush()
        asset = Asset(result_id=result.id, status="done", storage_path="/nonexistent")
        version = DatasetVersion(project_id=project_id, version=1, status="done", storage_path="/nonexistent")
        db.add_all([asset, version])
        db.flush()
        export = Export(version_id=version.id, format="csv", status="done", zip_path="/nonexistent")
        review = ReviewItem(version_id=version.id, row_index=0, payload={"text": "x"})
        db.add_all([export, review])
        db.commit()
        return {"need": need.id, "result": result.id, "asset": asset.id, "version": version.id,
                "export": export.id, "review": review.id}


def test_projects_are_private(auth_on):
    alice, bob = signup("alice@x.io"), signup("bob@x.io")
    pid = alice.post("/api/projects", json={"goal": "tetris ai"}).json()["id"]
    ids = _seed_children(pid)
    assert [p["id"] for p in alice.get("/api/projects").json()] == [pid]
    assert bob.get("/api/projects").json() == []
    for url in [f"/api/projects/{pid}", f"/api/projects/{pid}/results", f"/api/projects/{pid}/versions",
                f"/api/projects/{pid}/stats", f"/api/results/{ids['result']}/preview", f"/api/assets/{ids['asset']}/files",
                f"/api/versions/{ids['version']}", f"/api/versions/{ids['version']}/reviews",
                f"/api/exports/{ids['export']}/download"]:
        assert bob.get(url).status_code == 404, url
    assert bob.patch(f"/api/needs/{ids['need']}", json={"description": "x"}).status_code == 404
    assert bob.post(f"/api/reviews/{ids['review']}", json={"action": "reject"}).status_code == 404
    assert bob.post(f"/api/projects/{pid}/search").status_code == 404
    assert bob.post("/api/license-matrix", json={"result_ids": [ids["result"]]}).json() == []
    assert alice.post("/api/license-matrix", json={"result_ids": [ids["result"]]}).json()[0]["title"] == "a/b"
    with pytest.raises(Exception):
        with bob.websocket_connect(f"/api/projects/{pid}/ws") as ws:
            ws.receive_json()


def test_team_sharing_roles(auth_on):
    alice, bob, carol = signup("alice@x.io"), signup("bob@x.io"), signup("carol@x.io")
    team = alice.post("/api/teams", json={"name": "Lab"}).json()
    assert team["role"] == "owner"
    assert alice.post(f"/api/teams/{team['id']}/members", json={"email": "nobody@x.io"}).status_code == 404
    alice.post(f"/api/teams/{team['id']}/members", json={"email": "bob@x.io", "role": "viewer"})
    alice.post(f"/api/teams/{team['id']}/members", json={"email": "carol@x.io", "role": "editor"})
    assert bob.post(f"/api/teams/{team['id']}/members", json={"email": "carol@x.io", "role": "owner"}).status_code == 403

    pid = alice.post("/api/projects", json={"goal": "tetris ai"}).json()["id"]
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert bob.put(f"/api/projects/{pid}/team", json={"team_id": team["id"]}).status_code == 404
    assert alice.put(f"/api/projects/{pid}/team", json={"team_id": team["id"]}).json()["team_id"] == team["id"]

    need = bob.get(f"/api/projects/{pid}").json()["needs"][0]["id"]  # viewer can read
    assert bob.patch(f"/api/needs/{need}", json={"description": "x"}).status_code == 403
    assert carol.patch(f"/api/needs/{need}", json={"description": "edited"}).status_code == 200
    assert carol.delete(f"/api/projects/{pid}").status_code == 403  # editors can't delete
    assert carol.post("/api/projects", json={"goal": "team project", "team_id": team["id"]}).status_code == 201
    assert bob.post("/api/projects", json={"goal": "nope", "team_id": team["id"]}).status_code == 403

    assert alice.patch(f"/api/teams/{team['id']}/members/{team['members'][0]['user_id']}", json={"role": "editor"}).status_code == 409
    assert bob.delete(f"/api/teams/{team['id']}/members/{bob.get('/api/auth/me').json()['id']}").status_code == 204
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert alice.delete(f"/api/projects/{pid}").status_code == 204


def test_bearer_token(auth_on):
    with TestClient(app) as c:
        c.post("/api/auth/signup", json={"email": "api@x.io", "password": "correct horse battery"})
        token = c.cookies.get(auth.COOKIE)
    with TestClient(app) as anon:
        assert anon.get("/api/projects", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert anon.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_issue_connector_token(auth_on):
    with TestClient(app) as c:
        c.post("/api/auth/signup", json={"email": "bot@x.io", "password": "correct horse battery"})
        token = c.post("/api/auth/token").json()["token"]
    with TestClient(app) as anon:
        assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email"] == "bot@x.io"
