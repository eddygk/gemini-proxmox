import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import secrets
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastmcp.utilities.lifespan import combine_lifespans
import jwt
from proxmoxer import ProxmoxAPI
from starlette.requests import Request

from app.config import settings
from app.mcp_server import mcp, init_ops
from app.security import MCPAuthMiddleware, verify_request_auth, _validate_bearer

# Helpers for dynamic public URL and user email
def get_base_url() -> str:
    return settings.public_base_url.rstrip("/")

def get_user_email() -> str:
    if settings.oauth_user_email:
        return settings.oauth_user_email
    host = urlparse(get_base_url()).hostname or "pve-mcp.local"
    return f"operator@{host}"

# Configure application logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini-proxmox")

# Module-level PVE client & ops (single shared instance)
pve: ProxmoxAPI = None
ops = None

# Authorization code store: code -> metadata (PKCE challenge, redirect_uri, expires_at)
auth_codes: dict[str, dict] = {}

# Initialize FastMCP HTTP ASGI sub-app with route "/mcp"
# stateless_http=True enables independent request handling required for Streamable HTTP
mcp_asgi_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    global pve, ops
    pve = ProxmoxAPI(
        settings.proxmox_host,
        port=settings.proxmox_port,
        user=settings.proxmox_user,
        token_name=settings.proxmox_token_name,
        token_value=settings.proxmox_token_value,
        verify_ssl=settings.proxmox_verify_ssl,
        timeout=30,  # Prevent timeout drops during vzdump or snapshot creation
    )
    # init_ops returns the shared ProxmoxOps instance — used by both MCP tools and REST routes
    ops = init_ops(pve)
    yield
    pve = None
    ops = None


# Official FastMCP best practice: combine app lifespan with MCP session manager lifespan
app = FastAPI(
    title="Gemini Proxmox VE Operator",
    description="MCP and REST Gateway for Proxmox VE cluster management and Gemini Spark",
    version="2.0.0",
    lifespan=combine_lifespans(app_lifespan, mcp_asgi_app.lifespan),
)

# Detailed request logging middleware for OAuth & MCP diagnostics
@app.middleware("http")
async def log_requests(request: Request, call_next):
    client_ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else "unknown")
    )
    user_agent = request.headers.get("user-agent", "-")
    path = request.url.path
    query = request.url.query
    full_url = f"{path}?{query}" if query else path

    response = await call_next(request)

    if any(k in path for k in ["oauth", ".well-known", "mcp"]):
        logger.info(
            f"HTTP {request.method} {full_url} -> {response.status_code} "
            f"[Client: {client_ip}] [UA: {user_agent}]"
        )
    return response

# CORS middleware for browser-based callers (Gemini web, personal intelligence)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
)

# Mount FastMCP server at capability secret prefix, wrapped in auth middleware
# (Starlette sub-apps bypass parent FastAPI dependencies — MCPAuthMiddleware gates access)
app.mount(f"/{settings.mcp_secret_path}", MCPAuthMiddleware(mcp_asgi_app))


# Existing Unauthenticated Health Probe
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "gemini-proxmox"}


# RFC 9728 OAuth 2.0 Protected Resource Metadata Discovery (Queried by Google Gemini / MCP Clients)
@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/{full_path:path}")
@app.get(f"/{settings.mcp_secret_path}/.well-known/oauth-protected-resource")
def oauth_protected_resource(full_path: str = None):
    base = get_base_url()
    return {
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["openid", "profile", "email"],
    }


# OpenID Connect Discovery 1.0 (Queried by Google Gemini OAuth Client)
@app.get("/.well-known/openid-configuration")
@app.get("/.well-known/openid-configuration/{full_path:path}")
@app.get(f"/{settings.mcp_secret_path}/.well-known/openid-configuration")
@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/{full_path:path}")
@app.get(f"/{settings.mcp_secret_path}/.well-known/oauth-authorization-server")
def openid_configuration(full_path: str = None):
    base = get_base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "userinfo_endpoint": f"{base}/oauth/userinfo",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "client_credentials",
        ],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["HS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/.well-known/jwks.json")
def jwks():
    return {"keys": []}


# OAuth 2.0 Authorization Endpoint (Auto-Approves User-Bound Custom Connected Apps)
@app.get("/oauth/authorize")
async def oauth_authorize(
    request: Request,
    response_type: str = Query("code"),
    client_id: str = Query(None),
    redirect_uri: str = Query(None),
    state: str = Query(None),
    code_challenge: str = Query(None),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(None),
):
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="Missing redirect_uri")

    # Generate single-use authorization code
    code = secrets.token_urlsafe(32)
    auth_codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
    }

    params = {"code": code}
    if state:
        params["state"] = state

    sep = "&" if "?" in redirect_uri else "?"
    redirect_target = f"{redirect_uri}{sep}{urlencode(params)}"

    logger.info(
        f"OAuth authorize: issued code={code[:8]}... for client={client_id}, redirecting to={redirect_uri}"
    )

    return RedirectResponse(url=redirect_target, status_code=302)


