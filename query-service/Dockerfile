# syntax=docker/dockerfile:1
# ── Stage 1: Build ──────────────────────────────────────────
FROM golang:1.22-alpine AS builder

WORKDIR /app
RUN apk add --no-cache git ca-certificates

# Deps first so an unrelated source edit doesn't invalidate the module
# download — only a go.mod/go.sum change re-triggers this layer.
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download

COPY . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build -o query-service .

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM alpine:3.19

RUN apk --no-cache add ca-certificates curl

WORKDIR /app
COPY --from=builder /app/query-service .

EXPOSE 8083

CMD ["./query-service"]
