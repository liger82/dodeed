import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dodeed.sources.public_page import (
    ALLOWED_PUBLIC_DOMAINS,
    SUBRESOURCE_ALLOWED_SUFFIXES,
    DomainNotAllowed,
    PublicPageReader,
)

PAGES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/ok.html":
            body = (PAGES / "ok.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/sub.html"):
            # 다른 호스트(localhost:B)의 이미지와 스크립트를 부른다
            other = self.path.split("other=")[1]
            body = (
                f'<html><body><p>본문</p><img src="http://localhost:{other}/pixel.png">'
                f'<script src="http://localhost:{other}/app.js"></script></body></html>'
            ).encode()
            self._html(body)
        elif self.path == "/spa.html":
            # SPA처럼 본문을 나중에 그린다
            body = (
                b"<html><body><div id=app></div><script>"
                b"fetch('/late.json').then(r => r.json()).then(d => {"
                b"document.getElementById('app').innerText = d.text; });"
                b"</script></body></html>"
            )
            self._html(body)
        elif self.path == "/late.json":
            time.sleep(0.3)
            body = '{"text": "늦게 그린 가게 정보"}'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/ok.html")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def _html(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)


class CountingHandler(BaseHTTPRequestHandler):
    """다른 호스트 역할: 들어온 요청 경로를 센다."""

    hits: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        CountingHandler.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"")


@pytest.fixture(scope="module")
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
async def reader():
    r = PublicPageReader(allowed_domains=frozenset({"127.0.0.1"}))
    yield r
    await r.aclose()


def test_default_allowlist():
    assert "blog.naver.com" in ALLOWED_PUBLIC_DOMAINS and len(ALLOWED_PUBLIC_DOMAINS) == 4


def test_blog_url_converted_to_mobile():
    assert (
        PublicPageReader._to_mobile_blog("https://blog.naver.com/abc/223000000001")
        == "https://m.blog.naver.com/abc/223000000001"
    )


async def test_public_page_rejects_disallowed_domain():
    r = PublicPageReader()
    with pytest.raises(DomainNotAllowed):
        await r.text("https://evil.example.com/x")
    with pytest.raises(DomainNotAllowed):
        await r.text("https://sub.blog.naver.com/x")


async def test_public_page_reads_text(local_server, reader):
    text = await reader.text(f"http://127.0.0.1:{local_server}/ok.html")
    assert "공개 페이지 본문" in text


async def test_public_page_stops_on_redirect_outside_allowlist(local_server, reader):
    assert await reader.text(f"http://127.0.0.1:{local_server}/redir") is None


async def test_public_page_none_on_404_unreachable(reader):
    assert await reader.text("http://127.0.0.1:9/none", timeout_s=3) is None


@pytest.fixture(scope="module")
def other_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def hits():
    CountingHandler.hits.clear()
    return CountingHandler.hits


def test_default_subresource_suffixes():
    assert SUBRESOURCE_ALLOWED_SUFFIXES == (
        ".kakaocdn.net",
        ".daumcdn.net",
        ".kakao.com",
        ".pstatic.net",
        ".naver.net",
        ".naver.com",
    )


def test_subresource_suffix_matching():
    r = PublicPageReader()
    assert r._subresource_allowed("https://t1.kakaocdn.net/x.js")
    assert r._subresource_allowed("https://kakao.com/x.js")
    assert r._subresource_allowed("https://place.map.kakao.com/x.js")  # 탐색 허용 호스트
    assert not r._subresource_allowed("https://evilkakao.com/x.js")
    assert not r._subresource_allowed("https://www.google-analytics.com/x.js")


async def test_third_party_subresource_aborted(local_server, other_server, hits, reader):
    text = await reader.text(f"http://127.0.0.1:{local_server}/sub.html?other={other_server}")
    assert "본문" in text
    assert hits == []


async def test_allowlisted_subresource_loaded_but_navigation_still_blocked(
    local_server, other_server, hits
):
    r = PublicPageReader(
        allowed_domains=frozenset({"127.0.0.1"}), subresource_suffixes=("localhost",)
    )
    try:
        text = await r.text(f"http://127.0.0.1:{local_server}/sub.html?other={other_server}")
        assert "본문" in text
        assert sorted(hits) == ["/app.js", "/pixel.png"]  # 대조군: 허용하면 실제로 닿는다
        # 하위 리소스로 허용한 호스트라도 페이지 탐색(리다이렉트)은 막는다
        assert await r.text(f"http://127.0.0.1:{local_server}/redir") is None
    finally:
        await r.aclose()


async def test_waits_for_network_idle_before_reading(local_server, reader):
    text = await reader.text(f"http://127.0.0.1:{local_server}/spa.html")
    assert "늦게 그린 가게 정보" in text


async def test_failure_logged_with_type_name(reader, caplog):
    with caplog.at_level(logging.WARNING, logger="dodeed.sources.public_page"):
        assert await reader.text("http://127.0.0.1:9/none", timeout_s=3) is None
    assert any(r.levelno == logging.WARNING and "Error" in r.getMessage() for r in caplog.records)
