from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    provider: str | None = None  # 이 호출을 만든 공급자 ("openai", "gemini"). 직접 만든 호출은 None
    thought_signature: str | None = None  # Gemini가 준 불투명한 사고 서명. 그대로 돌려보낸다


@dataclass
class Message:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant의 도구 호출 턴


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
