FROM node:22-bookworm-slim AS viewer
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./web
COPY tsconfig.json vite.config.js ./
COPY scripts/viewer-notices.mjs ./scripts/viewer-notices.mjs
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.11.21 AS uv
FROM python:3.12-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md THIRD_PARTY_NOTICES.md ./
COPY voicedesign ./voicedesign
COPY --from=viewer /src/voicedesign/static ./voicedesign/static
RUN uv build --wheel --out-dir /wheels && uv export --frozen --no-dev --no-emit-project --output-file /requirements.txt

FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 libsm6 libxext6 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /requirements.txt /tmp/requirements.txt
COPY --from=builder /wheels /tmp/wheels
RUN uv pip install --system -r /tmp/requirements.txt && uv pip install --system --no-deps /tmp/wheels/*.whl && useradd --create-home --uid 10001 cad && mkdir /data && chown cad:cad /data
USER cad
WORKDIR /home/cad
ENV VOICEDESIGN_HOME=/home/cad/.local/share/VoiceDesign3D
EXPOSE 8743
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8743/api/health',timeout=3)"
ENTRYPOINT ["voicedesign"]
CMD ["--workspace", "/data", "serve", "--container"]
