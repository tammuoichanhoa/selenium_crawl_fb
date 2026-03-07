from __future__ import annotations

import queue
import random
import socket


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def build_port_queue(
    port_min: int,
    port_max: int,
    desired_count: int,
    seed: int | None = None,
) -> queue.Queue[int]:
    if port_min > port_max:
        raise ValueError("port_min must be <= port_max")
    if desired_count <= 0:
        raise ValueError("desired_count must be > 0")

    candidates = list(range(port_min, port_max + 1))
    rnd = random.Random(seed)
    rnd.shuffle(candidates)

    port_queue: queue.Queue[int] = queue.Queue()
    for port in candidates:
        if is_port_free(port):
            port_queue.put(port)
            if port_queue.qsize() >= desired_count:
                break

    if port_queue.qsize() < desired_count:
        raise RuntimeError(
            f"Not enough free ports in {port_min}-{port_max}. "
            f"Needed {desired_count}, got {port_queue.qsize()}."
        )
    return port_queue
