GO ?= go
BENCH_VEHICLES ?= 100000
BENCH_SECONDS ?= 60

.PHONY: build test vet bench bench-kafka run-stdout lint docker

build: ## compile the binary to bin/simulator
	$(GO) build -o bin/simulator ./cmd/simulator

vet:
	$(GO) vet ./...

test:
	$(GO) test ./...

bench-encode: ## micro-benchmark the OEM encoders
	$(GO) test ./internal/oem -run xxx -bench . -benchmem

bench: build ## 100K vehicles for 60 s into the discard sink; prints achieved msg/s
	SIM_TRANSPORT=discard SIM_VEHICLES=$(BENCH_VEHICLES) SIM_DURATION_S=$(BENCH_SECONDS) SIM_BURST_EVERY_MIN=0 ./bin/simulator 2>&1 | grep -E "^(SUMMARY|.*events/s)"

bench-kafka: build ## same against a live broker (KAFKA_BOOTSTRAP, default localhost:19092)
	SIM_TRANSPORT=kafka SIM_VEHICLES=$(BENCH_VEHICLES) SIM_DURATION_S=$(BENCH_SECONDS) ./bin/simulator 2>&1 | grep -E "^(SUMMARY|.*events/s)"

run-stdout: build ## print a sample of payloads from a small fleet
	SIM_TRANSPORT=stdout SIM_VEHICLES=50 SIM_DURATION_S=5 SIM_STDOUT_SAMPLE=10 ./bin/simulator

docker:
	docker build -t orbit/simulator .
