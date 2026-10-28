"""RHU server entry point.

Run with:  uvicorn rhu.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from rhu import config, db
from rhu.api import router
from rhu.ingest import IngestPipeline


def create_app(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or config.DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(db_path)
        app.state.pipeline = IngestPipeline(db_path)
        app.state.pipeline.start()
        yield
        app.state.pipeline.stop()  # finishes queued batches before shutting down

    app = FastAPI(title="HealthPulz RHU Server", version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path
    app.include_router(router)

    # Temporary: point the root URL to the API docs until the web pages exist.
    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    # The protocol defines malformed messages as 400 (FastAPI's default is 422).
    @app.exception_handler(RequestValidationError)
    async def malformed_message(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content=jsonable_encoder({"detail": exc.errors()}))

    return app


app = create_app()
