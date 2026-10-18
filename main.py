 
import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware


app = FastAPI()

# Configure these environment variables before deployment.
EMAIL = os.getenv("LOGIN_EMAIL", "23f3003865@ds.study.iitm.ac.in")

ALLOWED_ORIGINS = [
    "https://app-yrrxdi.example.com",
]

# Add the actual exam page origin here through EXAM_ORIGIN.
exam_origin = os.getenv("EXAM_ORIGIN")
if exam_origin and exam_origin not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(exam_origin)


# -----------------------------------------
# Middleware 1: Request context propagation
# -----------------------------------------
class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode(
            "latin-1"
        )

        if not request_id:
            request_id = str(uuid.uuid4())

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                response_headers = list(
                    message.get("headers", [])
                )
                response_headers.append(
                    (
                        b"x-request-id",
                        request_id.encode("latin-1"),
                    )
                )
                message["headers"] = response_headers

            await send(message)

        await self.app(scope, receive, send_with_request_id)


# -----------------------------------------
# Middleware 2: Per-client rate limiting
# -----------------------------------------
class RateLimitMiddleware:
    def __init__(self, app, limit=9, window=10):
        self.app = app
        self.limit = limit
        self.window = window
        self.requests = defaultdict(deque)
        self.lock = Lock()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Apply the limit to /ping requests.
        if scope["path"] != "/ping":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        client_id = headers.get(
            b"x-client-id", b"unknown"
        ).decode("latin-1")

        now = time.monotonic()

        with self.lock:
            bucket = self.requests[client_id]

            # Remove requests outside the 10-second window.
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()

            if len(bucket) >= self.limit:
                limited = True
            else:
                bucket.append(now)
                limited = False

        if limited:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


 
# Register inner → outer. CORS goes LAST so it is outermost and
# adds headers to every response, including 429s and preflights.
app.add_middleware(RateLimitMiddleware, limit=9, window=10)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# Middleware registration order matters:
# The last added middleware wraps the earlier ones.
app.add_middleware(RateLimitMiddleware, limit=9, window=10)
app.add_middleware(RequestContextMiddleware)


# -----------------------------------------
# Endpoint
# -----------------------------------------
@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.scope["state"]["request_id"],
    }


@app.options("/ping")
async def ping_options():
    # Normally handled by CORSMiddleware for browser
    # preflight requests from permitted origins.
    return Response(status_code=204)


@app.get("/")
async def root():
    return {"status": "ok"} 
