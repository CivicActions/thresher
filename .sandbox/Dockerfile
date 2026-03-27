# Sandbox template for Thresher development
#
# Build on a machine with internet access (e.g. your local Docker Desktop):
#   docker build -t thresher-sandbox:latest .sandbox/
#
# Launch:
#   docker sandbox create --template thresher-sandbox:latest copilot-thresher .
#
# Alternative (capture running sandbox directly — no build needed):
#   docker sandbox save copilot-thresher thresher-sandbox:latest

FROM docker/sandbox-templates:copilot

USER root

# --- System packages ---
# libmagic-mgc + libmagic1t64: MIME detection for python-magic
# (Ubuntu 25.10 uses t64 transition package names)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic-mgc \
    libmagic1t64 \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# --- kubectl ---
ARG KUBECTL_VERSION=v1.32.3
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl" \
      -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl

# --- uv (latest — base image ships an older version) ---
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --- Proxy CA bundle fix ---
# Docker sandbox proxy does MITM on HTTPS. When --bypass-host is used for a
# domain, the connection goes direct but SSL_CERT_FILE may point to only the
# proxy CA. This script creates a combined bundle at startup.
COPY setup-ca-bundle.sh /usr/local/bin/setup-ca-bundle.sh
RUN chmod +x /usr/local/bin/setup-ca-bundle.sh

# --- Functional test services startup ---
COPY start-services.sh /usr/local/bin/start-services.sh
RUN chmod +x /usr/local/bin/start-services.sh

# --- Sandbox init script (runs on sandbox start) ---
COPY sandbox-init.sh /usr/local/bin/sandbox-init.sh
RUN chmod +x /usr/local/bin/sandbox-init.sh

USER agent
