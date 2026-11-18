# Practice Range worker performance

Measure a single Python game worker hosting one Practice Range room on build `1.74.0.0.104319`. Each simulated Soldier uses a separate loopback UDP socket, performs the real encrypted game handshake, selects a hero and acknowledges delivered state. The measurement starts after all connections possess their bodies and 112 active simulation frames have elapsed. The room requires the prepared collision world and all 15 stock bot body definitions.

## Reproduce a run

From a checkout with the [development environment](../CONTRIBUTING.md) installed, select the private prepared pack with `ORPHEUS_RUNTIME_DIR`. Each invocation needs a new or empty local output directory.

```console
python -m scripts.practice_benchmark --test-players 1 --seconds 8 --out work/perf/p1
python -m scripts.practice_benchmark --test-players 2 --seconds 8 --out work/perf/p2
python -m scripts.practice_benchmark --test-players 4 --seconds 8 --out work/perf/p4
python -m scripts.practice_benchmark --test-players 8 --seconds 8 --out work/perf/p8
```

Repeat the 2- and 1-player runs in fresh directories to expose variability. Record the host model, operating system, Python version and competing load. Use the same source revision, prepared manifest and workload settings on each machine. The script writes one row per server callback to `ticks.csv`. The aggregate `summary.json` also identifies the source and manifest. Keep raw files under the ignored `work/` directory or another private location.

## What the counters measure

- A separate process offers one command per player every 16 ms on its own clock, alternating forward/backward input and intermittent rifle fire. Firing is aimed at the same authored target from the stock spawn. A delayed generator skips expired slots. The report counts the actual sends.
- `tick_wall` and `tick_cpu` time the production worker callback on its own thread. `match_update` covers room simulation and preparing state for viewers. `send_frame_on_send_ticks` covers transmission work for all peers on callbacks that send. `tick_plus_ingress_wall` also includes time in datagram handling since the preceding callback. Percentiles use nearest rank. Windows thread CPU samples can be coarser than the wall clock.
- `missed_ticks` sums the production scheduler's skipped simulation frames. Each callback that starts inside the observation interval runs to completion. The initial and final scheduler boundaries can make callbacks plus skipped frames differ slightly from the nominal duration divided by 16 ms. The summary gives the last callback's overrun.
- Per-peer `read` and `accepted` count datagrams received and fresh inputs applied during the window. These can include commands offered during warm-up. `accepted_current` additionally matches the accepted sequence to an offer made during the same half-open interval. An unread offer may still be queued. A transport sequence gap becomes observable only after a newer datagram is read.
- These measurements exercise the game worker with synthetic players. Original-client frame rate, input batching, visual latency, WAN delivery and other heroes or maps require separate measurements.
