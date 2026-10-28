"""Simulates many BHS nodes sending case batches to the RHU at the same time.

Every node runs concurrently, sends its batches over real HTTP, retries on 503
(backpressure), then the script waits until every batch is STORED and checks
that no record was lost.

Usage (with the RHU server running):
    python tools/simulate_nodes.py --nodes 40 --batches 5 --records 100
"""

import argparse
import asyncio
import random
import string
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx

DISEASES = ["DEN", "ILI", "AWD", "LEP", "TYP", "MEA"]


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
    }
    while True:
        res = await client.post("/api/batches", json=batch)
        if res.status_code == 503:
            stats["busy_retries"] += 1
            await asyncio.sleep(float(res.headers.get("Retry-After", 1)))
            continue
        res.raise_for_status()
        return batch["batch_id"]


async def run_node(client: httpx.AsyncClient, barangay_id: str, args, stats: dict) -> list[str]:
    node_id = random_node_id(barangay_id)
    res = await client.post(
        "/api/nodes/register", json={"type": "REGISTER", "node_id": node_id, "barangay_id": barangay_id}
    )
    res.raise_for_status()
    return [await send_batch(client, node_id, args.records, stats) for _ in range(args.batches)]


async def wait_until_stored(client: httpx.AsyncClient, batch_ids: list[str]) -> int:
    """Returns the total number of accepted records across all batches."""
    accepted = 0
    for batch_id in batch_ids:
        while (body := (await client.get(f"/api/batches/{batch_id}")).json())["status"] != "STORED":
            await asyncio.sleep(0.05)
        accepted += body["accepted"]
    return accepted


async def main(args) -> None:
    limits = httpx.Limits(max_connections=args.nodes)
    async with httpx.AsyncClient(base_url=args.url, timeout=30, limits=limits) as client:
        barangays = [b["barangay_id"] for b in (await client.get("/api/barangays")).json()]
        node_barangays = [barangays[i % len(barangays)] for i in range(args.nodes)]
        stats = {"busy_retries": 0}

        start = time.perf_counter()
        per_node = await asyncio.gather(*(run_node(client, b, args, stats) for b in node_barangays))
        received_at = time.perf_counter()
        batch_ids = [batch_id for ids in per_node for batch_id in ids]
        accepted = await wait_until_stored(client, batch_ids)
        stored_at = time.perf_counter()

    expected = args.nodes * args.batches * args.records
    print(f"Nodes: {args.nodes}   Batches: {len(batch_ids)}   Records sent: {expected:,}")
    print(f"All RECEIVED in:  {received_at - start:6.2f} s")
    print(f"All STORED in:    {stored_at - start:6.2f} s   ({expected / (stored_at - start):,.0f} records/s)")
    print(f"503 retries:      {stats['busy_retries']}")
    print(f"Records accepted: {accepted:,} / {expected:,}  ->  {'OK, nothing lost' if accepted == expected else 'MISMATCH'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--batches", type=int, default=5, help="batches per node")
    parser.add_argument("--records", type=int, default=100, help="records per batch (max 100)")
    asyncio.run(main(parser.parse_args()))
