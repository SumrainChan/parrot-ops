"""P0-1: CLI entry point and process lifecycle.

Usage:
    parrot-agent --port 9090               # foreground (dev)
    sudo systemctl start parrot-agent       # background (production)
"""

import argparse
import signal
import sys

from .server import AgentServer


def main():
    parser = argparse.ArgumentParser(
        description="Parrot Agent — reliable remote execution engine",
        prog="parrot-agent",
    )
    parser.add_argument("--port", "-p", type=int, default=9090,
                        help="HTTP listen port (default: 9090)")
    parser.add_argument("--bind", "-b", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--data-dir", default=None,
                        help="Data directory (default: ~/.parrot)")
    args = parser.parse_args()

    server = AgentServer(bind=args.bind, port=args.port, data_dir=args.data_dir)

    # Graceful shutdown on SIGTERM (systemd) and SIGINT (Ctrl+C)
    def _shutdown(signum, frame):
        print("\n[parrot-agent] shutting down...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Periodic audit cleanup
    import threading
    import time

    def _cleanup_loop():
        while True:
            time.sleep(3600)  # every hour
            server.persistence.cleanup_old_audit()

    threading.Thread(target=_cleanup_loop, daemon=True).start()

    server.start()


if __name__ == "__main__":
    main()
