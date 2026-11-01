# dodeed LLM 공급자 연결 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 고정 응답 LLM(StaticLLM)을 실제 모델로 바꾼다. 1차 모델은 gpt-6-luna(OpenAI)이고, 1차 모델이 실패하면 gemini-3.5-flash-lite(Google)로 그 호출만 다시 보낸다.

**Architecture:** 두 공급자 모두 공식 SDK 없이 httpx로 REST API를 직접 호출하는 클라이언트를 만든다. 둘 다 기존 `LLMClient` 프로토콜을 구현한다. `FallbackLLM`이 두 클라이언트를 묶고, 앱은 지금처럼 그 바깥을 `GuardedLLM`으로 감싼다. 그래서 출구 필터는 공급자 전환보다 먼저, 한 번만 적용된다.

**Tech Stack:** Python 3.12, httpx(앱이 이미 쓰는 클라이언트 재사용), respx(테스트), pydantic-settings.

**Spec:** `docs/superpowers/specs/2026-11-01-dodeed-memory-skills-scheduling-design.md` §8 (그리고 `docs/superpowers/specs/2026-10-25-dodeed-platform-design.md` REQ-SEC-001, 002, 008)

## Global Constraints

- 1차 모델 `gpt-6-luna`, 백업 모델 `gemini-3.5-flash-lite`. 모델 이름은 설정값이다.
- OpenAI는 Chat Completions API(`POST https://api.openai.com/v1/chat/completions`)를 쓰고 모든 요청에 `"store": false`를 넣는다.
- Gemini는 Gemini API 결제 등급 키만 쓴다. API 키는 URL 쿼리(`?key=`)가 아니라 헤더 `x-goog-api-key`로 보낸다 (키가 URL과 로그에 남지 않게).
- 백업 전환 조건은 1차 모델의 **시간 초과, 429, 5xx, 연결 오류**뿐이다 (스펙 §8). 그 밖의 4xx는 전환하지 않는다.
- 두 API 키(`DODEED_OPENAI_API_KEY`, `DODEED_GEMINI_API_KEY`)는 출구 필터에 비밀값으로 등록한다 (REQ-SEC-001).
- 오류 메시지와 로그에는 API 키, 요청 본문, 응답 본문을 넣지 않는다. 상태 코드와 예외 종류 이름만 넣는다.
- 출구 필터(`GuardedLLM`)는 `FallbackLLM` 바깥에 둔다. 공급자 클라이언트 안에서 egress 검사를 다시 하지 않는다.
- `ruff format`은 `src/`, `tests/`, `scripts/`에만 돌린다. `docs/`에는 돌리지 않는다.
- 실제 API 키가 없어도 기본 테스트(`uv run pytest`)가 모두 통과해야 한다.

## Review Focus

1. **모델이 깨진 JSON 인자로 도구를 호출함:** OpenAI 응답의 `tool_calls[].function.arguments`가 JSON이 아니면 공급자 오류로 보고 백업으로 넘긴다. → Task 2 `test_invalid_tool_arguments_raise_provider_error`, Task 4 `test_invalid_primary_output_falls_back`
2. **두 공급자가 모두 실패함:** 예외가 오케스트레이터까지 올라가서, 이미 있는 오류 처리("잠시 문제가 생겼어요…")로 끝난다. 서버가 500을 내지 않는다. → Task 4 `test_both_fail_user_gets_retry_message`
3. **한 대화 안에서 공급자가 바뀜:** OpenAI가 만든 도구 호출 id가 있는 기록을 Gemini가 이어받아도 도구 결과가 올바른 함수 이름에 연결된다. → Task 3 `test_tool_result_mapped_to_function_name_by_call_id`
4. **안전 필터로 내용 없이 끝난 응답:** OpenAI `finish_reason: "content_filter"`, Gemini `finishReason: "SAFETY"`이면 오류가 아니라 `LLMResponse(text=None, tool_calls=[])`를 돌려준다. → Task 2 `test_content_filter_returns_empty`, Task 3 `test_safety_block_returns_empty`
5. **API 키가 오류 메시지에 섞임:** 401·500 응답과 연결 오류의 예외 문자열에 키가 없다. → Task 2·3 `test_errors_do_not_contain_api_key`

---

## 파일 구조

