FROM rust:bullseye AS builder

RUN apt-get -y update \
    && apt-get -y install ca-certificates \
                          clang \
                          libssl-dev \
                          llvm \
    && rm -rf /var/lib/apt/lists/*

COPY qbench /qbench

WORKDIR /qbench

RUN echo "Building..." \
    && cargo build --release \
    && mkdir -p /qbench/bin \
    && mv target/release/qbench /qbench/bin \
    && chmod +x /qbench/bin/qbench

FROM debian:bullseye-slim AS qbench

LABEL org.opencontainers.image.title="Quickwit"
LABEL maintainer="Quickwit, Inc. <hello@quickwit.io>"
LABEL org.opencontainers.image.vendor="Quickwit, Inc."
LABEL org.opencontainers.image.licenses="MIT"

RUN apt-get -y update \
    && apt-get -y install ca-certificates \
                          libssl1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /qbench/bin/qbench /qbench

RUN /qbench --help

ENTRYPOINT ["/qbench"]
