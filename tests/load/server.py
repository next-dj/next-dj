import socket
from multiprocessing.queues import Queue

import uvicorn

from tests.load.boot import boot


BACKLOG = 4096


def serve(ports: Queue[int]) -> None:
    """Load the harness app, then serve it under uvicorn on a free loopback port.

    The port is sent once the app is loaded and listening, so no hello times the boot.
    """
    boot()
    config = uvicorn.Config(
        "tests.load.asgi:application",
        lifespan="off",
        log_level="warning",
        access_log=False,
        ws="websockets-sansio",
        http="h11",
        backlog=BACKLOG,
        timeout_graceful_shutdown=5,
    )
    config.load()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(BACKLOG)
    ports.put(sock.getsockname()[1])
    uvicorn.Server(config).run(sockets=[sock])
