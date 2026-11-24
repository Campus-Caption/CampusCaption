# CampusCaption

Live captions for campus lectures or events. Speak into a microphone, captions appear on a beamer or any screen.
Speech recognition runs only on local models: audio never leaves this computer.

| Model | Best for | Needs |
|---|---|---|
| NVIDIA Nemotron 3.5 ASR Streaming 0.6B | fastest and lightest | CPU is enough (~475 MB) |
| Parakeet v3 (default) | fast, 25 European languages | CPU is enough (~465 MB) |
| Whisper large-v3-turbo / small | 99 languages | GPU recommended for turbo |
| Mistral Voxtral Mini 4B Realtime | best accuracy | NVIDIA GPU (~16 GB), `pip install -r requirements-voxtral.txt`, ~9 GB download from Hugging Face |

**Lecturer password.** Set `LECTURER_SECRET` in `.env` whenever other people can reach the server. Then starting,
changing, clearing, exporting and ending captions (and streaming the microphone) need that password; the console asks
for it once per browser tab. Viewers (overlay, caption feed) never need it. If it is empty, anyone who can reach the
server can control captions, so only leave it empty on your own machine (127.0.0.1).

**No audio on disk.** Audio only lives in RAM. `tests/test_no_audio_on_disk.py` streams audio through the whole
pipeline and fails if any file is written or any audio shows up in the logs.

## Quick start (Windows)

1. Double-click `run.bat` (first run installs everything).
2. Optional: edit `.env` (created automatically), e.g. `LECTURER_SECRET=` or `STT_LOCAL_MODEL=`.
3. Open http://127.0.0.1:8080/, choose a speech model, press **Start captions**, allow the microphone.
   `run.bat` downloads the Parakeet model (~465 MB) once during setup and loads it at startup, so the first caption is instant.
4. **Open overlay** shows the captions for the beamer.

Manual start: `pip install -r requirements-local.txt` then `python main.py`. Docker: `docker compose up --build`.

## Layout

- `main.py` - run locally
- `app/api/routes/captions.py` - REST + WebSocket API
- `app/stt/` - speech pipeline: VAD, segmenter, local engines (Nemotron / Parakeet / Whisper / Voxtral), optional Claude cleanup, session manager
- `app/templates/`, `frontend/static/` - console (`/`) and overlay (`/overlay`)
- `scripts/stt_smoke.py` - run a WAV through a real local model and report speed
- `tests/` - `pip install -r requirements-dev.txt && pytest`

## How we work

Backlog, sprints and code are all here on GitHub: see the [Issues](https://github.com/Campus-Caption/CampusCaption/issues) and the organization's project board. Read [CONTRIBUTING.md](CONTRIBUTING.md) before your first pull request.

## Docker

```bash
docker compose up --build
```

Stop with `Ctrl+C` or `docker compose down`.

## Smoke test

```bash
python scripts/stt_smoke.py sample.wav --language de --realtime
python scripts/stt_smoke.py sample.wav --model whisper-turbo
```
(16 kHz mono WAV: `ffmpeg -i in.mp3 -ar 16000 -ac 1 sample.wav`.)

## Tests

`pytest` never downloads a model. To also run the real-Parakeet test, set `CC_TEST_MODEL_DIR` to a folder that holds
the model.
