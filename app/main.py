"""FastAPI application entrypoint for the UMAI Engine."""

import logging

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.core.logging import configure_logging
from app.core.runtime_validation import validate_engine_runtime

logger = logging.getLogger("umai.engine")


def create_app() -> FastAPI:
    configure_logging()
    validate_engine_runtime()
    app = FastAPI(title="UMAI Enterprise Engine", version="1.0.0")
    app.include_router(api_router)
    logger.info("engine.startup")
    return app


app = create_app()
