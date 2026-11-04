# API image (also runs migrations and operator commands).
#   docker build -f infra/docker/backend.Dockerfile -t studio-backend .
ARG PYTHON_BASE=python:3.13-slim-bookworm
FROM ${PYTHON_BASE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system studio && useradd --system --gid studio --home /app --shell /usr/sbin/nologin studio
WORKDIR /app
COPY contracts/ contracts/
COPY backend/ backend/
RUN pip install ./contracts ./backend

FROM base AS api
USER studio
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
