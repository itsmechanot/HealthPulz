"""Who can log in: one shared account per BHU, one for the RHU, one for the load-test tool.

Only the RHU gives a BHU its password, on the dashboard's BHU Accounts screen. A BHU account has
none until then, so it can't log in. The password is shown once; the database holds only a salted
scrypt hash.

The RHU's own password and the load-test tool's are generated on first start and listed in
var/accounts.txt, which git ignores. BHU passwords are never written there.

Show those two:         python -m rhu.accounts
Change a password:      python -m rhu.accounts reset RHU
"""

import argparse
import base64
import hashlib
import hmac
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rhu import config, db, rules

RHU = "RHU"
BHU = "BHU"
LOADTEST = "LOADTEST"  # tools/simulate_nodes.py: may send reports for any barangay, and nothing else

SESSION_HOURS = 12
SCRYPT_N = 2**14  # memory and time cost; stored with each hash, so it can be raised later
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 8
NO_PASSWORD = ""  # the stored hash of a BHU account the RHU hasn't given a password yet

# No look-alikes (l/1, o/0), because passwords are copied from paper onto phones.
_PASSWORD_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"


@dataclass(frozen=True)
class Session:
    """The logged-in account. Every access rule of the system is one of these methods."""

    username: str
    role: str
    barangay_id: str | None = None

    @property
    def home(self) -> str:
        return {RHU: "/dashboard/", BHU: "/node/"}.get(self.role, "/")

    @property
    def is_rhu(self) -> bool:
        return self.role == RHU

    def may_open_records(self, barangay_id: str) -> bool:
        """Client records stay with their own BHU. The RHU gets de-identified reports only."""
        return self.role == BHU and self.barangay_id == barangay_id

    def may_send_for(self, barangay_id: str) -> bool:
        return self.role == LOADTEST or self.may_open_records(barangay_id)


# ---- passwords ------------------------------------------------------------------------

def new_password() -> str:
    letters = "".join(secrets.choice(_PASSWORD_LETTERS) for _ in range(8))
    return f"{letters[:4]}-{letters[4:]}"


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    encode = lambda raw: base64.b64encode(raw).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${encode(salt)}${encode(digest)}"


def password_matches(password: str, stored: str) -> bool:
    if stored == NO_PASSWORD:
        return False
    _, n, r, p, salt, digest = stored.split("$")
    actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    return hmac.compare_digest(actual, base64.b64decode(digest))


# ---- accounts -------------------------------------------------------------------------

def _all_accounts() -> list[tuple[str, str, str | None, str]]:
    """(username, role, barangay_id, name to show)"""
    return [
        (RHU, RHU, None, "RHU Naval"),
        *((b["barangay_id"], BHU, b["barangay_id"], b["name"]) for b in rules.barangays()),
        (LOADTEST, LOADTEST, None, "Load-test tool (tools/simulate_nodes.py)"),
    ]


def passwords_path(db_path: Path) -> Path:
    return db_path.with_name("accounts.txt")


def read_passwords(db_path: Path) -> dict[str, str]:
    path = passwords_path(db_path)
    if not path.exists():
        return {}
    rows = (line.split("\t") for line in path.read_text(encoding="utf-8").splitlines())
    return {row[0]: row[2] for row in rows if len(row) == 3}


