# CampusCaption

Live captions for campus lectures or events. 

## Layout

- `main.py` - run locally (`python main.py`)
- `app/` - API, services, templates
- `frontend/static/` - CSS and JS
- `Dockerfile`, `docker-compose.yml`

## Docker

```bash
docker compose up --build
```
Just a Sample setup done for now! 
- App: http://127.0.0.1:8080/
- Overlay: http://127.0.0.1:8080/overlay

Stop with `Ctrl+C` or `docker compose down`.

## Local Python

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

