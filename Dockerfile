FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# WITH_LOCAL=1 also installs the offline speech models' runtimes (larger image).
ARG WITH_LOCAL=1
COPY requirements.txt requirements-local.txt ./
RUN if [ "$WITH_LOCAL" = "1" ]; then pip install --no-cache-dir -r requirements-local.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi

COPY app ./app
COPY frontend ./frontend
COPY main.py .

RUN mkdir -p /app/data/models

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