```
src/dodeed/config.py                 # 설정 추가 (Task 1)
src/dodeed/security/egress.py        # registry_from_settings에 키 등록 (Task 1)
src/dodeed/llm/errors.py             # LLMProviderError, LLMRequestError (Task 2)
src/dodeed/llm/openai_chat.py        # OpenAIChatClient (Task 2)
src/dodeed/llm/gemini.py             # GeminiClient (Task 3)
src/dodeed/llm/fallback.py           # FallbackLLM (Task 4)
src/dodeed/app.py                    # _make_llm 연결 (Task 4)
tests/llm/test_openai_chat.py, test_gemini.py, test_fallback.py, test_live.py
scripts/llm_ko_eval.py               # 한국어 품질 비교 (Task 5)
scripts/llm_ko_eval_cases.json
.env.example, README.md
```

---

### Task 1: 설정과 출구 필터 등록

**Files:**
- Modify: `src/dodeed/config.py`, `src/dodeed/security/egress.py`, `.env.example`, `README.md`
- Test: `tests/test_config.py`, `tests/security/test_egress.py`

**Interfaces:**
- Produces (`Settings`에 추가):
  - `llm: Literal["fake", "live"] = "fake"`
  - `openai_api_key: str | None = Field(default=None, min_length=8)`
  - `gemini_api_key: str | None = Field(default=None, min_length=8)`
  - `openai_model: str = "gpt-6-luna"`
  - `gemini_model: str = "gemini-3.5-flash-lite"`
  - `llm_timeout_s: float = 60.0`
  - `openai_reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None` (None이면 요청에 넣지 않는다)
  - `model_validator(mode="after")`: `llm == "live"`인데 두 키 중 하나라도 없으면 `ValueError`
- Produces (`egress.py`): `registry_from_settings`가 `openai_api_key`, `gemini_api_key`가 있으면 등록한다.
- `config.py`의 "기본값이 있는 유일한 설정" 주석은 지금 상태에 맞게 고친다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_live_requires_both_keys(): ...          # llm="live", openai만 → ValidationError, gemini만 → ValidationError
def test_live_with_both_keys_ok(): ...
def test_fake_needs_no_llm_keys(): ...
def test_llm_key_min_length(): ...               # 7자 키 → ValidationError
def test_model_names_default(): ...              # openai_model == "gpt-6-luna", gemini_model == "gemini-3.5-flash-lite"
def test_llm_keys_registered_as_secrets(): ...   # registry_from_settings(live 설정).contains_in(f"x={openai_key}") is True, gemini도
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_config.py tests/security/test_egress.py -v` / Expected: 새 테스트 FAIL
- [ ] **Step 3: 구현.** `.env.example`에 `DODEED_LLM=fake`, 빈 `DODEED_OPENAI_API_KEY=`, `DODEED_GEMINI_API_KEY=`와 주석(“live로 바꾸려면 두 키가 모두 필요, Gemini는 결제 등급 키”)을 넣는다. README 환경 변수 표와 "현재 한계"를 고친다 (StaticLLM은 `fake`일 때만).
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` / Expected: 전체 PASS
- [ ] **Step 5: 커밋** — `git commit -m "feat: LLM 공급자 설정과 API 키 출구 필터 등록"`

---

### Task 2: OpenAI Chat Completions 클라이언트

**Files:**
- Create: `src/dodeed/llm/errors.py`, `src/dodeed/llm/openai_chat.py`
- Test: `tests/llm/test_openai_chat.py`

**Interfaces:**
- Consumes: `Message`, `ToolCall`, `ToolDef`, `LLMResponse` (`dodeed.llm.types`)
- Produces (`errors.py`):
  - `class LLMProviderError(Exception)`: 전환 대상(시간 초과, 연결 오류, 429, 5xx, 응답 형식 오류). 속성 `provider: str`, `kind: str` (`"timeout" | "connection" | "rate_limited" | "server_error" | "bad_output"`), `status: int | None`
  - `class LLMRequestError(Exception)`: 전환하지 않는 오류(그 밖의 4xx). 속성 `provider`, `status`
  - 두 예외의 `str()`은 `f"{provider} {kind or status}"` 형태로만 만든다.
- Produces (`openai_chat.py`):
  - `class OpenAIChatClient`: `__init__(self, http: httpx.AsyncClient, api_key: str, model: str, timeout_s: float, reasoning_effort: str | None = None)`
  - `async def complete(self, messages, tools=None, json_schema=None) -> LLMResponse`
  - `BASE_URL = "https://api.openai.com/v1/chat/completions"`

