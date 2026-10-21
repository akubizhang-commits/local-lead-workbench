# syntax=docker/dockerfile:1
# 寻迹线索工作台：服务器部署镜像（AWS trial, design §3.2）。linux/arm64 与 linux/amd64 均可构建。
# Base images are pinned to their multi-architecture index digests (evidence/images.md).

# 1. Frontend: architecture independent, always built on the build machine's own platform.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim@sha256:3ed98443660ff48413b5af19ecefce3b9d50845a901a43420d47cc63a9fddbe9 AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 2. Maps scraper for the target architecture, via the unchanged scripts/install_maps.sh
#    (pinned upstream + stability patches, go test -race, manifest check, its own Playwright browser).
FROM golang:1.27.1-bookworm@sha256:8d48e12ec56735e9358640898b9d9b9fcca110612ed8a5567438c0a1baa24e66 AS maps
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY scripts/install_maps.sh scripts/patch_maps_stability.py scripts/maps_stability.patch scripts/maps_scrapemate_launch.patch /build/scripts/
# Optional Go module proxy for networks without access to proxy.golang.org
# (e.g. --build-arg GOPROXY=https://goproxy.cn,direct). Empty means Go's default.
ARG GOPROXY=
RUN cd /build && PYTHON=python3 GO=/usr/local/go/bin/go sh scripts/install_maps.sh
# The macOS fix (no --single-process) must hold on Linux too.
RUN /build/tools/bin/google-maps-scraper -local-stability-info \
  | python3 -c 'import json,sys; assert json.load(sys.stdin)["chromium_single_process"] is False'
# playwright-go installs its node driver as 0744; the runtime user (10001) must be able to run it.
RUN chmod -R a+rX /build/tools/bin

# 3. Runtime: Python, Python Playwright Chromium (social public reads), non-root user 10001.
FROM python:3.14-slim-bookworm@sha256:58b9cb2dde6dc8ca7cabd10942d9d01ead923811b191f259008942f7b402ca7e AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends procps lsof ca-certificates fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 lw \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/lw lw \
 && mkdir -p /data && chown 10001:10001 /data
COPY requirements.lock requirements-server.lock /app/
RUN pip install --no-cache-dir --root-user-action=ignore -r /app/requirements.lock
RUN pip install --no-cache-dir --root-user-action=ignore --require-hashes --no-deps -r /app/requirements-server.lock
RUN python -m playwright install-deps chromium && rm -rf /var/lib/apt/lists/*
USER lw
RUN python -m playwright install chromium
USER root
COPY --from=maps /build/tools/bin /app/tools/bin
COPY --from=maps --chown=10001:10001 /build/tools/bin/playwright-browsers /app/tools/bin/playwright-browsers
# The browsers directory itself (playwright-go takes a lock file there) also belongs to 10001.
RUN chown 10001:10001 /app/tools/bin/playwright-browsers
COPY leadworkbench /app/leadworkbench
COPY scripts/_probe_session.py /app/scripts/
# Child processes (Maps guard, export jobs) run with a filtered environment and another working
# directory, so PYTHONPATH alone is not enough: put /app on every interpreter's path.
RUN python -c 'import site; open(site.getsitepackages()[0] + "/leadworkbench-app.pth", "w").write("/app\n")'
COPY --from=frontend /src/frontend/dist /app/frontend/dist
ENV LEAD_MODE=server \
    LEAD_DATA_DIR=/data \
    LEAD_BIND=0.0.0.0 \
    LEAD_PORT=8000 \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001:10001
EXPOSE 8000
VOLUME /data
CMD ["python", "-m", "leadworkbench.serve"]
