# Worker Automathon : arbitre Python 3.12 + Headless Linux x86-64 + client Docker CLI.
# Build depuis la racine du dépôt après `python scripts/fetch_sources.py --extract`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    DOTNET_gcServer=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        libstdc++6 \
        zlib1g \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && . /etc/os-release \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir "httpx>=0.28" "pydantic>=2.10" "pyzmq>=26.0"

COPY sources/extracted/kit/Headless/ /app/Headless/
RUN chmod +x /app/Headless/Headless

COPY sources/extracted/kit/PythonAI/bridge/ /app/PythonAI/bridge/
COPY worker/ /app/worker/

ENV PYTHONPATH="/app:/app/PythonAI" \
    AUTOMATHON_HEADLESS_PATH="/app/Headless/Headless"

ENTRYPOINT ["python", "-u", "-m", "worker.main"]
