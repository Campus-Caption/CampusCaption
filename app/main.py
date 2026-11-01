from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import captions, health

BASE = Path(__file__).resolve().parent
FRONTEND = BASE.parent / "frontend"

app = FastAPI(title="CampusCaption", version="0.1.0")
app.include_router(health.router, prefix="/api")
app.include_router(captions.router, prefix="/api")

app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.get("/", response_class=HTMLResponse)
def demo_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/overlay", response_class=HTMLResponse)
def overlay_page(request: Request):
    return templates.TemplateResponse(request=request, name="overlay.html")
