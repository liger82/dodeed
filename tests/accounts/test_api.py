BODY = {"email": "a@example.com", "password": "pw-12345678", "display_name": "가나다"}


async def test_signup_duplicate_email_rejected(client):
    assert (await client.post("/api/signup", json=BODY)).status_code == 200
    assert (await client.post("/api/signup", json=BODY)).status_code == 409


async def test_link_code_requires_login(client):
    assert (await client.post("/api/link-codes")).status_code == 401
    assert (await client.delete("/api/channels/telegram")).status_code == 401


async def test_login_wrong_password_401(client):
    await client.post("/api/signup", json=BODY)
    r = await client.post("/api/login", json={"email": BODY["email"], "password": "nope"})
    assert r.status_code == 401
    r = await client.post("/api/login", json={"email": "x@example.com", "password": "nope"})
    assert r.status_code == 401


async def test_forged_cookie_rejected(client):
    client.cookies.set("dodeed_session", "forged")
    assert (await client.post("/api/link-codes")).status_code == 401


async def test_login_then_issue_code_and_unlink(client):
    await client.post("/api/signup", json=BODY)
    r = await client.post("/api/login", json={"email": BODY["email"], "password": BODY["password"]})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    r = await client.post("/api/link-codes")
    assert r.status_code == 200
    assert len(r.json()["code"]) == 6 and r.json()["expires_at"]
    assert (await client.delete("/api/channels/telegram")).status_code == 204


async def test_signup_short_password_422(client):
    r = await client.post("/api/signup", json={**BODY, "password": "1234567"})
    assert r.status_code == 422
    r = await client.post("/api/signup", json={**BODY, "password": "12345678"})
    assert r.status_code == 200
