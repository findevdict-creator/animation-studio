# Blender worker image.
#   docker build -f infra/docker/worker.Dockerfile -t studio-worker .
#
# BLENDER_SOURCE=wheel (default) installs Blender as the bpy module from PyPI and runs jobs
# with STUDIO_BLENDER_MODE=wheel. BLENDER_SOURCE=binary downloads the official Linux build,
# checks its SHA-256 and runs jobs with the blender executable; use it for GPU render workers.
ARG BLENDER_SOURCE=wheel
ARG PYTHON_BASE=python:3.13-slim-bookworm

FROM ${PYTHON_BASE} AS common
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Shared libraries bpy links against even when headless (from ldd on bpy 5.2.3; the X11
# and GL client libraries are loaded, never used to open a display).
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libx11-6 libxext6 libxfixes3 libxi6 libxrender1 libxkbcommon0 libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system studio && useradd --system --gid studio --home /work --shell /usr/sbin/nologin studio \
    && mkdir -p /work && chown studio:studio /work
WORKDIR /app
COPY contracts/ contracts/
COPY backend/ backend/
COPY blender_engine/ blender_engine/
COPY workers/ workers/
RUN pip install ./contracts ./backend ./blender_engine ./workers

FROM common AS blender-wheel
ARG BPY_VERSION=5.2.3
RUN pip install "bpy==${BPY_VERSION}"
ENV STUDIO_BLENDER_MODE=wheel

FROM common AS blender-binary
ARG BLENDER_VERSION=5.2.3
ARG BLENDER_SHA256
RUN test -n "${BLENDER_SHA256}" || (echo "BLENDER_SHA256 is required for BLENDER_SOURCE=binary" && exit 1)
RUN apt-get update && apt-get install -y --no-install-recommends xz-utils curl ca-certificates libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    series="$(echo ${BLENDER_VERSION} | cut -d. -f1,2)"; \
    curl -fsSL -o /tmp/blender.tar.xz "https://download.blender.org/release/Blender${series}/blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; rm /tmp/blender.tar.xz; \
    py="$(ls -d /opt/blender/${series}/python/bin/python3*| head -1)"; \
    "$py" -m ensurepip; "$py" -m pip install --no-cache-dir /app/contracts /app/blender_engine
ENV STUDIO_BLENDER_MODE=binary STUDIO_BLENDER_BINARY=/opt/blender/blender

FROM blender-${BLENDER_SOURCE} AS worker
ENV STUDIO_WORK_ROOT=/work
USER studio
WORKDIR /work
CMD ["celery", "-A", "studio_workers.tasks", "worker", "-Q", "blender", "--concurrency", "1", "--hostname", "blender@%h"]
