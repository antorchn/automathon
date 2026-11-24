# Image des agents Automathon : Python 3.12 + dépendances figées du kit 1.0.0 + hôte d'agent.
# Build depuis la racine du dépôt après `python scripts/fetch_sources.py --extract`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    NUMBA_CACHE_DIR=/tmp/numba_cache \
    TORCH_HOME=/tmp/torch \
    MPLCONFIGDIR=/tmp/mpl \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /kit/PythonAI
COPY sources/extracted/kit/PythonAI/pyproject.toml sources/extracted/kit/PythonAI/uv.lock ./
RUN uv sync --frozen --no-dev --python python3.12 \
    && rm -rf /root/.cache

COPY sources/extracted/kit/PythonAI/bridge/ /kit/PythonAI/bridge/
COPY sources/extracted/kit/PythonAI/core/ /kit/PythonAI/core/

ENV PATH="/kit/PythonAI/.venv/bin:$PATH" \
    PYTHONPATH="/kit/PythonAI" \
    AUTOMATHON_KIT_PYTHON="/kit/PythonAI"

COPY agent_host/host.py /app/host.py

RUN groupadd -g 1000 agent && useradd -u 1000 -g 1000 -M -d /tmp -s /usr/sbin/nologin agent
USER 1000:1000

ENTRYPOINT ["python", "-u", "/app/host.py"]
