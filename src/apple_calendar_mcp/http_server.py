"""Remote (HTTP) entry point for the Apple Calendar MCP connector.

Serves the same tools as the stdio server over **Streamable HTTP**, the transport
Claude's custom/remote connectors use. Deploy this behind a public HTTPS URL and
add that URL in Claude → Settings → Connectors → Add custom connector.

Security model — Claude's connector dialog only accepts a URL (plus optional
OAuth). So we authenticate with a **secret in the URL path**: set MCP_URL_SECRET
to a long random string and the connector URL becomes

    https://<host>/<MCP_URL_SECRET>/mcp

Anyone who lacks the secret gets a 404. Treat the full URL like a password.
(Optionally also set MCP_AUTH_TOKEN to require a Bearer header for defense in
depth — but Claude's UI can't send one, so the URL secret is the real lock.)
"""

from __future__ import annotations

import os

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .server import mcp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Optional second factor: require Authorization: Bearer <MCP_AUTH_TOKEN>."""

    async def dispatch(self, request: Request, call_next):
        token = os.environ.get("MCP_AUTH_TOKEN")
        if token and request.url.path != "/health":
            if request.headers.get("authorization", "") != f"Bearer {token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "apple-calendar-mcp"})


def build_app() -> Starlette:
    mcp_app = mcp.streamable_http_app()  # serves the MCP endpoint at "/mcp"
    secret = os.environ.get("MCP_URL_SECRET", "").strip("/")

    if secret:
        # MCP lives at /<secret>/mcp ; unknown paths fall through to 404.
        mount_path = f"/{secret}"
    else:
        mount_path = ""

    routes = [Route("/health", health)]
    if mount_path:
        routes.append(Mount(mount_path, app=mcp_app))
    else:
        routes.append(Mount("/", app=mcp_app))

    app = Starlette(
        routes=routes,
        # Forward FastMCP's lifespan so the session manager starts.
        lifespan=lambda _app: mcp_app.router.lifespan_context(mcp_app),
    )
    app.add_middleware(BearerAuthMiddleware)
    return app


app = build_app()


def main() -> None:
    """Run with uvicorn. Honors PORT (cloud hosts inject this) and HOST."""
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
