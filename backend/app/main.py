import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.rate_limit import limiter
from app.routes import (
    accounts,
    ad_spend,
    alerts,
    auth,
    connectors,
    creative_performance,
    dashboard,
    metrics,
    orders,
    pixel_events,
    products,
    push,
    reports,
    stores,
)

settings.validate_production_ready()
configure_logging(settings.log_level)
logger = logging.getLogger("escal.request")

app = FastAPI(title="Escal", version="0.1.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": request.client.host if request.client else None,
        },
    )
    return response


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(stores.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(pixel_events.router)
app.include_router(ad_spend.router)
app.include_router(creative_performance.router)
app.include_router(metrics.router)
app.include_router(connectors.router)
app.include_router(connectors.health_router)
app.include_router(dashboard.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(push.router)


@app.get("/health")
def health():
    return {"status": "ok"}
