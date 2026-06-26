# Remote Apple Calendar MCP connector (Streamable HTTP).
FROM python:3.12-slim

# Install uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install into the system environment (no venv needed in a container).
RUN uv pip install --system .

ENV HOST=0.0.0.0
# Most hosts inject PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

CMD ["apple-calendar-mcp-http"]
