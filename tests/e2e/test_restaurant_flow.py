import json
from datetime import UTC, datetime

import httpx
import pytest

from dodeed.accounts.service import issue_link_code
from dodeed.app import build_orchestrator, create_app
from dodeed.channels.gateway import MSG_LINKED, Gateway
from dodeed.db import get_db
from dodeed.llm.types import LLMResponse, Message
from dodeed.sessions.service import get_or_create, get_state
from tests.agent.helpers import ScriptedLLM, text, tool_call
from tests.restaurant.conftest import PlaceFx, own_money_blogs

HDR = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}
TG_UID = 777


def tg_text(update_id: int, body: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": TG_UID},
            "chat": {"id": TG_UID},
            "text": body,
        },
    }


def answer_second_candidate(messages: list[Message]) -> LLMResponse:
    """시스템 메시지의 이전 조회 요약에서 두 번째 후보 이름을 꺼내 답한다."""
    for m in messages:
        if m.role == "system" and '"previous_search"' in m.content:
            data = json.loads(m.content[m.content.index("{") :])
            second = next(c for c in data["previous_search"]["candidates"] if c["index"] == 2)
            return text(f"두 번째 집은 {second['name']}이에요.")
    return text("이전 조회 결과가 없어요.")


@pytest.fixture
async def e2e_client(
    settings, db_session, redis_client, fake_bot, http, scenario, schema_reader_llm
):
    orch_llm = ScriptedLLM(
        [
            tool_call("search_restaurants", {"area_query": "강남역", "situation": "solo"}),
            text("강남역 근처 혼밥하기 좋은 곳을 찾았어요."),
            answer_second_candidate,
        ]
    )
    orch = build_orchestrator(
        settings,
        http=http,
        page_reader=scenario.page_reader,
        orchestrator_llm=orch_llm,
        reader_llm=schema_reader_llm,
    )
    app = create_app(settings, gateway=Gateway(orch, settings, redis_client), telegram_bot=fake_bot)
    app.dependency_overrides[get_db] = lambda: db_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.orch_llm = orch_llm
        yield c


async def test_telegram_search_then_web_continues_same_candidates(
    e2e_client, db_session, signed_up_user_id, fake_bot, scenario, schema_reader_llm
):
    scenario.add(
        *(
            PlaceFx(str(i), f"{n}집", i, blog=own_money_blogs(3))
            for i, n in enumerate(["가", "나", "다", "라"], start=1)
        )
    )

    # 1. 텔레그램 계정 연결
    code = await issue_link_code(db_session, signed_up_user_id, datetime.now(UTC))
    r = await e2e_client.post("/telegram/webhook", json=tg_text(1, f"/link {code}"), headers=HDR)
    assert r.status_code == 200
    assert fake_bot.calls[-1][1]["text"] == MSG_LINKED

    # 2. 텔레그램에서 조회 → 안내 문장 다음에 카드 3~5장
    fake_bot.calls.clear()
    r = await e2e_client.post("/telegram/webhook", json=tg_text(2, "강남역 근처 혼밥"), headers=HDR)
    assert r.status_code == 200
    sent = [kw for name, kw in fake_bot.calls if name == "send_message"]
    assert sent[0]["text"] == "강남역 근처 혼밥하기 좋은 곳을 찾았어요."
    cards = [kw for kw in sent[1:] if kw.get("parse_mode") == "HTML"]
    assert 3 <= len(cards) <= 5 and len(cards) == len(sent) - 1
    assert schema_reader_llm.calls  # 외부 원문은 격리 리더가 읽었다

    conv = await get_or_create(db_session, signed_up_user_id, "web")
    second_name = (await get_state(db_session, conv.id))["candidates"]["candidates"][1]["name"]
    assert f"<b>{second_name}</b>" in cards[1]["text"]

    # 3. 같은 사용자가 웹에서 이어서 묻는다 → 같은 세션의 후보를 쓴다
    searches = len(scenario.keyword_queries)
    r = await e2e_client.post(
        "/api/login", json={"email": "svc@example.com", "password": "pw-12345678"}
    )
    assert r.status_code == 200
    r = await e2e_client.post("/api/chat", json={"text": "두 번째 집 알려줘", "event_id": "w1"})
    assert r.status_code == 200
    messages = r.json()["messages"]
    assert messages == [{"kind": "text", "text": f"두 번째 집은 {second_name}이에요."}]
    assert len(scenario.keyword_queries) == searches  # 다시 검색하지 않았다
