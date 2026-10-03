# MinIO for local development only, built from a pinned source tag.
#
# The official minio/minio image was withdrawn from Docker Hub and quay.io and the upstream
# repository was archived in 2026 (CLAUDE.md O5, decision D24). RELEASE.2025-10-15 is the last
# upstream release and includes the October 2025 security fix. Never expose this beyond
# 127.0.0.1; production uses Cloud Storage through the same S3-style BlobStore port.
FROM golang:1.24.9-bookworm AS build
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
RUN CGO_ENABLED=0 go install -trimpath -ldflags "-s -w" github.com/minio/minio@${MINIO_VERSION}

FROM debian:bookworm-20260918-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /go/bin/minio /usr/local/bin/minio
USER minio
VOLUME ["/data"]
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
