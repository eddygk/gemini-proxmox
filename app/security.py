import jwt
from fastapi import Header, HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings


def _validate_bearer(auth_header: str | None) -> bool:
    """Validate a Bearer token — accepts either a signed JWT or the static gateway secret."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return False

    token = auth_header.removeprefix("Bearer ").strip()

    # 1. Static gateway secret (LAN clients: Atomic, Claude Code, curl)
    if token == settings.gateway_secret:
        return True

    # 2. JWT issued by /oauth/token (Gemini Connected Apps)
    try:
        payload = jwt.decode(
            token,
            settings.gateway_secret,
            algorithms=["HS256"],
            issuer=settings.public_base_url.rstrip("/"),
        )
        if payload.get("type") and payload.get("type") != "access":
            return False
        return True
    except jwt.InvalidTokenError:
        return False


def verify_request_auth(authorization: str = Header(None)):
    """FastAPI dependency for REST endpoints — rejects if neither JWT nor static secret."""
    if not _validate_bearer(authorization):
        base = settings.public_base_url.rstrip("/")
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization",
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
            },
        )


class MCPAuthMiddleware:
    """ASGI middleware wrapper for the mounted FastMCP sub-app.

    The FastMCP sub-app is mounted at the unguessable 128-bit capability secret path
    (/{settings.mcp_secret_path}/mcp). Possession of the secret path grants capability access.

    If an Authorization header is provided (e.g. from an OAuth client or script),
    it is strictly validated. If no Authorization header is provided, access is permitted
    under the secret capability URL, allowing seamless connection from Gemini Connected Apps
    without triggering failed OAuth account linking.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            # Allow OPTIONS preflight requests to pass through to CORS middleware
            if scope.get("method") == "OPTIONS":
                await self.app(scope, receive, send)
                return

            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode("utf-8")

            # If an Authorization header is explicitly sent, it must be valid
            if auth_header and not _validate_bearer(auth_header):
                response = JSONResponse(
                    status_code=401,
                    content={"error": "Invalid authorization token"},
                    headers={
                        "Access-Control-Allow-Origin": "*",
                    },
                )
                await response(scope, receive, send)
                return

            # If no Authorization header is sent, capability URL possession authenticates the caller
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)
