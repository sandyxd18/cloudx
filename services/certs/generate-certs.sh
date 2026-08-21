#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# CloudX internal PKI — generate a self-signed CA + per-service certificates
# ═══════════════════════════════════════════════════════════════════════════════
# Produces, under $TLS_CERT_DIR (default ./services/certs/out):
#   ca.crt / ca.key                     — internal certificate authority
#   <service>.crt / <service>.key       — server cert per service (SAN-enabled)
#
# The generated certs enable internal TLS between microservices when
# TLS_ENABLED=true. External-facing TLS is out of scope for this phase.
#
# Usage:
#   ./generate-certs.sh                 # single-node (localhost SANs)
#   SERVICE_HOSTS="10.0.0.11 10.0.0.12" ./generate-certs.sh   # multi-node SANs
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

OUT_DIR="${TLS_CERT_DIR_LOCAL:-$(cd "$(dirname "$0")" && pwd)/out}"
DAYS="${CERT_DAYS:-825}"
# Services that get a server cert. Names match docker-compose service DNS.
SERVICES="${TLS_SERVICES:-auth-api networking-api image-api compute-api block-store-api placement-api dashboard postgres rabbitmq}"
# Extra IP/DNS SANs for multi-node (management IPs). Space separated.
SERVICE_HOSTS="${SERVICE_HOSTS:-}"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

# ── 1. Internal CA ──────────────────────────────────────────────────────────────
if [ ! -f ca.key ]; then
    echo "Generating internal CA..."
    openssl genrsa -out ca.key 4096
    openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
        -subj "/C=ID/O=CloudX/CN=CloudX Internal CA" \
        -out ca.crt
else
    echo "Reusing existing CA (ca.key present)."
fi

# ── 2. Per-service server certs ─────────────────────────────────────────────────
build_san() {
    local svc="$1"
    {
        echo "DNS.1 = ${svc}"
        echo "DNS.2 = localhost"
        echo "IP.1 = 127.0.0.1"
        local i=2 h
        local n=3
        for h in $SERVICE_HOSTS; do
            if [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                echo "IP.${i} = ${h}"; i=$((i+1))
            else
                echo "DNS.${n} = ${h}"; n=$((n+1))
            fi
        done
    }
}

for svc in $SERVICES; do
    echo "Issuing cert for ${svc}..."
    cat > "${svc}.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
distinguished_name = dn
req_extensions = v3_req
[dn]
C = ID
O = CloudX
CN = ${svc}
[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt_names
[alt_names]
$(build_san "$svc")
EOF

    openssl genrsa -out "${svc}.key" 2048
    openssl req -new -key "${svc}.key" -out "${svc}.csr" -config "${svc}.cnf"
    openssl x509 -req -in "${svc}.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out "${svc}.crt" -days "$DAYS" -sha256 \
        -extensions v3_req -extfile "${svc}.cnf"
    rm -f "${svc}.csr" "${svc}.cnf"
done

echo ""
echo "Done. Certificates written to: $OUT_DIR"
echo "Distribute ca.crt to every node and mount \$TLS_CERT_DIR into the containers."
echo "Set TLS_ENABLED=true and switch *_API_URL values to https:// to enforce."
