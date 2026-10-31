FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 qa \
    && useradd --system --uid 10001 --gid 10001 --create-home qa \
    && mkdir -p /data /workspace \
    && chown 10001:10001 /data \
    && git config --system --add safe.directory /workspace
WORKDIR /app
# Only the reviewed Python package is copied, never a workspace or credentials.
COPY qa_lab /app/qa_lab
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8765
CMD ["python", "-m", "qa_lab", "--data-dir", "/data", "serve"]
