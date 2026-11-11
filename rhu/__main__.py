"""Start the RHU server so that phones on the same Wi-Fi can reach it:  python -m rhu"""

import argparse

import uvicorn

from rhu.network import lan_addresses

DEFAULT_PORT = 8001  # 8000 is often taken by other projects on this laptop
ALL_NETWORKS = "0.0.0.0"  # not 127.0.0.1, which only accepts connections from this laptop


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the HealthPulz RHU server.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="restart when the code changes (for development)")
    args = parser.parse_args()

    print(f"\n  This laptop:   http://localhost:{args.port}/")
    for address in lan_addresses():
        print(f"  Phones:        http://{address}:{args.port}/")
    print(flush=True)  # before uvicorn takes over, even when the output goes to a file
    uvicorn.run("rhu.main:app", host=ALL_NETWORKS, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
