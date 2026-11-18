"""Measure the production worker after all peers have acknowledged their stock Soldier bodies."""

import multiprocessing
import time
from contextlib import ExitStack
from threading import Event
from unittest.mock import patch

from ow174.game import collision
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.link import peek_connection
from ow174.game.script import graph
from ow174.game.server import GameServer
from tests.runtime.test_game_combat import aim

from .metrics import Tick, Traffic, summarize
from .peers import run_peers

clock = time.perf_counter_ns


class TimedServer(GameServer):
    def __init__(self, terrain):
        super().__init__(
            "127.0.0.1", 0, collision_provider_factory=lambda: collision.StaticCollisionProvider(terrain)
        )
        self.cutoff = 0
        self.finished = Event()
        self.errors = []
        self.traffic = {}
        self.rows = []
        self.ingress_ns = 0
        self.last_end = 0

    def _run(self):
        super()._run(on_iteration_error=self.errors.append)

    def _datagram(self, data, address, now):
        if clock() >= self.cutoff:
            return super()._datagram(data, address, now)
        conn = peek_connection(data)
        client = self.clients[conn]
        before, previous = client.window.expected, client.player.commands.last_frame
        start = clock()
        super()._datagram(data, address, now)
        self.ingress_ns += clock() - start
        self.traffic[conn].received(
            before, client.window.expected, previous, client.player.commands.last_frame
        )

    def phase(self, function, *, send):
        def timed(*args, **kwargs):
            start = clock()
            value = function(*args, **kwargs)
            elapsed = clock() - start
            if send:
                self.delivery_ns += elapsed
                self.sends += 1
            else:
                self.match_ns += elapsed
            return value

        return timed

    def _tick(self, now, *, missed=0):
        self.match_ns = self.delivery_ns = self.sends = 0
        start = clock()
        if start >= self.cutoff:
            if self.cutoff:
                self.finished.set()
            return super()._tick(now, missed=missed)
        cpu = time.thread_time_ns()
        super()._tick(now, missed=missed)
        cpu = time.thread_time_ns() - cpu
        self.last_end = clock()
        self.rows.append(
            Tick(
                self.tick,
                missed,
                self.last_end - start,
                cpu,
                self.match_ns,
                self.delivery_ns,
                self.sends,
                self.ingress_ns,
            )
        )
        self.ingress_ns = 0

    def wait_until(self, condition, peers, timeout=120):
        limit = time.monotonic() + timeout
        while time.monotonic() < limit:
            with self.lock:
                if self.errors:
                    raise RuntimeError("production worker raised an exception") from self.errors[0]
                if not peers.is_alive():
                    raise RuntimeError(f"peer process exited with status {peers.exitcode}")
                if condition():
                    return
            time.sleep(0.02)
        raise RuntimeError(f"timed out waiting for {condition.__name__}")


def run(players, seconds, warmup):
    scene = PRACTICE_RANGE
    terrain = collision.world_for(scene.map_guid, scene.name, build_missing=False, mode=scene.mode_guid)
    if terrain is None:
        raise ValueError("prepared Practice Range collision is missing")
    server = TimedServer(terrain)
    ctx = multiprocessing.get_context("spawn")
    control, remote = ctx.Pipe()
    peers = ctx.Process(target=run_peers, args=(remote,), daemon=True)
    peers.start()
    remote.close()
    try:
        if not control.poll(120) or control.recv() != "ready":
            raise RuntimeError("loopback peers did not start")
        server.start()
        handoffs = server.create_match(
            scene, [(n + 1, f"Peer {n + 1}", SOLDIER, 0, False) for n in range(players)]
        )
        (match,) = server.matches
        roster = match.players
        if len(match.bots) != 15 or any(graph.entity_body(bot.body) is None for bot in match.bots):
            raise ValueError("benchmark requires all 15 authored Practice Range bot bodies")
        control.send((handoffs, server.port))

        def loaded():
            return len(server.clients) == players and all(
                p.spawned and p.steps_done == 4 and p.client.controller_ready and p.mode_script.delivered
                for p in roster
            )

        server.wait_until(loaded, peers)
        control.send(("pick", [p.entity for p in roster]))

        def bound():
            return all(
                p.body_script
                and p.body_script.create_arrived
                and p.body_script.stream.base is not None
                and p.entity not in p.client._pending_possessions
                for p in roster
            )

        server.wait_until(bound, peers)
        with server.lock:
            # Fixed authored target visible from the Soldier spawn. Never inject damage or move a target.
            bot = match.bots[2]
            if any(p.team == bot.team for p in roster):
                raise ValueError("stock target is not hostile")
            x, y, z = bot.position
            angles = [aim(match.combat._eye(p), (x, y + 1, z)) for p in roster]
            control.send(("drive", (server.tick + 1, angles)))
            warm_until = server.tick + warmup

        def warmed():
            return server.tick >= warm_until and all(p.commands.last_frame is not None for p in roster)

        server.wait_until(warmed, peers)
        with ExitStack() as phases:
            with server.lock:
                phases.enter_context(patch.object(match, "update", server.phase(match.update, send=False)))
                for client in server.clients.values():
                    phases.enter_context(
                        patch.object(client, "send_frame", server.phase(client.send_frame, send=True))
                    )
                server.traffic = {h.conn: Traffic() for h in handoffs}
                started_unix_ns = time.time_ns()
                start = clock()
                server.cutoff = start + round(seconds * 1e9)
            server.wait_until(server.finished.is_set, peers, timeout=seconds + 120)
            with server.lock:
                if len(server.clients) != players or any(not p.has_body for p in roster):
                    raise RuntimeError("a benchmark player lost its body or connection")
                control.send(("end", (start, server.cutoff)))
        if not control.poll(30):
            raise RuntimeError("peer counters did not arrive")
        offers = control.recv()
        traffic = [server.traffic[h.conn].summary(o) for h, o in zip(handoffs, offers, strict=True)]
        if any(not p["offered"] for p in traffic):
            raise RuntimeError("a peer offered no input during the measurement")
        report = summarize(server.rows)
        report.update(
            started_unix_ns=started_unix_ns,
            peers=traffic,
            players=players,
            seconds=seconds,
            warmup_frames=warmup,
            last_tick_overrun_ms=round(max(0, server.last_end - server.cutoff) / 1e6, 3),
        )
        return server.rows, report
    finally:
        if peers.is_alive():
            peers.join(timeout=1)
        if peers.is_alive():
            peers.terminate()
            peers.join()
        control.close()
        if server.running:
            server.stop()
