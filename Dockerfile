# Test image: the whole application with AWS emulated in memory (moto).
# Not for production; data is lost when the container stops.
#
#   docker build -t mzdy-test .
#   docker run --rm -p 8000:8000 -e ADMIN_PASSWORD=... mzdy-test

FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
# Optional CA bundle for builds behind a TLS-intercepting proxy (see below).
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
# An extra CA bundle can be passed for builds behind a TLS-intercepting proxy:
#   docker build --secret id=ca_bundle,src=/path/to/ca.crt ...
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen --no-install-project
COPY backend/ ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle; fi; \
    uv sync --frozen
COPY --from=frontend /app/frontend/dist /app/frontend/dist

RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"
CMD [".venv/bin/python", "devserver.py", "--host", "0.0.0.0", "--port", "8000"]
