# syntax=docker/dockerfile:1
# Bundled bge-m3 Ollama image — the embedding model is baked at build time so
# containers reach embedding-ready with ZERO network pull (offline-friendly,
# China-network-friendly). See docs/plans/2026-07-07-bundled-embedding-model-*.
ARG OLLAMA_VERSION=0.32.13
FROM ollama/ollama:${OLLAMA_VERSION}

# Task 0 allowlist — the gguf model-layer digest. Build fails if the pulled
# model drifts from this (guards against `latest` moving under us).
ARG BGE_M3_MODEL_DIGEST=daec91ffb5dd0c27411bd71f29932917c49cf529a641d0168496c3a501e3062c

# Pull once at build, verify the model-layer digest, snapshot the store to /opt
# (NOT /root/.ollama — a runtime named volume mounted there would shadow it),
# then drop the build-time store so nothing double-counts.
RUN set -eux; \
    pid=""; \
    start_ollama() { \
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then \
            if ollama list >/dev/null 2>&1; then \
                return 0; \
            fi; \
            kill "$pid" 2>/dev/null || true; \
            sleep 1; \
        fi; \
        ollama serve & pid="$!"; \
        i=0; \
        while [ "$i" -lt 30 ]; do \
            ollama list >/dev/null 2>&1 && return 0; \
            i=$((i+1)); \
            sleep 1; \
        done; \
        return 1; \
    }; \
    trap '[ -z "$pid" ] || kill "$pid" 2>/dev/null || true' EXIT; \
    model_blob="/root/.ollama/models/blobs/sha256-${BGE_M3_MODEL_DIGEST}"; \
    attempts=0; \
    until start_ollama && ollama pull bge-m3 && [ -f "$model_blob" ]; do \
        attempts=$((attempts + 1)); \
        if [ "$attempts" -ge 3 ]; then \
            find /root/.ollama/models -maxdepth 4 -type f -print | sort >&2 || true; \
            exit 1; \
        fi; \
        sleep $((attempts * 15)); \
    done; \
    mkdir -p /opt/bge-m3-seed; \
    cp -a /root/.ollama/models/blobs /opt/bge-m3-seed/blobs; \
    cp -a /root/.ollama/models/manifests /opt/bge-m3-seed/manifests; \
    rm -rf /root/.ollama/models

COPY docker/seed-bge-m3.sh /usr/local/bin/seed-bge-m3.sh
COPY docker/ollama-bundled-entrypoint.sh /usr/local/bin/ollama-bundled-entrypoint.sh
RUN chmod +x /usr/local/bin/seed-bge-m3.sh /usr/local/bin/ollama-bundled-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/ollama-bundled-entrypoint.sh"]
