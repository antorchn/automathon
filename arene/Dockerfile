# Site de l'arène Automathon (API, pages, classement, build WebGL 1.0.0).
# Build depuis la racine du dépôt après `python scripts/fetch_sources.py --extract`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY arene/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY arene/ /app/arene/
COPY sources/extracted/webgl/ /app/webgl/
COPY sources/extracted/reference_agents/ /app/reference_agents/

ENV ARENE_WEBGL_DIR=/app/webgl \
    ARENE_REF_AGENTS_DIR=/app/reference_agents

EXPOSE 8000

CMD ["uvicorn", "arene.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
