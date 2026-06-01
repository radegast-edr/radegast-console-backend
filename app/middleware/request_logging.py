import logging
import sys

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import settings
from app.middleware.session import parse_session_cookie

logger = logging.getLogger("radegast.access")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z")
handler.setFormatter(formatter)
logger.addHandler(handler)


def get_request_session_label(request: Request) -> str:
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        return "[NA]"

    session = parse_session_cookie(cookie)
    if not session:
        return "[NA]"

    prefix = "U" if session.scope == "user" else "D"
    return f"[{prefix}{session.id}]"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else 0
        http_version = request.scope.get("http_version", "1.1")
        session_label = get_request_session_label(request)
        
        response = await call_next(request)
        
        logger.info(
            "%s:%s - %s %s HTTP/%s %s - %s",
            client_host,
            client_port,
            request.method,
            request.url.path,
            http_version,
            response.status_code,
            session_label,
        )
        return response
