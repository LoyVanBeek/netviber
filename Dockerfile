FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# Dependencies first, so this layer is cached across code changes.
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY netviber/ netviber/
COPY static/ static/
RUN uv sync --frozen --no-dev

# Run as a non-root user whose UID matches the host user that owns the
# ./data volume mount (overridable via the UID build arg).
ARG UID=1000
RUN useradd --uid "${UID}" --create-home app && chown -R app:app /app
USER app
ENV UV_NO_SYNC=1
EXPOSE 8377

CMD ["/app/.venv/bin/netviber", "--host", "0.0.0.0", "--port", "8377", "--data-dir", "/app/data", "--no-open"]
