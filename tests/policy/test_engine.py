import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from dodeed.audit.models import AuditLog
from dodeed.policy.engine import Decision, PolicyEngine, get_level, set_level
from dodeed.policy.tools import Grade, PermissionLevel, ToolRegistry, ToolSpec


class _In(BaseModel):
    q: str = ""


def _spec(name, grade=Grade.WRITE, **kw) -> ToolSpec:
    return ToolSpec(name=name, service="svc", grade=grade, input_model=_In, description="d", **kw)


async def _noop(ctx, inp):
    return inp


@pytest.fixture
def specs(monkeypatch):
    """TASK_TOOLSETS에 테스트용 도구를 허용해 둔다."""
    from dodeed.policy import tools

    monkeypatch.setitem(
        tools.TASK_TOOLSETS,
        "t",
        frozenset({"r", "w", "pay", "w_instant", "search_restaurants"}),
    )


@pytest.fixture
def reg():
    r = ToolRegistry()
    r.register(_spec("w_instant", instant_allowed=True), _noop)
    return r


async def check(db, uid, spec, token=None, task="t"):
    return (await PolicyEngine().check(db, uid, task, spec, token)).decision


async def test_tool_outside_taskset_denied(db_session, signed_up_user_id, specs):
    assert await check(db_session, signed_up_user_id, _spec("x", Grade.READ)) == Decision.DENY
    assert (
        await check(db_session, signed_up_user_id, _spec("r", Grade.READ), task="nope")
        == Decision.DENY
    )


async def test_read_tool_allowed(db_session, signed_up_user_id, specs):
    assert await check(db_session, signed_up_user_id, _spec("r", Grade.READ)) == Decision.ALLOW


async def test_write_tool_denied_when_read_only(db_session, signed_up_user_id, specs, reg):
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.READ_ONLY, reg)
    assert await check(db_session, signed_up_user_id, _spec("w")) == Decision.DENY


async def test_write_tool_needs_approval_by_default(db_session, signed_up_user_id, specs):
    assert await get_level(db_session, signed_up_user_id, "svc") == PermissionLevel.APPROVE
    assert await check(db_session, signed_up_user_id, _spec("w")) == Decision.NEEDS_APPROVAL


async def test_payment_needs_approval_even_when_instant(db_session, signed_up_user_id, specs, reg):
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.INSTANT, reg)
    spec = _spec("pay", instant_allowed=True, is_payment=True)
    assert await check(db_session, signed_up_user_id, spec) == Decision.NEEDS_APPROVAL


async def test_instant_only_for_instant_allowed_tool(db_session, signed_up_user_id, specs, reg):
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.INSTANT, reg)
    assert (
        await check(db_session, signed_up_user_id, _spec("w_instant", instant_allowed=True))
        == Decision.ALLOW
    )
    assert await check(db_session, signed_up_user_id, _spec("w")) == Decision.NEEDS_APPROVAL


async def test_approval_token_denied_until_service_exists(db_session, signed_up_user_id, specs):
    result = await PolicyEngine().check(db_session, signed_up_user_id, "t", _spec("w"), "tok")
    assert result.decision == Decision.DENY
    assert result.reason == "승인 서비스 미구현"


async def test_set_level_instant_requires_instant_allowed_tool(db_session, signed_up_user_id):
    with pytest.raises(ValueError):
        await set_level(
            db_session, signed_up_user_id, "svc", PermissionLevel.INSTANT, ToolRegistry()
        )


async def test_set_level_upserts(db_session, signed_up_user_id, reg):
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.READ_ONLY, reg)
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.APPROVE, reg)
    assert await get_level(db_session, signed_up_user_id, "svc") == PermissionLevel.APPROVE


async def test_every_check_is_audited(db_session, signed_up_user_id, specs):
    for s in (_spec("r", Grade.READ), _spec("x"), _spec("w")):
        await check(db_session, signed_up_user_id, s)
    count = await db_session.scalar(select(func.count()).select_from(AuditLog))
    assert count == 3


async def test_registry(reg):
    with pytest.raises(ValueError):
        reg.register(_spec("w_instant"), _noop)
    with pytest.raises(KeyError):
        reg.get("nope")
    reg.register(_spec("b", Grade.READ), _noop)
    defs = reg.tool_defs(["b", "w_instant"])
    assert [d.name for d in defs] == ["b", "w_instant"]
    assert defs[0].parameters == _In.model_json_schema()


async def test_payment_with_token_denied_at_instant(db_session, signed_up_user_id, specs, reg):
    await set_level(db_session, signed_up_user_id, "svc", PermissionLevel.INSTANT, reg)
    spec = _spec("pay", instant_allowed=True, is_payment=True)
    assert await check(db_session, signed_up_user_id, spec, "tok") == Decision.DENY


async def test_payment_with_token_denied_at_approve(db_session, signed_up_user_id, specs):
    spec = _spec("pay", is_payment=True)
    assert await check(db_session, signed_up_user_id, spec, "tok") == Decision.DENY
