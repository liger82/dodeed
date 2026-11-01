from dodeed.channels.messages import OutText


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


async def test_503_when_gateway_missing(client, signed_up_user_id):
    await client.post("/api/login", json={"email": "svc@example.com", "password": "pw-12345678"})
    r = await client.post("/api/chat", json={"text": "hi", "event_id": "e"})
    assert r.status_code == 503
