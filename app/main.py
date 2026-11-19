import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import captions, health
from app.core.config import get_settings
from app.stt.manager import SessionManager

BASE = Path(__file__).resolve().parent
FRONTEND = BASE.parent / "frontend"

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.captions = SessionManager(get_settings())
    app.state.captions.start()
    try:
        yield
    finally:
        await app.state.captions.close()


app = FastAPI(title="CampusCaption", version="0.4.0", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path != "/overlay":  # the overlay may be embedded (OBS / beamer browser source)
        response.headers.setdefault("X-Frame-Options", "DENY")
    if get_settings().is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(health.router, prefix="/api")
app.include_router(captions.router, prefix="/api")

app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _page(name: str):
    def handler(request: Request):
        return templates.TemplateResponse(request=request, name=name)

    return handler


for path, template in {
    "/": "index.html",
    "/overlay": "overlay.html",
}.items():
    app.add_api_route(path, _page(template), methods=["GET"], response_class=HTMLResponse, include_in_schema=False)
