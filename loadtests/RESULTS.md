# Load test results: ticket rush

**Scenario** ([locustfile.py](locustfile.py)): 300 concurrent users for 60 s, spawned at
30/s, against an event with a scarce **front row (50 tickets)** and a large
**general zone (5,000 tickets)**.

| Simulated user | Share | Behaviour |
|---|---|---|
| Browser | 60% | Lists and searches the catalog, checks availability |
| Buyer | 30% | Checks availability, reserves 1–2 tickets (70% go for the front row) |
| Hesitant | 10% | Reserves, waits, cancels (tickets go back to the pool) |

`409 sold out` and `429 rate limited` count as successes: in a rush they are the
correct answer. Only unexpected responses count as failures.

**Environment:** a laptop (Intel i7-11370H, 8 threads, 16 GB RAM) running
*everything*: both services, Postgres ×2, Redis, RabbitMQ, Prometheus, Grafana,
Jaeger and Locust itself. Development servers: Django `runserver` and a single
`uvicorn` worker. **These are not production numbers**: they set a baseline and
show the system's behaviour under contention.

## Results (2026-10-06)

| Endpoint | Requests | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|---:|
| booking: availability (cached 5 s in Redis) | 4,107 | 4 ms | 67 ms | 140 ms | 240 ms |
| booking: reserve (front row, contended) | 1,667 | 9 ms | 170 ms | 260 ms | 350 ms |
| booking: reserve (general) | 1,280 | 12 ms | 200 ms | 310 ms | 430 ms |
| booking: cancel | 514 | 14 ms | 130 ms | 260 ms | 330 ms |
| catalog: list events | 1,922 | 17 ms | 48 ms | 92 ms | 110 ms |
| catalog: search | 659 | 16 ms | 42 ms | 80 ms | 100 ms |
| **All** | **10,149** | **12 ms** | **90 ms** | **230 ms** | **430 ms** |

- **Throughput:** ~170 requests/s sustained.
- **Failures:** 1 of 10,149 (0.01%), a `ConnectionResetError`: the client reused
  a keep-alive connection at the moment uvicorn closed it for being idle (5 s).
  In production a reverse proxy (Nginx) manages client connections.
- **Reservation outcomes** (from Prometheus): ~1,100 created, ~1,250 sold out,
  ~700 rate limited.

## Correctness under load: no ticket sold twice

After the run, `make loadtest-check` verifies `held + available == total`:

```
zone 900101: total=50   held=50  available=0    -> OK, no oversell
zone 900102: total=5000 held=759 available=4241 -> OK, no oversell
```

Hundreds of buyers fought for the 50 front-row tickets: **exactly 50** were
held, everyone else got a clean `409`. The atomic conditional `UPDATE` holds
under real concurrent load, not just in the unit test.

## What the load test found

**FastAPI's automatic telemetry was doubling the tracing work.** FastAPI ≥ 0.142
adds its own OpenTelemetry exporters when `OTEL_EXPORTER_OTLP_ENDPOINT` is set,
on top of ours: every span was exported twice, and metrics were sent to Jaeger,
which rejects them (`404` in the logs). Fixed with
`FastAPI(telemetry={"auto_configure": False})`:

| | Before the fix | After |
|---|---:|---:|
| p95 (all requests) | 170 ms | **90 ms** |
| Max | 1,600 ms | **430 ms** |
| Error log lines in booking | 1 | **0** |

**The rate limiter dominates for aggressive buyers.** A simulated buyer tries
to reserve every 1–2 s, more than the 10/min allowed: a large share of attempts
get `429`. That is the limiter doing its job against bot-like behaviour.

## Reproduce

```bash
make up                   # infrastructure
make run                  # catalog   (another terminal)
make run-booking          # booking   (another terminal)
make loadtest             # seeds event 900001, runs Locust, checks for oversell
make loadtest-clean       # removes the load-test data
```

Watch it live in Grafana (http://localhost:3000, *TicketHub overview*). The full
Locust HTML report is written to `loadtests/results/report.html`.
