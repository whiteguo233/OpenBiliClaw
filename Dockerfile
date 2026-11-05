# Multi-arch image: python:3.11-slim is published on Docker Hub for
# linux/amd64, linux/arm64, linux/arm/v7, linux/386 and others, so this
# Dockerfile builds the OpenBiliClaw backend on Intel Macs, Apple Silicon
# (M1/M2/M3), x86_64 Linux, ARM Linux (Raspberry Pi 4/5), and Windows
# with Docker Desktop (which runs linux containers via WSL2 by default).
FROM python:3.11-slim

# Link the GHCR package to this repository and make `docker inspect`
# self-describing.
LABEL org.opencontainers.image.source="https://github.com/whiteguo233/OpenBiliClaw" \
      org.opencontainers.image.description="OpenBiliClaw backend — local-first cross-platform AI content discovery agent" \
      org.opencontainers.image.licenses="MIT"

# OPENBILICLAW_IN_CONTAINER makes install-mode detection ("docker") work on
# every container runtime — /.dockerenv exists under Docker but not under
# containerd/Kubernetes, so the marker is baked into the image itself.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENBILICLAW_IN_CONTAINER=1

WORKDIR /app

# Dependency layer, keyed on pyproject.toml alone: `git pull` + rebuild
# reuses this cached layer unless the dependency list actually changed.
# Copying src/ before installing dependencies would invalidate the layer
# on every source edit and force a full multi-minute reinstall.
COPY pyproject.toml ./
RUN python -c "import tomllib, pathlib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(deps) + '\n')" \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY README.md config.example.toml ./
COPY src ./src

# Dependencies are already satisfied by the layer above; this only
# installs the openbiliclaw package itself, so rebuilds after source
# changes finish in seconds.
RUN pip install --no-deps .

# Native multi-arch build resolves only the matching Core-pinned signed SDK.
RUN python -m openbiliclaw.runtime.sponsored_bundle --stage /opt/openbiliclaw-sponsored
ENV OPENBILICLAW_SPONSORED_BUNDLE=/opt/openbiliclaw-sponsored

EXPOSE 8420

# Healthcheck via Python stdlib so we don't bloat the image with curl.
# Hits /api/health every 30s after a 20s warmup. Docker / Compose use
# this to report whether the backend is actually ready, not just running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health', timeout=4).status == 200 else 1)" \
    || exit 1

CMD ["python", "-m", "openbiliclaw.docker_runtime", "openbiliclaw", "serve-api", "--host", "0.0.0.0", "--port", "8420"]
