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
from rhu.alerts import AlertService
from rhu.analytics import Analytics, run_when_cases_change
from rhu.hub import Hub
from rhu.ingest import IngestPipeline

# Python reads file types from the Windows registry, which can map .js to
# text/plain. Browsers refuse to run module scripts served that way, and won't
# render an icon sprite that isn't image/svg+xml.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def create_app(
    db_path: Path | None = None,
    *,
    analytics_workers: int = config.ANALYTICS_WORKERS,
    analytics_interval: float = config.ANALYTICS_INTERVAL_SECONDS,
) -> FastAPI:
    db_path = db_path or config.DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(db_path)
        hub = Hub()
        hub.bind_loop(asyncio.get_running_loop())
        pipeline = IngestPipeline(db_path, on_stored=hub.send_to_node_threadsafe)
        pipeline.start()
        analytics = Analytics(db_path, workers=analytics_workers)
        alerts = AlertService(db_path, hub)
        app.state.hub = hub
        app.state.pipeline = pipeline
        app.state.analytics = analytics
        app.state.alerts = alerts

        async def check_alerts(_result) -> None:
            await alerts.check_and_publish()

        background = [
            asyncio.create_task(ws.monitor_feed(app)),
            asyncio.create_task(run_when_cases_change(analytics, analytics_interval, after_run=check_alerts)),
        ]

        yield

        for task in background:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Finish queued batches before shutting down (in a thread, so the event
        # loop stays free to deliver the last STORED messages).
        await asyncio.to_thread(pipeline.stop)
        analytics.close()

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
