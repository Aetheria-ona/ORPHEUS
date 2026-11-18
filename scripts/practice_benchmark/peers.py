"""Independent-clock protocol peers using real UDP on loopback."""

import select
import socket
import time
from contextlib import ExitStack

from ow174.game import messages
from ow174.game.bits import BitReader, BitWriter
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN
from ow174.game.server import TICK_SECONDS
from tests.runtime.test_server_journal import Peer, pick

from .metrics import offered_range


class SocketPeer(Peer):
    """Use the existing protocol model's cipher, sequence, ACK window and reliable messages."""

    def __init__(self, handoff, destination, sock):
        super().__init__(self, handoff, [], destination)
        self.sock = sock
        self.syn = self.welcomed = self.loaded = False
        self.previous_frame = 0
        self.offers = []
        self.received_count = 0

    def datagram(self, data, address, now):
        self.sock.sendto(data, address)
        self.sent_at = time.perf_counter_ns()

    def drain(self):
        while select.select([self.sock], [], [], 0)[0]:
            data, remote = self.sock.recvfrom(65535)
            packet = self.cipher.open(data)
            if remote != self.address or packet is None:
                raise ValueError("invalid loopback response")
            self.window.accept(packet.seq)
            self.received_count += 1
            self.syn |= bool(packet.flags & SYN)
            if packet.kind == KIND_FRAME and not self.welcomed:
                reader = BitReader(packet.payload)
                reader.var_a()
                reliable, _ = messages.read_channel(reader)
                self.welcomed = any(kind == 20300 for _, kind, _ in reliable)

    def drive(self, first_frame, age, angles):
        frame = max(first_frame + age, self.previous_frame + 1)
        body, payload = BitWriter(), BitWriter()
        body.bits(0, 24)  # latency and key flags
        body.bits(1, 8)  # one command
        body.bits(frame, 32)
        body.bit(1)
        body.bits(4, 3)  # arbitrary movement, unchanged right, new forward
        body.signed(32 if age % 120 < 60 else -32, 8)
        for angle in angles:
            body.signed(angle, 16)
        body.bits(int(age % 96 < 64), 24)  # pulse rifle trigger
        body.bits(0, 24)  # action and extra
        messages.write_channel(payload)
        payload.bit(1)
        payload.bits(body.count, 32)
        payload.append(body)
        seq = self.seq
        self.send(payload=payload.getvalue())
        self.offers.append((self.sent_at, seq))
        self.previous_frame = frame


def run_peers(control):
    control.send("ready")
    handoffs, port = control.recv()
    with ExitStack() as sockets:
        peers = []
        for handoff in handoffs:
            sock = sockets.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            sock.bind(("127.0.0.1", 0))
            sock.setblocking(False)
            peer = SocketPeer(handoff, ("127.0.0.1", port), sock)
            peers.append(peer)
            peer.send(flags=SYN)
        first_frame = epoch = angles = None
        due = time.monotonic()
        while True:
            if control.poll():
                command, value = control.recv()
                if command == "end":
                    control.send([offered_range(p.offers, *value) for p in peers])
                    return
                if command == "pick":
                    for peer, entity in zip(peers, value, strict=True):
                        peer.message(21616, pick(entity, SOLDIER))
                elif command == "drive":
                    first_frame, angles = value
                    epoch = time.monotonic()
                else:
                    raise ValueError("unknown benchmark control message")
            for peer in peers:
                peer.drain()
            now = time.monotonic()
            if now >= due:
                # A stalled generator skips its own expired slots. Count the actual sends.
                due += TICK_SECONDS
                if now - due >= TICK_SECONDS:
                    due = now + TICK_SECONDS
                for index, peer in enumerate(peers):
                    if not peer.syn:
                        peer.send(flags=SYN)
                    elif peer.welcomed and not peer.loaded:
                        peer.message(21601, {})
                        peer.loaded = True
                    elif epoch is not None:
                        peer.drive(first_frame, int((now - epoch) / TICK_SECONDS), angles[index])
                    else:
                        peer.send()
            time.sleep(min(0.002, max(0, due - time.monotonic())))
