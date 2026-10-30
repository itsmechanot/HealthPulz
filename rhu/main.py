"""RHU server entry point.

Run with:  uvicorn rhu.main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import mimetypes
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from rhu import api, config, db, ws
from rhu.hub import Hub
from rhu.ingest import IngestPipeline

# Python reads file types from the Windows registry, which can map .js to
# text/plain. Browsers refuse to run module scripts served that way.
mimetypes.add_type("text/javascript", ".js")


def create_app(db_path: Path | None = None) -> FastAPI:
    db_path = db_path or config.DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(db_path)
        hub = Hub()
        hub.bind_loop(asyncio.get_running_loop())
        pipeline = IngestPipeline(db_path, on_stored=hub.send_to_node_threadsafe)
        pipeline.start()
        app.state.hub = hub
        app.state.pipeline = pipeline
        feed = asyncio.create_task(ws.monitor_feed(app))

        yield

        feed.cancel()
        with suppress(asyncio.CancelledError):
            await feed
        # Finish queued batches before shutting down (in a thread, so the event
        # loop stays free to deliver the last STORED messages).
        await asyncio.to_thread(pipeline.stop)

    app = FastAPI(title="HealthPulz RHU Server", version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path
    app.include_router(api.router)
    app.include_router(ws.router)

    # The protocol defines malformed messages as 400 (FastAPI's default is 422).
    @app.exception_handler(RequestValidationError)
    async def malformed_message(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content=jsonable_encoder({"detail": exc.errors()}))

    # Web pages (BHS Node, dashboards). Mounted last so /api and /ws routes take priority.
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")

    return app


app = create_app()
