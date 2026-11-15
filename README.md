# dodeed
Just say it, we Do the Deed.

## 소개

말로 부탁하면 대신 처리해 주는 비서입니다. 지금은 텔레그램과 웹 채팅으로 음식점을 찾아 주는 기능(계획 1)까지 들어 있습니다.

## 요구 사항

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker (docker compose로 PostgreSQL 16과 Redis 7을 띄웁니다)
- Chromium (Playwright로 설치합니다. 공개 웹페이지를 읽을 때 씁니다)

## 환경 변수

모든 설정은 `DODEED_` 접두사가 붙은 환경변수나 저장소 루트의 `.env` 파일에서 읽습니다. `.env`는 `.gitignore`에 들어 있으니 실제 키는 여기에만 적으세요.

```bash
cp .env.example .env
```

| 변수 | 설명 |
| --- | --- |
| `DODEED_DATABASE_URL` | PostgreSQL 주소. docker compose 기본값은 `postgresql+asyncpg://dodeed:dodeed@localhost:5432/dodeed` |
| `DODEED_REDIS_URL` | Redis 주소. 기본값은 `redis://localhost:6379/0` |
| `DODEED_WEB_BASE_URL` | 웹 주소. 가입 안내 링크에 쓰고, `https://`로 시작하면 보안 쿠키를 씁니다. 로컬은 `http://localhost:8000` |
| `DODEED_TELEGRAM_BOT_TOKEN` | 텔레그램 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 봇을 만들면 받는 토큰 (8자 이상) |
| `DODEED_TELEGRAM_WEBHOOK_SECRET` | 웹훅 요청을 확인하는 비밀값. 임의 문자열을 쓰세요. 비워 둘 수 없고, 영문·숫자·`_`·`-`만 1–256자 |
| `DODEED_KAKAO_REST_API_KEY` | [카카오 개발자](https://developers.kakao.com) → 내 애플리케이션 → 앱 키의 **REST API 키** (8자 이상) |
| `DODEED_NAVER_CLIENT_ID` | [네이버 개발자센터](https://developers.naver.com)에서 애플리케이션을 등록하고 **검색** API를 고르면 받는 Client ID (8자 이상) |
| `DODEED_NAVER_CLIENT_SECRET` | 위 애플리케이션의 Client Secret (8자 이상) |
| `DODEED_LOCALDATA_API_KEY` | [공공데이터포털](https://www.data.go.kr)에서 "행정안전부 식품_일반음식점 조회서비스"(일반음식점 인허가)를 활용 신청하면 받는 일반 인증키. **Decoding** 키를 넣으세요 (8자 이상) |
| `DODEED_SECRET_HMAC_KEY` | 내부 HMAC 키(세션 쿠키 서명). 32자 이상의 임의 문자열이어야 합니다 |
| `DODEED_LLM` | LLM 공급자. `fake`(고정 응답, 기본값) 또는 `live`(1차 OpenAI `gpt-6-luna`, 백업 Gemini `gemini-3.5-flash-lite`). `live`에는 아래 두 키가 모두 필요합니다 |
| `DODEED_OPENAI_API_KEY` | [OpenAI 플랫폼](https://platform.openai.com)의 API 키 (8자 이상). `live`일 때 필수. 접두사 없는 `OPENAI_API_KEY`도 받습니다 |
| `DODEED_GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com)의 Gemini API 키 (8자 이상). **결제 등급** 키를 넣으세요. `live`일 때 필수. 접두사 없는 `GEMINI_API_KEY`도 받습니다 |
| `DODEED_OPENAI_MODEL` | 1차 모델 이름. 기본값 `gpt-6-luna` |
| `DODEED_GEMINI_MODEL` | 백업 모델 이름. 기본값 `gemini-3.5-flash-lite` |
| `DODEED_LLM_TIMEOUT_S` | LLM 요청 하나의 시간 제한(초). 기본값 `30`. 1차 모델이 이 시간을 넘기면 백업으로 전환합니다 |
| `DODEED_OPENAI_REASONING_EFFORT` | OpenAI `reasoning_effort` (`none`, `minimal`, `low`, `medium`, `high`). 비워 두면 요청에 넣지 않습니다. 도구 호출이 있는 요청은 gpt-6-luna 제약으로 추론을 끄고(`reasoning_effort=none`), 리더(도구 없음)는 설정값을 따릅니다 |

LLM 키는 `DODEED_OPENAI_API_KEY`와 `OPENAI_API_KEY`(Gemini는 `DODEED_GEMINI_API_KEY`와 `GEMINI_API_KEY`)를 모두 받고, 둘 다 있으면 `DODEED_*`가 이깁니다. 셸에 `OPENAI_API_KEY`나 `GEMINI_API_KEY`가 이미 있으면 `DODEED_*`를 넣지 않아도 `live`에서 그 키를 씁니다(다른 프로젝트용 키로 요금이 나갈 수 있으니 확인하세요). 셸 환경변수는 `.env`보다 우선합니다. 키 앞뒤 공백은 지우고, 빈 값은 없는 것으로 봅니다.

`.env`를 [dotenvx](https://dotenvx.com)로 암호화했다면 값이 `encrypted:`로 시작합니다. 이 상태로 그냥 실행하면 키 값을 보여 주지 않고 "dotenvx로 암호화된 값입니다" 오류로 멈추니, `dotenvx run -- <명령>`으로 실행하세요.

비밀값이 비어 있거나 위 조건에 맞지 않으면 서버가 시작되지 않습니다. 빈 웹훅 비밀값이나 짧은 HMAC 키는 누구나 다른 사용자로 위장할 수 있게 만들기 때문입니다.

`DODEED_SECRET_HMAC_KEY`는 이렇게 만들 수 있습니다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 실행 순서

```bash
docker compose up -d
uv sync
uv run playwright install chromium
uv run alembic upgrade head
uv run uvicorn dodeed.app:create_app --factory
```

서버가 뜨면 `curl http://localhost:8000/health`가 `{"status":"ok"}`를 돌려줍니다.

`DODEED_LLM=fake`(기본값)일 때는 고정 응답 LLM(`StaticLLM`)이 답합니다.

## 텔레그램 웹훅 등록

텔레그램은 HTTPS 주소로만 웹훅을 보냅니다. 로컬에서는 [ngrok](https://ngrok.com) 같은 터널을 씁니다.

```bash
ngrok http 8000
```

ngrok이 알려 주는 `https://...` 주소로 웹훅을 등록합니다. `secret_token`에는 `DODEED_TELEGRAM_WEBHOOK_SECRET`과 같은 값을 넣어야 합니다. 텔레그램이 이 값을 `X-Telegram-Bot-Api-Secret-Token` 헤더로 보내고, 서버는 값이 다르면 요청을 거절합니다.

```bash
curl -X POST "https://api.telegram.org/bot<봇_토큰>/setWebhook" \
  -d "url=https://<ngrok_주소>/telegram/webhook" \
  -d "secret_token=<웹훅_비밀값>"
```

등록 상태는 `https://api.telegram.org/bot<봇_토큰>/getWebhookInfo`로 확인합니다.

## 테스트

```bash
docker compose up -d
uv run pytest
```

테스트는 `dodeed_test` 데이터베이스를 씁니다. 이 데이터베이스는 `docker/postgres-init.sql`이 만드는데, 이 스크립트는 **볼륨을 처음 만들 때만** 실행됩니다. 이미 있던 볼륨이라 `dodeed_test`가 없다면 직접 만드세요.

```bash
docker compose exec postgres psql -U dodeed -c "CREATE DATABASE dodeed_test;"
```

테스트는 외부 API를 부르지 않으므로 실제 키가 없어도 됩니다.

### 실제 키로 LLM 점검

실제 OpenAI·Gemini API를 부르는 테스트에는 `live` 표시가 붙어 있고 기본 실행(`uv run pytest`)에서는 빠집니다. `.env`나 환경변수에 `DODEED_OPENAI_API_KEY`, `DODEED_GEMINI_API_KEY`(또는 `OPENAI_API_KEY`, `GEMINI_API_KEY`)를 넣고 따로 돌립니다. 키가 없는 공급자의 테스트는 건너뜁니다. 공급자마다 짧은 한국어 대화, `search_restaurants` 도구 호출, 구조화 출력(JSON)을 한 번씩 실제로 호출하므로 소액의 요금이 듭니다.

```bash
uv run pytest -m live
# .env를 dotenvx로 암호화했다면
dotenvx run -- uv run pytest -m live
```

기본 테스트는 셸의 LLM 키 환경변수를 지우고 돌기 때문에 실제 키가 섞이지 않습니다.

### 한국어 품질 비교

`scripts/llm_ko_eval_cases.json`의 한국어 사례 25개를 1차(`gpt-6-luna`)와 백업(`gemini-3.5-flash-lite`) 모델에 같은 시스템 프롬프트와 도구로 보내고, 모델별 도구 선택 정확도, 인자 일치율, 평균 지연 시간, 입출력 토큰 합계, 추정 비용을 마크다운 표로 씁니다. 키는 위와 같이 `.env`나 환경변수에서 읽고(접두사 없는 `OPENAI_API_KEY`, `GEMINI_API_KEY`도 받으며 `DODEED_*`가 이깁니다. 모델 이름은 `DODEED_OPENAI_MODEL`, `DODEED_GEMINI_MODEL`로 바꿀 수 있습니다), 키가 있는 모델만 돌립니다. 보고서와 콘솔에는 키를 쓰지 않습니다. 단가는 스크립트의 `PRICES_PER_1M` 상수입니다.

```bash
uv run python scripts/llm_ko_eval.py --out docs/superpowers/eval/2026-11-ko-llm.md
# .env를 dotenvx로 암호화했다면
dotenvx run -- uv run python scripts/llm_ko_eval.py --out <경로>
```

## 현재 한계

- `DODEED_LLM=fake`일 때는 모든 답이 `StaticLLM`의 고정 응답입니다. `DODEED_LLM=live`는 OpenAI·Gemini 키(`DODEED_*` 또는 접두사 없는 이름)가 모두 있어야 시작하며, 1차 `gpt-6-luna`가 시간 초과·429·5xx·연결 오류·응답 형식 오류를 내면 백업 `gemini-3.5-flash-lite`로 한 번 전환합니다. 둘 다 실패하면 "잠시 후 다시 시도해 주세요" 안내로 끝납니다. 기본 테스트는 respx로만 검증하며, 실제 키로 두 공급자를 호출하는 확인은 `uv run pytest -m live`로 따로 돌립니다(아직 실제 키로 돌려 보지 않았습니다).
- LOCALDATA(일반음식점 인허가) 응답의 필드명은 문서에 없어 추정으로 파싱합니다. 실제 키로 응답 형식을 확인해야 하며, 형식이 다르면 인허가상 영업·폐업 상태를 "알 수 없음"으로 처리합니다.
- 예약과 결제는 계획 3에서 다룹니다.
