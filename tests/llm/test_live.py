"""실제 API 키로 두 공급자를 부르는 점검 테스트.

기본 실행(`uv run pytest`)에서는 `-m "not live"`로 빠진다. 실행: `uv run pytest -m live`.
키(`DODEED_OPENAI_API_KEY`, `DODEED_GEMINI_API_KEY`)는 환경변수나 `.env`에서 읽고,
없는 공급자의 테스트는 건너뛴다. 요청은 모두 GuardedLLM을 거친다.
"""

import json
import re
import secrets

import httpx
import pytest
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dodeed.agent.prompts import SYSTEM
from dodeed.llm.gemini import GeminiClient
from dodeed.llm.guarded import GuardedLLM
from dodeed.llm.openai_chat import OpenAIChatClient
from dodeed.llm.types import Message, ToolDef
from dodeed.restaurant.candidates import SearchRequest
from dodeed.restaurant.tools import SEARCH_RESTAURANTS
from dodeed.security.egress import SecretRegistry

pytestmark = pytest.mark.live

HANGUL = re.compile(r"[가-힣]")


class LiveKeys(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DODEED_", env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_model: str = "gpt-6-luna"
    gemini_model: str = "gemini-3.5-flash-lite"

    @field_validator("openai_api_key", "gemini_api_key", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_db() -> None:
    """루트 conftest의 DB 마이그레이션을 덮어쓴다: live 테스트는 DB가 필요 없다."""


@pytest.fixture(params=["openai", "gemini"])
async def llm(request):
    keys = LiveKeys()
    key = keys.openai_api_key if request.param == "openai" else keys.gemini_api_key
    if not key:
        pytest.skip(f"DODEED_{request.param.upper()}_API_KEY가 없어 건너뜁니다")
    registry = SecretRegistry(secrets.token_bytes(32))
    for k in (keys.openai_api_key, keys.gemini_api_key):
        if k:
            registry.register(k)
    async with httpx.AsyncClient() as http:
        if request.param == "openai":
            inner = OpenAIChatClient(http, key, keys.openai_model, timeout_s=60.0)
        else:
            inner = GeminiClient(http, key, keys.gemini_model, timeout_s=60.0)
        yield inner, GuardedLLM(inner, registry, "orchestrator")


def _tool_def() -> ToolDef:
    return ToolDef(
        SEARCH_RESTAURANTS.name,
        SEARCH_RESTAURANTS.description,
        SEARCH_RESTAURANTS.input_model.model_json_schema(),
    )


async def test_korean_chat(llm):
    inner, guarded = llm
    resp = await guarded.complete(
        [
            Message(role="system", content=SYSTEM),
            Message(role="user", content="안녕! 한 문장으로 자기소개해 줘."),
        ]
    )
    assert resp.text and HANGUL.search(resp.text)
    assert resp.tool_calls == []
    assert inner.last_usage is not None and inner.last_usage["input_tokens"] > 0


async def test_search_restaurants_tool_call(llm):
    _, guarded = llm
    resp = await guarded.complete(
        [
            Message(role="system", content=SYSTEM),
            Message(role="user", content="강남역에서 혼밥하기 좋은 국밥집 찾아줘"),
        ],
        tools=[_tool_def()],
    )
    calls = [tc for tc in resp.tool_calls if tc.name == SEARCH_RESTAURANTS.name]
    assert calls, "search_restaurants를 부르지 않았습니다"
    req = SearchRequest.model_validate(calls[0].arguments)
    assert req.area_query and "강남" in req.area_query


async def test_structured_output(llm):
    _, guarded = llm
    schema = {
        "type": "object",
        "properties": {
            "situation": {"type": "string", "enum": ["solo", "date", "value", "none"]},
            "cuisine": {"type": "string"},
        },
        "required": ["situation", "cuisine"],
        "additionalProperties": False,
    }
    resp = await guarded.complete(
        [
            Message(
                role="system", content="사용자 요청에서 상황과 음식 종류를 뽑아 JSON으로 답한다."
            ),
            Message(role="user", content="여자친구랑 성수에서 파스타 먹고 싶어"),
        ],
        json_schema=schema,
    )
    assert resp.text
    data = json.loads(resp.text)
    assert data["situation"] in {"solo", "date", "value", "none"}
    assert isinstance(data["cuisine"], str) and data["cuisine"]
