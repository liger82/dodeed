import httpx

from dodeed.app import create_app
from dodeed.channels.messages import OutText
from dodeed.db import get_db


async def test_chat_requires_login(channel_client):
    r = await channel_client.post("/api/chat", json={"text": "hi", "event_id": "e1"})
    assert r.status_code == 401
    r = await channel_client.post("/api/chat/location", json={"lat": 1, "lng": 2, "event_id": "e1"})
    assert r.status_code == 401


async def test_chat_returns_outbound_messages(logged_in_client, orch, signed_up_user_id):
    r = await logged_in_client.post("/api/chat", json={"text": "맛집", "event_id": "e1"})
    assert r.status_code == 200
    assert r.json() == {"messages": [OutText(text="echo:맛집").model_dump()]}
    assert orch.calls[0][0] == signed_up_user_id


async def test_chat_duplicate_event_returns_empty(logged_in_client, orch):
    await logged_in_client.post("/api/chat", json={"text": "a", "event_id": "dup"})
    r = await logged_in_client.post("/api/chat", json={"text": "a", "event_id": "dup"})
    assert r.json() == {"messages": []}
    assert len(orch.calls) == 1


async def test_chat_text_too_long_422(logged_in_client):
    r = await logged_in_client.post("/api/chat", json={"text": "a" * 2001, "event_id": "e"})
    assert r.status_code == 422
    r = await logged_in_client.post("/api/chat", json={"text": "a" * 2000, "event_id": "e"})
    assert r.status_code == 200


async def test_location_saved(logged_in_client):
    r = await logged_in_client.post(
        "/api/chat/location", json={"lat": 37.5, "lng": 127.0, "event_id": "l1"}
    )
    assert r.status_code == 200
    assert r.json() == {"messages": [OutText(text="위치를 받았어요.").model_dump()]}


async def test_location_range_validation(logged_in_client):
    for lat, lng in [(91, 0), (-91, 0), (0, 181), (0, -181)]:
        r = await logged_in_client.post(
            "/api/chat/location", json={"lat": lat, "lng": lng, "event_id": "x"}
        )
        assert r.status_code == 422, (lat, lng)


async def test_503_when_gateway_missing(settings, db_session, signed_up_user_id):
    app = create_app(settings)
    app.state.gateway = None  # 조립이 빠진 경우
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/login", json={"email": "svc@example.com", "password": "pw-12345678"}
        )
        r = await client.post("/api/chat", json={"text": "hi", "event_id": "e"})
    assert r.status_code == 503


async def test_same_event_id_for_different_users_both_processed(
    channel_client, orch, db_session, signed_up_user_id
):
    from dodeed.accounts.service import signup

    await signup(db_session, "b@example.com", "pw-12345678", "비")
    out = []
    for email in ("svc@example.com", "b@example.com"):
        channel_client.cookies.clear()
        r = await channel_client.post(
            "/api/login", json={"email": email, "password": "pw-12345678"}
        )
        assert r.status_code == 200
        r = await channel_client.post("/api/chat", json={"text": "hi", "event_id": "1"})
        out.append(r.json())
    assert all(o == {"messages": [OutText(text="echo:hi").model_dump()]} for o in out)
    assert len(orch.calls) == 2
    assert orch.calls[0][0] != orch.calls[1][0]
