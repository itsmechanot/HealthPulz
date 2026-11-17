"""RHU server entry point. Run with: python -m rhu"""

import asyncio
import mimetypes
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rhu import accounts, api, auth, bhu_api, config, db, ws
from rhu.alerts import AlertService, reword_old_messages
from rhu.analytics import Analytics, run_when_cases_change
from rhu.bhu_store import BhuStores, OutboxRelay
from rhu.hub import Hub
from rhu.ingest import IngestPipeline

# Windows can map .js to text/plain, and browsers refuse module scripts served that way.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


def quiet_closed_connections(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Browsers cut video downloads short all the time; Windows then logs a harmless traceback."""
    if isinstance(context.get("exception"), ConnectionResetError):
        return
    loop.default_exception_handler(context)


def create_app(
    db_path: Path | None = None,
    *,
    analytics_workers: int = config.ANALYTICS_WORKERS,
    analytics_interval: float = config.ANALYTICS_INTERVAL_SECONDS,
    analytics_on_reports: bool = True,
) -> FastAPI:
    db_path = db_path or config.DB_PATH

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(db_path)
        accounts.ensure_accounts(db_path)
        reword_old_messages(db_path)
        hub = Hub()  #switch board
        hub.bind_loop(asyncio.get_running_loop())
        stores = BhuStores(db_path.parent / "bhu")

        def on_stored(node_id: str, message: dict) -> None:
            relay.on_stored(message)  # first, so a device told STORED reads the updated record
            hub.send_to_node_threadsafe(node_id, message)

        # Set from the DB writer thread, so it has to go through the event loop.
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(quiet_closed_connections)
        new_reports = asyncio.Event()

        def announce_reports() -> None:
            loop.call_soon_threadsafe(new_reports.set)

        pipeline = IngestPipeline(db_path, on_stored=on_stored, on_reports=announce_reports if analytics_on_reports else None)
        relay = OutboxRelay(stores, pipeline)
        pipeline.start()
        relay.start()
        app.state.bhu_stores = stores
        app.state.outbox_relay = relay
        analytics = Analytics(db_path, workers=analytics_workers)
        alerts = AlertService(db_path, hub)
        app.state.hub = hub
        app.state.pipeline = pipeline
        app.state.analytics = analytics
        app.state.alerts = alerts

        async def after_analytics(result) -> None:
            # Tell the dashboards right away, before the alert check, so their numbers don't wait.
            await hub.broadcast_to_monitors({"type": "ANALYTICS_DONE", "finished_at": result.finished_at})
            await alerts.check_and_publish()

        background = [
            asyncio.create_task(ws.monitor_feed(app)),
            asyncio.create_task(run_when_cases_change(analytics, analytics_interval, after_run=after_analytics, new_reports=new_reports)),
        ]

        yield

        for task in background:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Finish queued batches before shutting down (in a thread, so STORED messages still go out).
        await asyncio.to_thread(relay.stop)
        await asyncio.to_thread(pipeline.stop)
        analytics.close()

    app = FastAPI(title="HealthPulz RHU Server", version="0.1.0", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.login_throttle = auth.LoginThrottle()
    app.include_router(auth.router)
    app.include_router(api.public_router)
    app.include_router(api.router)
    app.include_router(bhu_api.router)
    app.include_router(ws.router)

    # The protocol defines malformed messages as 400 (FastAPI's default is 422).
    @app.exception_handler(RequestValidationError)
    async def malformed_message(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content=jsonable_encoder({"detail": exc.errors()}))

    # Web pages (login, BHU, RHU). Mounted last so /api and /ws routes take priority.
    app.mount("/", auth.GuardedPages(directory=config.WEB_DIR, html=True), name="web")

    return app


app = create_app()
