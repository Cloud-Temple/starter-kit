# WAF Caddy + Coraza + OWASP CRS + rate limiting.
# Les plugins et les modules Go ayant reçu des correctifs de sécurité sont figés
# explicitement. L'image finale minimale évite de conserver le binaire Caddy
# d'origine et sa SBOM obsolète dans une couche inférieure.
FROM caddy:2.11.4-builder-alpine@sha256:1a1689db91cfb390b2d856a1b3774e796852822cd723fa54c475b272f82bb4b7 AS builder

RUN xcaddy build v2.11.4 \
    --with github.com/corazawaf/coraza-caddy/v2@v2.5.0 \
    --with github.com/mholt/caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db \
    --replace golang.org/x/crypto=golang.org/x/crypto@v0.56.0 \
    --replace golang.org/x/net=golang.org/x/net@v0.58.0 \
    --replace google.golang.org/grpc=google.golang.org/grpc@v1.83.2

FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40

RUN apk add --no-cache ca-certificates tzdata \
    && addgroup -S caddy \
    && adduser -S -D -H -G caddy caddy \
    && mkdir -p /config/caddy /data/caddy /srv /etc/caddy \
    && chown -R caddy:caddy /config /data /srv /etc/caddy

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# Le mode TLS direct peut écouter sur 80/443 sans exécuter Caddy en root.
RUN apk add --no-cache --virtual .setcap-deps libcap-utils \
    && setcap cap_net_bind_service=+ep /usr/bin/caddy \
    && getcap /usr/bin/caddy | grep -F 'cap_net_bind_service=ep' \
    && apk del .setcap-deps

ENV XDG_CONFIG_HOME=/config
ENV XDG_DATA_HOME=/data

USER caddy
WORKDIR /srv

EXPOSE 8082

# Le Caddyfile est monté en lecture seule par Docker Compose.
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
