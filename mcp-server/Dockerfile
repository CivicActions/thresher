FROM python:3.11-slim

WORKDIR /app

# Install uv for package management
RUN pip install --no-cache-dir uv

# Copy source and install from local package
COPY pyproject.toml README.md ./
COPY src/ src/
RUN uv pip install --system --no-cache-dir .

# Expose the default port for HTTP transport
EXPOSE 8000

# Bind to all interfaces (required for Docker/K8s)
ENV FASTMCP_HOST="0.0.0.0"
ENV FASTMCP_PORT="8000"
# Stateless HTTP for horizontal scaling behind load balancers
ENV FASTMCP_STATELESS_HTTP="true"

# Default env vars (override at runtime)
ENV QDRANT_URL=""
ENV QDRANT_API_KEY=""
ENV QDRANT_READ_ONLY="true"

# Run the server with streamable-http transport
CMD ["mcp-server-qdrant", "--transport", "streamable-http"]
