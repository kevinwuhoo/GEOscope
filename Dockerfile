FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

LABEL org.geo-metadata-index.search-backend="elasticsearch" \
      org.geo-metadata-index.embedding-variant="gemini_embedding_2_3072_v1"

USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "geo_index.mcp_server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
