# Practice Range worker performance

Measure a single Python game worker hosting one Practice Range room on build `1.74.0.0.104319`. Each simulated Soldier uses a separate loopback UDP socket, performs the real encrypted game handshake, selects a hero and acknowledges delivered state. The measurement starts after all connections possess their bodies and 112 active simulation frames have elapsed. The room requires the prepared collision world and all 15 stock bot body definitions.

## Reproduce a run

From a clean checkout with the [development environment](../CONTRIBUTING.md) installed, select the private prepared pack with `ORPHEUS_RUNTIME_DIR`. Each invocation needs a new or empty local output directory.

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
- Per-peer `read` and `accepted` count datagrams received and fresh inputs that advanced the player's command frame during the window. These can include commands offered during warm-up. `accepted_current` additionally matches the accepted sequence to an offer made during the same half-open interval. An unread offer may still be queued. A transport sequence gap becomes observable only after a newer datagram is read.
- These measurements exercise the game worker with synthetic players. Original-client frame rate, input batching, visual latency, WAN delivery and other heroes or maps require separate measurements.

## Windows baseline

Six eight-second windows ran on 2026-11-18 with Python 3.12.0, Windows 11 build 26300 and an Intel Core i7-9750H (6 cores, 12 logical processors). The source was `19fdf14660fa7e48c2fa05c5cc6e781ea21d3edf` and the prepared manifest SHA-256 was `8379095e4c7fc4175e63562503dd820fa375faaeec0a974fd68d54ad676a675e`. The machine also ran desktop processes. Host busy is the time-weighted whole-machine CPU use during each window, including the load generator.

| Start (UTC) | Players | Host busy | Callbacks | Missed ticks | Tick wall P50 / P95 / P99 (ms) | Tick CPU P50 / P95 / P99 (ms) |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 19:29:56 | 1 | 19.1% | 499 | 1 | 5.564 / 11.912 / 17.541 | 0.000 / 15.625 / 15.625 |
| 19:30:19 | 2 | 21.0% | 500 | 0 | 6.123 / 17.115 / 20.340 | 0.000 / 31.250 / 31.250 |
| 19:30:40 | 4 | 26.5% | 348 | 151 | 15.017 / 39.133 / 45.734 | 15.625 / 46.875 / 62.500 |
| 19:31:05 | 8 | 42.8% | 62 | 439 | 112.835 / 289.415 / 336.734 | 109.375 / 296.875 / 343.750 |
| 19:31:27 | 2 | 27.3% | 475 | 25 | 6.403 / 21.526 / 30.948 | 0.000 / 31.250 / 31.250 |
| 19:31:48 | 1 | 19.9% | 500 | 0 | 5.568 / 10.617 / 14.513 | 0.000 / 15.625 / 15.625 |

| Players | Trial | Offered | Read | Accepted | Accepted from same window |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | Ascending | 500 | 507 | 507 | 500 |
| 2 | Ascending | 1002 | 1053 | 1053 | 975 |
| 4 | Ascending | 2000 | 465 | 465 | 83 |
| 8 | Ascending | 4000 | 64 | 64 | 0 |
| 2 | Repeat | 1000 | 920 | 920 | 920 |
| 1 | Repeat | 500 | 500 | 500 | 500 |

The four-player run accepted 83 of the 2,000 inputs offered within its window. At eight players, all 64 commands processed had been sent before the window opened. The server observed zero transport sequence gaps among the packets it read during the six windows. At eight players, room update P95 was 268.39 ms and aggregate packet sending on send callbacks was 24.53 ms. Its 62 samples make its P99 the maximum observed callback. The repeat runs give a sense of desktop variability. The Windows CPU clock advanced in 15.625 ms increments in a short calibration, so a zero median can include real CPU work.

A matching Raspberry Pi 4 result and a Rust comparison are pending.
