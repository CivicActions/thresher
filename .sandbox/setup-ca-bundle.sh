#!/usr/bin/env bash
# Creates a combined CA bundle (certifi + proxy CA) so that both proxied and
# direct (bypassed) HTTPS connections work. Without this, bypassed hosts fail
# because SSL_CERT_FILE points only to the proxy CA, not real root CAs.
#
# Run once at sandbox start or before running tests.

set -euo pipefail

PROXY_CA="/usr/local/share/ca-certificates/proxy-ca.crt"
COMBINED="/tmp/combined-ca-bundle.pem"

if [ ! -f "$PROXY_CA" ]; then
    echo "setup-ca-bundle: No proxy CA found at $PROXY_CA — skipping (not a proxied sandbox)"
    exit 0
fi

# Find the certifi bundle (in whichever venv is active, or system)
CERTIFI_BUNDLE=""
if command -v python3 &>/dev/null; then
    CERTIFI_BUNDLE=$(python3 -c "
try:
    import certifi; print(certifi.where())
except ImportError:
    import ssl; print(ssl.get_default_verify_paths().cafile or '')
" 2>/dev/null || true)
fi

# Fallback to system CA bundle
if [ -z "$CERTIFI_BUNDLE" ] || [ ! -f "$CERTIFI_BUNDLE" ]; then
    for candidate in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
        if [ -f "$candidate" ]; then
            CERTIFI_BUNDLE="$candidate"
            break
        fi
    done
fi

if [ -z "$CERTIFI_BUNDLE" ] || [ ! -f "$CERTIFI_BUNDLE" ]; then
    echo "setup-ca-bundle: WARNING — could not find system CA bundle, using proxy CA only"
    cp "$PROXY_CA" "$COMBINED"
else
    cat "$CERTIFI_BUNDLE" "$PROXY_CA" > "$COMBINED"
    echo "setup-ca-bundle: Combined bundle created at $COMBINED"
fi

export SSL_CERT_FILE="$COMBINED"
export REQUESTS_CA_BUNDLE="$COMBINED"

# Write exports for shells to source
cat > /tmp/ca-bundle-env.sh << EOF
export SSL_CERT_FILE="$COMBINED"
export REQUESTS_CA_BUNDLE="$COMBINED"
EOF

echo "setup-ca-bundle: Source /tmp/ca-bundle-env.sh or set SSL_CERT_FILE=$COMBINED"
