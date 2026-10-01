# syntax=docker/dockerfile:1
FROM golang:1.23-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/simulator ./cmd/simulator

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/simulator /simulator
USER nonroot:nonroot
EXPOSE 9101
ENTRYPOINT ["/simulator"]
