from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.server.url_paths import absolute_public_url, cli_public_link, external_path, request_mount_prefix


def _app_with_base(route_base_path: str = "/blink") -> Starlette:
    async def home(request: Request) -> PlainTextResponse:
        return PlainTextResponse(request_mount_prefix(request))

    app = Starlette(routes=[Route("/", home)])
    app.state.route_base_path = route_base_path
    return app


def test_request_mount_prefix_dedupes_proxy_and_config() -> None:
    app = _app_with_base("/blink")
    client = TestClient(app, root_path="/blink")
    r = client.get("/")
    assert r.text == "/blink"


def test_absolute_public_url_when_base_in_public_url() -> None:
    async def ping(request: Request) -> PlainTextResponse:
        url = absolute_public_url(request, "/auth/login", public_base_url="https://hey.cardano.org/blink")
        return PlainTextResponse(url)

    app = Starlette(routes=[Route("/", ping)])
    app.state.route_base_path = "/blink"
    client = TestClient(app, root_path="/blink")
    assert client.get("/").text == "https://hey.cardano.org/blink/auth/login"


def test_cli_public_link_adds_route_base_when_missing_from_public_url() -> None:
    link = cli_public_link("https://hey.cardano.org", "/blink", "/auth/set-password?token=abc")
    assert link == "https://hey.cardano.org/blink/auth/set-password?token=abc"