def _save_passwords(db_path: Path, changed: dict[str, str]) -> None:
    """A password can't be read back from its hash, so other accounts' lines are kept as they are.
    BHU lines are left out, which also clears them from a list written by an older version."""
    passwords = {**read_passwords(db_path), **changed}
    lines = [
        f"{username}\t{name}\t{passwords[username]}"
        for username, role, _, name in _all_accounts()
        if username in passwords and role != BHU
    ]
    passwords_path(db_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_accounts(db_path: Path) -> None:
    """Create any account that doesn't exist yet. Existing passwords never change."""
    with closing(db.connect(db_path)) as conn, conn:
        existing = {row[0] for row in conn.execute("SELECT username FROM accounts")}
        created = {}
        for username, role, barangay_id, _ in _all_accounts():
            if username in existing:
                continue
            stored = NO_PASSWORD
            if role != BHU:
                created[username] = new_password()
                stored = hash_password(created[username])
            conn.execute(
                "INSERT INTO accounts (username, role, barangay_id, password_hash) VALUES (?, ?, ?, ?)",
                (username, role, barangay_id, stored),
            )
        # The first login version generated BHU passwords by itself. Those were never given by the
        # RHU (every given password has a date), so they stop working.
        conn.execute(
            "DELETE FROM sessions WHERE username IN "
            "(SELECT username FROM accounts WHERE role = ? AND password_set_at IS NULL)",
            (BHU,),
        )
        conn.execute(
            "UPDATE accounts SET password_hash = ? WHERE role = ? AND password_set_at IS NULL", (NO_PASSWORD, BHU)
        )
        # Inside the transaction: if the list can't be saved, the accounts aren't created either,
        # so no account ends up with a password nobody can read.
        _save_passwords(db_path, created)


def set_password(db_path: Path, username: str, password: str | None = None) -> str:
    """Give the account a password (a generated one if None) and log out every device using it."""
    password = password or new_password()
    with closing(db.connect(db_path)) as conn, conn:
        changed = conn.execute(
            "UPDATE accounts SET password_hash = ?, password_set_at = ? WHERE username = ?",
            (hash_password(password), db.utc_now(), username),
        )
        if changed.rowcount == 0:
            raise KeyError(username)
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        _save_passwords(db_path, {username: password})
    return password


def bhu_accounts(db_path: Path) -> list[dict]:
    """For the RHU's BHU Accounts screen. Says whether a password exists, never what it is."""
    names = {barangay_id: name for _, _, barangay_id, name in _all_accounts() if barangay_id}
    with closing(db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT barangay_id, password_hash, password_set_at FROM accounts WHERE role = ?", (BHU,)
        ).fetchall()
    accounts = [
        {
            "barangay_id": row["barangay_id"],
            "name": names[row["barangay_id"]],
            "assigned": row["password_hash"] != NO_PASSWORD,
            "password_set_at": row["password_set_at"],
        }
        for row in rows
    ]
    return sorted(accounts, key=lambda account: account["name"])


def assign_missing(db_path: Path) -> list[dict]:
    """Generate a password for every BHU without one. BHUs that have one keep it."""
    return [
        {
            "barangay_id": account["barangay_id"],
            "name": account["name"],
            "password": set_password(db_path, account["barangay_id"]),
        }
        for account in bhu_accounts(db_path)
        if not account["assigned"]
    ]


# ---- sessions -------------------------------------------------------------------------

def _session_key(token: str) -> str:
    """Only a hash of the token is stored, so reading the database doesn't give anyone a login."""
    return hashlib.sha256(token.encode()).hexdigest()


def log_in(conn: sqlite3.Connection, username: str, password: str) -> tuple[str, Session] | None:
    """Returns (token for the cookie, session), or None if the login is wrong."""
    row = conn.execute(
        "SELECT role, barangay_id, password_hash FROM accounts WHERE username = ?", (username,)
    ).fetchone()
    if row is None or not password_matches(password, row["password_hash"]):
        return None

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (db.utc_now(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, username, expires_at) VALUES (?, ?, ?)",
            (_session_key(token), username, expires_at.isoformat(timespec="milliseconds")),
        )
    return token, Session(username, row["role"], row["barangay_id"])


def session_for_token(conn: sqlite3.Connection, token: str) -> Session | None:
    row = conn.execute(
        """SELECT a.username, a.role, a.barangay_id
           FROM sessions s JOIN accounts a USING (username)
           WHERE s.token_hash = ? AND s.expires_at > ?""",
        (_session_key(token), db.utc_now()),
    ).fetchone()
    return Session(row["username"], row["role"], row["barangay_id"]) if row else None


def log_out(conn: sqlite3.Connection, token: str) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_session_key(token),))


# ---- command line ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Show or change the HealthPulz login passwords.")
    parser.add_argument("action", nargs="?", choices=["list", "reset"], default="list")
    parser.add_argument("username", nargs="?", help="for reset: RHU or LOADTEST (a BHU's password is given on the dashboard)")
    args = parser.parse_args()

    db.init_db(config.DB_PATH)
    ensure_accounts(config.DB_PATH)
    if args.action == "reset":
        if not args.username:
            parser.error("reset needs a username")
        try:
            print(f"New password for {args.username}: {set_password(config.DB_PATH, args.username)}")
        except KeyError:
            parser.error(f"no account named {args.username}")
        return
    print(passwords_path(config.DB_PATH).read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()