요청 변환 규칙:
- `Message(role="system"|"user")` → `{"role", "content"}`
- `Message(role="assistant", tool_calls=[...])` → `{"role": "assistant", "content": content or None, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments": json.dumps(arguments, ensure_ascii=False)}}]}`
- `Message(role="tool")` → `{"role": "tool", "tool_call_id", "content"}`
- `tools` → `[{"type": "function", "function": {"name", "description", "parameters"}}]`
- `json_schema` → `"response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": json_schema, "strict": false}}`
- 항상 `"model"`, `"store": false`. `reasoning_effort`가 있으면 넣는다. 헤더 `Authorization: Bearer {api_key}`.

응답 변환 규칙:
- `choices[0].message.content` → `text` (빈 문자열은 None)
- `choices[0].message.tool_calls[]` → `ToolCall(id, name, arguments=json.loads(...))`. JSON이 아니거나 dict가 아니면 `LLMProviderError(kind="bad_output")`
- `finish_reason == "content_filter"` → `LLMResponse(text=None, tool_calls=[])`
- 응답에 `choices`가 없거나 형식이 다르면 `LLMProviderError(kind="bad_output")`
- `httpx.TimeoutException` → timeout, `httpx.TransportError` → connection, 429 → rate_limited, ≥500 → server_error, 그 밖의 4xx → `LLMRequestError`

- [ ] **Step 1: 실패하는 테스트 작성** (respx로 `BASE_URL` 흉내)

```python
async def test_request_shape_has_store_false_and_tools(respx_mock): ...
    # 보낸 JSON: model == "gpt-6-luna", store is False, tools[0]["type"] == "function", 메시지 역할 순서 보존
async def test_assistant_tool_call_and_tool_result_round_trip(respx_mock): ...
async def test_json_schema_sent_as_response_format(respx_mock): ...
async def test_parses_text_and_tool_calls(respx_mock): ...
async def test_invalid_tool_arguments_raise_provider_error(respx_mock): ...
async def test_content_filter_returns_empty(respx_mock): ...
@pytest.mark.parametrize("status,kind", [(429, "rate_limited"), (500, "server_error"), (503, "server_error")])
async def test_retryable_status_raises_provider_error(respx_mock, status, kind): ...
async def test_400_raises_request_error(respx_mock): ...
async def test_timeout_and_connection_errors(respx_mock): ...
async def test_errors_do_not_contain_api_key(respx_mock): ...   # 401/500/연결 오류의 str(exc)에 키 없음
async def test_reasoning_effort_only_when_set(respx_mock): ...
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/llm/test_openai_chat.py -v` / Expected: FAIL (모듈 없음)
- [ ] **Step 3: 구현** — 요청마다 `timeout=timeout_s`를 넘긴다. 앱의 공유 httpx 클라이언트를 쓰므로 클라이언트를 닫지 않는다.
- [ ] **Step 4: 통과 확인** — Expected: PASS
- [ ] **Step 5: 커밋** — `git commit -m "feat: OpenAI Chat Completions 클라이언트 (store=false)"`

---

### Task 3: Gemini API 클라이언트

**Files:**
- Create: `src/dodeed/llm/gemini.py`
- Test: `tests/llm/test_gemini.py`