# RFC 6749 OAuth 2.0 Token Endpoint (Supports Authorization Code, Refresh Token, & Client Credentials)
@app.post("/oauth/token")
@app.post(f"/{settings.mcp_secret_path}/oauth/token")
async def oauth_token(request: Request):
    content_type = request.headers.get("content-type", "")
    params: dict = {}

    # Read raw body once to avoid stream consumption conflicts between Form and JSON
    body_bytes = await request.body()
    if body_bytes:
        if "application/json" in content_type:
            try:
                params = json.loads(body_bytes.decode("utf-8"))
            except Exception as e:
                logger.warning(f"Failed to parse JSON body in /oauth/token: {e}")
        else:
            try:
                from urllib.parse import parse_qs
                qs = parse_qs(body_bytes.decode("utf-8"))
                params = {k: v[0] for k, v in qs.items()}
            except Exception as e:
                logger.warning(f"Failed to parse form body in /oauth/token: {e}")

    # Fallback to query params if any parameters were passed in the URL
    for k, v in request.query_params.items():
        if k not in params:
            params[k] = v

    client_id = params.get("client_id")
    client_secret = params.get("client_secret")
    grant_type = params.get("grant_type", "client_credentials")
    code = params.get("code")
    redirect_uri = params.get("redirect_uri")
    code_verifier = params.get("code_verifier")
    refresh_token = params.get("refresh_token")

    # Support client_secret_basic (Basic base64(id:secret))
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Basic "):
        try:
            cred = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
            if ":" in cred:
                b_id, b_sec = cred.split(":", 1)
                client_id = client_id or b_id
                client_secret = client_secret or b_sec
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Basic authorization header")

    logger.info(
        f"OAuth token request: grant_type={grant_type}, client_id={client_id}, "
        f"has_code={bool(code)}, has_verifier={bool(code_verifier)}, has_refresh={bool(refresh_token)}"
    )

    # Validate client credentials if client_id provided
    if client_id and client_id != settings.oauth_client_id:
        raise HTTPException(status_code=401, detail="Invalid client_id")

    # Handle Client Credentials Grant
    if grant_type == "client_credentials":
        if client_secret != settings.oauth_client_secret:
            raise HTTPException(status_code=401, detail="Invalid client_secret")

    # Handle Authorization Code Grant
    elif grant_type == "authorization_code":
        if client_secret and client_secret != settings.oauth_client_secret:
            raise HTTPException(status_code=401, detail="Invalid client_secret")

        if not code or code not in auth_codes:
            logger.warning(f"Invalid or expired authorization code requested: {code}")
            raise HTTPException(status_code=400, detail="Invalid or expired authorization code")

        code_data = auth_codes.pop(code)
        if datetime.now(timezone.utc) > code_data["expires_at"]:
            raise HTTPException(status_code=400, detail="Authorization code expired")

        # Verify PKCE if challenge was registered
        challenge = code_data.get("code_challenge")
        if challenge and code_verifier:
            method = code_data.get("code_challenge_method", "S256")
            if method == "S256":
                digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
                computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                if computed != challenge:
                    logger.warning("PKCE challenge verification failed")
                    raise HTTPException(status_code=400, detail="Invalid PKCE code_verifier")

    # Handle Refresh Token Grant
    elif grant_type == "refresh_token":
        if client_secret and client_secret != settings.oauth_client_secret:
            raise HTTPException(status_code=401, detail="Invalid client_secret")

        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh_token")

        try:
            r_payload = jwt.decode(
                refresh_token,
                settings.gateway_secret,
                algorithms=["HS256"],
                issuer=get_base_url(),
            )
            if r_payload.get("type") != "refresh":
                raise HTTPException(status_code=400, detail="Invalid token type for refresh")
        except jwt.PyJWTError as e:
            logger.warning(f"Refresh token validation failed: {e}")
            raise HTTPException(status_code=401, detail="Invalid or expired refresh_token")

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported grant_type: {grant_type}")

    now = datetime.now(timezone.utc)
    base = get_base_url()
    email = get_user_email()

    access_payload = {
        "sub": client_id or settings.oauth_client_id,
        "iss": base,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=24)).timestamp()),
    }
    access_token = jwt.encode(access_payload, settings.gateway_secret, algorithm="HS256")

    refresh_payload = {
        "sub": client_id or settings.oauth_client_id,
        "iss": base,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=90)).timestamp()),
    }
    issued_refresh_token = jwt.encode(refresh_payload, settings.gateway_secret, algorithm="HS256")

    id_payload = {
        "sub": client_id or settings.oauth_client_id,
        "iss": base,
        "aud": client_id or settings.oauth_client_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=24)).timestamp()),
        "name": "Gemini Proxmox Operator",
        "email": email,
    }
    id_token = jwt.encode(id_payload, settings.oauth_client_secret, algorithm="HS256")

    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400,
        "refresh_token": issued_refresh_token,
        "scope": "openid profile email",
        "id_token": id_token,
    }


# UserInfo Endpoint (OIDC profile info)
@app.get("/oauth/userinfo")
@app.post("/oauth/userinfo")
def oauth_userinfo(authorization: str = Header(None)):
    if not _validate_bearer(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "sub": settings.oauth_client_id,
        "name": "Gemini Proxmox Operator",
        "email": get_user_email(),
    }


# Retain REST Endpoints for Atomic (CT 670), Claude Code, and Local Scripts
@app.get("/telemetry/cluster", dependencies=[Depends(verify_request_auth)])
def rest_cluster_status():
    return ops.get_cluster_status()


@app.get("/telemetry/guests", dependencies=[Depends(verify_request_auth)])
def rest_guest_inventory():
    return ops.get_guest_inventory()


@app.get("/telemetry/storage", dependencies=[Depends(verify_request_auth)])
def rest_storage_health():
    return ops.get_storage_health()


@app.get("/telemetry/backups", dependencies=[Depends(verify_request_auth)])
def rest_backup_tasks(limit: int = Query(default=10, ge=1, le=50)):
    return ops.get_backup_tasks(limit=limit)
