# syntax=docker/dockerfile:1
FROM python:3.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# build-essential/libpq-dev: psycopg2 and friends.
# libglib2.0-0: required by OpenCV even in its headless build.
# libgl1: ultralytics depends on opencv-python (non-headless), which installs
#   alongside ml/requirements.txt's opencv-python-headless. Both provide `cv2`
#   and whichever lands last wins, so libGL has to be present or `import cv2`
#   can fail with "libGL.so.1: cannot open shared object file".
# libgomp1: OpenMP runtime torch links against.
# curl: used by the compose healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# pip 23.x (shipped in this base image) rejects wheels whose metadata name
# casing differs from the requested name — "Jinja2" vs "jinja2",
# "typing_extensions" vs "typing-extensions" — then falls back to building from
# sdist, which fails because the build deps aren't mirrored on the torch index.
# Newer pip normalises those names correctly.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Torch first, from the CPU-only index. ultralytics depends on torch, and the
# default PyPI wheel drags in ~2.5GB of bundled CUDA libraries that are dead
# weight on a CPU inference container. Installing the +cpu build first means the
# ultralytics install below finds its torch requirement already satisfied.
# PyPI stays as a fallback for anything the torch index doesn't mirror; the CPU
# build still wins because PEP 440 sorts "2.x.y+cpu" above plain "2.x.y".
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    torch torchvision

# Two requirement sets: the API's own, plus the ML runtime, because
# app/services/analyzer_service.py imports ml/shelf_analyzer.py in-process.
# ml/requirements.txt is the single source of truth for the ML side — note it
# pins opencv-python-headless rather than opencv-python (see COPY ml/ below).
COPY backend/requirements.txt ./requirements.txt
COPY ml/requirements.txt ./ml-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r ml-requirements.txt

# analyzer_service.py resolves the repo root as parents[3] of its own path. With
# the app at /app, that is "/" — so ml/ has to land at /ml for both the
# `from ml.shelf_analyzer import ShelfAnalyzer` import and the default weights
# path (/ml/weights/empty_space_best.pt) to resolve with no code change.
# This COPY includes weights/empty_space_best.pt: without it ShelfAnalyzer
# silently falls back to downloading stock yolov8n.pt at runtime, which detects
# none of the classes this app is trained for.
COPY ml/ /ml/

COPY backend/ /app/

# Kept outside /app on purpose: compose bind-mounts ./backend over /app for
# live reload, which would shadow anything stored there. Also normalises CRLF —
# the repo is edited on Windows, and \r in a shebang line makes the kernel
# report "no such file or directory" for the interpreter.
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
