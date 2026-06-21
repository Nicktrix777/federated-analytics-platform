# ── Stage 1: Build ──────────────────────────────────────────
FROM golang:1.22-alpine AS builder

WORKDIR /app

COPY . .
RUN apk add --no-cache git ca-certificates && go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o query-service .

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM alpine:3.19

RUN apk --no-cache add ca-certificates curl

WORKDIR /app
COPY --from=builder /app/query-service .

EXPOSE 8083

CMD ["./query-service"]
