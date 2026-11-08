FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir . && \
    groupadd --gid 10001 relay && useradd --uid 10001 --gid relay relay && \
    mkdir /state && chown relay:relay /state && chmod 700 /state
USER 10001:10001
CMD ["python", "-m", "openbiliclaw.sponsored_relay"]
