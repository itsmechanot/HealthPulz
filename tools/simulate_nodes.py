"""Simulates many BHU nodes sending batches at once, then checks that no record was lost.

Logs in as LOADTEST (password from var/accounts.txt, or --password).
Usage:  python tools/simulate_nodes.py --nodes 40 --batches 5 --records 100
"""

import argparse
import asyncio
import json
import random
import string
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rhu import accounts, config
from rhu.auth import COOKIE

DISEASES = ["ARI", "SKN", "PNE", "WND", "HTN", "TB", "FEV", "DIA", "DEN", "TYP"]
STORED_TIMEOUT_SECONDS = 30


def random_record() -> dict:
    onset = date.today() - timedelta(days=random.randint(1, 6))
    return {
        "record_id": str(uuid.uuid4()),
        "disease_code": random.choice(DISEASES),
        "onset_date": onset.isoformat(),
        "consult_date": (onset + timedelta(days=random.randint(0, 1))).isoformat(),
        "age_years": random.randint(0, 90),
        "sex": random.choice("MF"),
        "purok": random.randint(1, 7),
        "case_status": random.choice(["SUSPECTED", "CONFIRMED"]),
    }


def random_node_id(barangay_id: str) -> str:
    return f"{barangay_id}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"


async def send_batch(client: httpx.AsyncClient, node_id: str, records: int, stats: dict) -> str:
    batch = {
        "type": "SUBMIT_BATCH",
        "protocol_version": 1,
        "node_id": node_id,
        "batch_id": str(uuid.uuid4()),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "records": [random_record() for _ in range(records)],
        "drill": True,
    }
    while True:
        res = await client.post("/api/batches", json=batch)
        if res.status_code == 503:
            stats["busy_retries"] += 1
            await asyncio.sleep(float(res.headers.get("Retry-After", 1)))
            continue
        res.raise_for_status()
        return batch["batch_id"]


async def run_node(client: httpx.AsyncClient, barangay_id: str, args, stats: dict) -> int:
    """Returns the number of records the RHU accepted from this node."""
    node_id = random_node_id(barangay_id)
    res = await client.post(
        "/api/nodes/register", json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id}
    )
    res.raise_for_status()

    ws_url = args.url.replace("http", "ws", 1) + f"/ws/node/{node_id}"
    async with websockets.connect(ws_url, additional_headers={"Cookie": f"{COOKIE}={args.token}"}) as ws:
        await ws.send(json.dumps({"type": "HELLO", "node_id": node_id, "pending_batches": 0}))
        json.loads(await ws.recv())

        waiting = {await send_batch(client, node_id, args.records, stats) for _ in range(args.batches)}
        stats["last_received"] = time.perf_counter()

        accepted = 0
        while waiting:
            message = json.loads(await asyncio.wait_for(ws.recv(), STORED_TIMEOUT_SECONDS))
            if message["type"] == "STORED" and message["batch_id"] in waiting:
                waiting.discard(message["batch_id"])
                accepted += message["accepted"]
        return accepted


async def log_in(client: httpx.AsyncClient, args) -> str:
    password = args.password or accounts.read_passwords(config.DB_PATH).get(accounts.LOADTEST)
    if not password:
        raise SystemExit(f"No LOADTEST password in {accounts.passwords_path(config.DB_PATH)}. Use --password.")
    res = await client.post("/api/auth/login", json={"username": accounts.LOADTEST, "password": password})
    if res.status_code != 200:
        raise SystemExit(f"Login failed (HTTP {res.status_code}). Check the LOADTEST password.")
    return res.cookies[COOKIE]


async def main(args) -> None:
    limits = httpx.Limits(max_connections=args.nodes)
    async with httpx.AsyncClient(base_url=args.url, timeout=30, limits=limits) as client:
        args.token = await log_in(client, args)
        barangays = [b["barangay_id"] for b in (await client.get("/api/barangays")).json()]
        node_barangays = [barangays[i % len(barangays)] for i in range(args.nodes)]
        stats = {"busy_retries": 0}

        start = time.perf_counter()
        accepted = sum(await asyncio.gather(*(run_node(client, b, args, stats) for b in node_barangays)))
        stored_at = time.perf_counter()

    expected = args.nodes * args.batches * args.records
    print(f"Nodes: {args.nodes}   Batches: {args.nodes * args.batches}   Records sent: {expected:,}")
    print(f"All RECEIVED in:  {stats['last_received'] - start:6.2f} s")
    print(f"All STORED in:    {stored_at - start:6.2f} s   ({expected / (stored_at - start):,.0f} records/s, confirmed over WebSocket)")
    print(f"503 retries:      {stats['busy_retries']}")
    print(f"Records accepted: {accepted:,} / {expected:,}  ->  {'OK, nothing lost' if accepted == expected else 'MISMATCH'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--password", help="LOADTEST password (default: read from var/accounts.txt)")
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--batches", type=int, default=5, help="batches per node")
    parser.add_argument("--records", type=int, default=100, help="records per batch (max 100)")
    asyncio.run(main(parser.parse_args()))
