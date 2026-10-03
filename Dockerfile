# Browser-capable image supports linux/amd64 and linux/arm64.
# Playwright Chromium does not provide the arm/v7 or 386 runtime required
# by dynamic browser sources; these 32-bit targets are not advertised.
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
    OPENBILICLAW_IN_CONTAINER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/openbiliclaw-browsers

WORKDIR /app

# Dependency layer, keyed on pyproject.toml alone: `git pull` + rebuild
# reuses this cached layer unless the dependency list actually changed.
# Copying src/ before installing dependencies would invalidate the layer
# on every source edit and force a full multi-minute reinstall.
COPY pyproject.toml ./
RUN python -c "import tomllib, pathlib; project = tomllib.load(open('pyproject.toml', 'rb'))['project']; deps = project['dependencies']; pathlib.Path('/tmp/requirements.txt').write_text('\n'.join(deps) + '\n')" \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY README.md config.example.toml ./
COPY src ./src

# Dependencies are already satisfied by the layer above; this only
# installs the openbiliclaw package itself, so rebuilds after source
# changes finish in seconds.
RUN pip install --no-deps .

EXPOSE 8420

# Healthcheck via Python stdlib so we don't bloat the image with curl.
# Hits /api/health every 30s after a 20s warmup. Docker / Compose use
# this to report whether the backend is actually ready, not just running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8420/api/health', timeout=4).status == 200 else 1)" \
    || exit 1

CMD ["python", "-m", "openbiliclaw.docker_runtime", "openbiliclaw", "serve-api", "--host", "0.0.0.0", "--port", "8420"]