**Interfaces:**
- Consumes: `LLMProviderError`, `LLMRequestError` (Task 2), `dodeed.llm.types`
- Produces:
  - `class GeminiClient`: `__init__(self, http: httpx.AsyncClient, api_key: str, model: str, timeout_s: float)`
  - `async def complete(self, messages, tools=None, json_schema=None) -> LLMResponse`
  - URL: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`, 헤더 `x-goog-api-key`

- [ ] **Step 1: REST 필드 확인.** 구현 전에 Google 공식 문서(ai.google.dev의 Gemini API REST 레퍼런스: generateContent, function calling, structured output)에서 아래 필드 이름을 확인하고, 모듈 docstring에 확인 날짜와 함께 적는다. 문서와 다르면 문서를 따른다.
  - 시스템 메시지: `systemInstruction: {parts: [{text}]}` (system 메시지가 여러 개면 줄바꿈으로 합친다)
  - 대화: `contents: [{role: "user"|"model", parts: [...]}]`
  - 도구 정의: `tools: [{functionDeclarations: [{name, description, parametersJsonSchema}]}]` (JSON Schema를 그대로 받는 필드. 없으면 `parameters`와 스키마 변환이 필요하므로 보고한다)
  - 도구 호출: model 파트 `{functionCall: {name, args}}`, 도구 결과: user 파트 `{functionResponse: {name, response: {...}}}`
  - 구조화 출력: `generationConfig: {responseMimeType: "application/json", responseJsonSchema: ...}`
  - 응답: `candidates[0].content.parts`, `candidates[0].finishReason`

변환 규칙 [이 계획의 결정]:
- `assistant` → role `model`. 텍스트는 `{text}` 파트, 도구 호출은 `functionCall` 파트
- `tool` 메시지 → role `user`의 `functionResponse` 파트. 함수 이름은 **같은 요청 안의 앞선 assistant 메시지에서 `tool_call_id`로 찾는다.** 못 찾으면 `LLMRequestError`. `response`에는 도구 결과 문자열을 JSON으로 파싱한 dict를 넣고, dict가 아니면 `{"result": <문자열>}`로 감싼다.
- 연속된 같은 role의 메시지는 하나의 content로 합친다.
- 응답의 `functionCall`에는 id가 없으므로 `ToolCall.id = f"gemini-{uuid4().hex[:12]}"`로 만든다.
- 텍스트 파트는 이어 붙여 `text`로, 없으면 None.
- `finishReason`이 `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`이거나 `promptFeedback.blockReason`이 있으면 `LLMResponse(text=None, tool_calls=[])`.
- 오류 매핑은 Task 2와 같다.

- [ ] **Step 2: 실패하는 테스트 작성**

```python
async def test_request_uses_header_key_not_query(respx_mock): ...   # 요청 URL에 "key=" 없음, 헤더 x-goog-api-key 있음
async def test_system_and_roles_mapped(respx_mock): ...
async def test_tool_result_mapped_to_function_name_by_call_id(respx_mock): ...
    # OpenAI 형식 id("call_abc")를 가진 assistant tool_calls + tool 메시지 → functionResponse.name == 해당 함수 이름
async def test_function_call_parsed_with_generated_id(respx_mock): ...
async def test_json_schema_sent_as_response_json_schema(respx_mock): ...
async def test_safety_block_returns_empty(respx_mock): ...
async def test_retryable_and_request_errors(respx_mock): ...
async def test_errors_do_not_contain_api_key(respx_mock): ...
```

- [ ] **Step 3: 실패 확인** — Run: `uv run pytest tests/llm/test_gemini.py -v` / Expected: FAIL
- [ ] **Step 4: 구현**
- [ ] **Step 5: 통과 확인** — Expected: PASS
- [ ] **Step 6: 커밋** — `git commit -m "feat: Gemini API 클라이언트 (헤더 키, 함수 호출, 구조화 출력)"`

---

### Task 4: 백업 전환과 앱 연결

**Files:**
- Create: `src/dodeed/llm/fallback.py`
- Modify: `src/dodeed/app.py` (`_make_llm`)
- Test: `tests/llm/test_fallback.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `OpenAIChatClient` (Task 2), `GeminiClient` (Task 3), `LLMProviderError`
- Produces:
  - `class FallbackLLM`: `__init__(self, primary: LLMClient, backup: LLMClient, primary_name: str, backup_name: str)`
  - `async def complete(...) -> LLMResponse`: primary가 `LLMProviderError`를 내면 같은 인자로 backup을 호출한다. backup의 예외는 그대로 올린다. `LLMRequestError`와 그 밖의 예외는 전환하지 않고 그대로 올린다.
  - 전환할 때 `logger.warning("llm fallback %s→%s (%s)", primary_name, backup_name, exc.kind)`만 남긴다.
  - `app._make_llm(settings, http)`: `llm == "live"`이면 `FallbackLLM(OpenAIChatClient(...), GeminiClient(...), "openai", "gemini")`. 오케스트레이터와 리더가 같은 인스턴스를 쓴다. 기존처럼 바깥에서 `GuardedLLM`으로 감싼다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
async def test_primary_success_backup_not_called(): ...
@pytest.mark.parametrize("kind", ["timeout", "connection", "rate_limited", "server_error", "bad_output"])
async def test_provider_error_falls_back(kind): ...
async def test_request_error_does_not_fall_back(): ...
async def test_backup_error_propagates(): ...
async def test_invalid_primary_output_falls_back(respx_mock): ...   # 실제 OpenAIChatClient + GeminiClient, OpenAI가 깨진 arguments
async def test_egress_checked_once_before_any_provider(respx_mock): ...
    # GuardedLLM(FallbackLLM(...))에 비밀값이 든 메시지 → EgressBlocked, 두 공급자 URL 호출 0회
