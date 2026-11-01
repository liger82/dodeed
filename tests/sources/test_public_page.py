import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dodeed.sources.public_page import ALLOWED_PUBLIC_DOMAINS, DomainNotAllowed, PublicPageReader

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
        elif self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/ok.html")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


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
