# Dev/test-only image running tally-bridge inside the compose network
# against mock-tally, so the tunnel can be exercised with `docker compose up`
# instead of building+running the binary by hand. A real deployment runs
# this same binary directly on/near the customer's actual Tally server, not
# as a container in our stack — see main.go's own comment for cross-compiling
# a standalone binary (e.g. for Windows).
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o tally-bridge .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/tally-bridge /usr/local/bin/tally-bridge
ENTRYPOINT ["/usr/local/bin/tally-bridge"]
