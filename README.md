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
| `DODEED_TELEGRAM_BOT_TOKEN` | 텔레그램 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 봇을 만들면 받는 토큰 |
| `DODEED_TELEGRAM_WEBHOOK_SECRET` | 웹훅 요청을 확인하는 비밀값. 아무 임의 문자열이면 됩니다 (영문·숫자·`_`·`-`, 1–256자) |
| `DODEED_KAKAO_REST_API_KEY` | [카카오 개발자](https://developers.kakao.com) → 내 애플리케이션 → 앱 키의 **REST API 키** |
| `DODEED_NAVER_CLIENT_ID` | [네이버 개발자센터](https://developers.naver.com)에서 애플리케이션을 등록하고 **검색** API를 고르면 받는 Client ID |
| `DODEED_NAVER_CLIENT_SECRET` | 위 애플리케이션의 Client Secret |
| `DODEED_LOCALDATA_API_KEY` | [공공데이터포털](https://www.data.go.kr)에서 "행정안전부 식품_일반음식점 조회서비스"(일반음식점 인허가)를 활용 신청하면 받는 일반 인증키. **Decoding** 키를 넣으세요 |
| `DODEED_SECRET_HMAC_KEY` | 내부 HMAC 키. 긴 임의 문자열을 쓰세요 |
| `DODEED_LLM` | LLM 공급자. 지금은 `fake`만 됩니다 (기본값) |

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

실제 LLM 공급자가 정해지기 전까지는 `DODEED_LLM=fake`로만 실행됩니다. 이때는 고정 응답 LLM(`StaticLLM`)이 답합니다.

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

## 현재 한계

- LLM 공급자가 아직 연결되지 않았습니다. 모든 답은 `StaticLLM`의 고정 응답입니다.
- LOCALDATA(일반음식점 인허가) 응답의 필드명은 문서에 없어 추정으로 파싱합니다. 실제 키로 응답 형식을 확인해야 하며, 형식이 다르면 인허가상 영업·폐업 상태를 "알 수 없음"으로 처리합니다.
- 예약과 결제는 계획 3에서 다룹니다.
