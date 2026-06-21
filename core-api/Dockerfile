# ── Stage 1: Build ──────────────────────────────────────────
FROM golang:1.22-alpine AS builder

WORKDIR /app

# Copy source and build
COPY . .
RUN apk add --no-cache git ca-certificates && go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o core-api .

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM alpine:3.19

RUN apk --no-cache add ca-certificates curl

WORKDIR /app
COPY --from=builder /app/core-api .

EXPOSE 8081

CMD ["./core-api"]
