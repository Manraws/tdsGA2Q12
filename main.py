import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware


app = FastAPI()

EMAIL = os.getenv("LOGIN_EMAIL", "23f3003865@ds.study.iitm.ac.in")


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
        request_id = headers.get(b"x-request-id", b"").decode("latin-1")

        if not request_id:
            request_id = str(uuid.uuid4())

        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                # Drop any existing x-request-id, then set exactly one.
                response_headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                response_headers.append(
                    (b"x-request-id", request_id.encode("latin-1"))
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
        # Only limit real GET /ping requests (not preflights).
        if (
            scope["type"] != "http"
            or scope["path"] != "/ping"
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        client_id = headers.get(b"x-client-id", b"unknown").decode("latin-1")

        now = time.monotonic()
        with self.lock:
            bucket = self.requests[client_id]
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()
            limited = len(bucket) >= self.limit
            if not limited:
                bucket.append(now)

        if limited:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


# Last added = outermost. Final stack:
# CORS -> RequestContext -> RateLimit -> app
app.add_middleware(RateLimitMiddleware, limit=9, window=10)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],   # lets the browser read the echoed header
)


@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.scope["state"]["request_id"],
    }


@app.get("/")
async def root():
    return {"status": "ok"}