async def test_both_fail_user_gets_retry_message(...): ...
    # live 설정으로 조립한 앱의 /api/chat: 두 공급자 모두 503 → 200 응답에 "잠시 문제가 생겼어요" 문구, 감사 로그 decision="error"

# tests/test_app.py
def test_make_llm_live_builds_fallback(): ...
def test_make_llm_fake_is_static(): ...
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/llm/test_fallback.py tests/test_app.py -v` / Expected: FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` / Expected: 전체 PASS, 경고 없음
- [ ] **Step 5: 커밋** — `git commit -m "feat: gpt-6-luna 1차, gemini 백업 전환과 앱 연결"`

---

### Task 5: 실제 키 점검 테스트와 한국어 품질 비교 스크립트

**Files:**
- Create: `tests/llm/test_live.py`, `scripts/llm_ko_eval.py`, `scripts/llm_ko_eval_cases.json`
- Modify: `pyproject.toml` (pytest 마커 `live` 등록, 기본 실행에서 제외), `README.md`

**Interfaces:**
- Consumes: `OpenAIChatClient`, `GeminiClient`, `SEARCH_RESTAURANTS`(`dodeed.restaurant.tools`), `SearchRequest`, `ToolRegistry.tool_defs`
- Produces:
  - 마커 `live`: `addopts`에 `-m "not live"`. 실행: `uv run pytest -m live`
  - `tests/llm/test_live.py`: 환경변수 `DODEED_OPENAI_API_KEY`, `DODEED_GEMINI_API_KEY`가 없으면 skip. 공급자별로 (1) 짧은 한국어 대화 1회, (2) `search_restaurants` 도구 호출 1회, (3) 구조화 출력 1회를 실제로 호출해 응답 형식을 검증한다.
  - `scripts/llm_ko_eval.py`: `uv run python scripts/llm_ko_eval.py --out <경로>`
    - `llm_ko_eval_cases.json`의 사례 25개를 두 모델에 각각 보낸다. 사례 형식: `{"utterance": "...", "expect": {"tool": "search_restaurants" | null, "args": {부분 일치할 인자}}}`
    - 사례 구성 [이 계획의 결정]: 상황 3종(혼밥·데이트·가성비) 각 5개, 지역·음식 종류·시간·인원 조합 5개, "지금 여기서"처럼 위치가 필요한 요청 3개, 도구를 부르면 안 되는 잡담·인사 2개
    - 보고서: 모델별 정확도(도구 선택 일치, 인자 부분 일치), 평균 지연 시간, 입출력 토큰 합계, 추정 비용(설정의 단가 상수: luna $0.10/$0.50, flash-lite $0.30/$2.50 per 1M)을 마크다운 표로 쓴다.
    - 보고서와 콘솔 출력에 API 키를 쓰지 않는다. 요청은 `GuardedLLM`을 거친다.

- [ ] **Step 1:** 마커 등록 후 `uv run pytest -q`가 live 테스트를 건너뛰는지 확인 / Expected: 전체 PASS, live 테스트는 수집 제외
- [ ] **Step 2:** live 테스트와 스크립트 작성. 스크립트는 사례 파일 형식 검증 함수 `load_cases(path) -> list[Case]`를 두고, 그 함수만 일반 테스트 `tests/test_eval_cases.py::test_cases_file_valid`로 검증한다 (25개, 각 항목 형식).
- [ ] **Step 3: 통과 확인** — Run: `uv run pytest -q && uv run ruff check .` / Expected: PASS
- [ ] **Step 4:** README에 실제 키로 점검하는 방법(`uv run pytest -m live`)과 품질 비교 실행 방법을 적는다.
- [ ] **Step 5: 커밋** — `git commit -m "test: LLM 실제 키 점검과 한국어 품질 비교 스크립트"`

---

## 키가 생긴 뒤 할 일 (이 계획의 범위 밖)

1. `.env`에 두 키를 넣고 `DODEED_LLM=live`로 바꾼다.
2. `uv run pytest -m live`로 두 공급자 연결을 확인한다.
3. `uv run python scripts/llm_ko_eval.py --out docs/superpowers/eval/2026-11-ko-llm.md`로 비교하고, 결과에 따라 1차/백업을 다시 정한다 (스펙 §10 미결 사항).
