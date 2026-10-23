# School Information Adapter — production image (M5 deploy artifact).
#
# Build from the repo root:
#   docker build -f deploy/Dockerfile -t haliffer-school-mcp .
#
# Required environment (see src/school/app/config.py):
#   SCHOOL_DB_PATH         SQLite file on a persistent volume (/data/school.db)
#   SCHOOL_HAC_CACHE_PATH  grades cache JSON inside the read-only cache mount
#   SCHOOL_MCP_TOKEN       bearer token the gateway presents to the app
#   SCHOOL_DECK_REGISTRY   deck-registry JSON (M3 format; example in
#                          tests/unit/driver_web/fixtures/deck_registry.json)
# Optional: SCHOOL_STUDENT_ID, SCHOOL_STUDENT_NAME, SCHOOL_TEAM_SITE_URL,
# SCHOOL_HOST, SCHOOL_PORT, SCHOOL_STALE_HAC_HOURS, SCHOOL_STALE_WEB_HOURS.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCHOOL_HOST=0.0.0.0 \
    SCHOOL_PORT=8765

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . \
    && useradd --create-home --uid 10001 school \
    && mkdir -p /data \
    && chown school:school /data

USER school
EXPOSE 8765

CMD ["python", "-m", "school.app", "serve"]
