from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / "backend" / ".env"
RENEW_URL = "https://api.dhan.co/v2/RenewToken"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _resolve_env_value(env: dict[str, str], key: str, *aliases: str) -> str | None:
    for candidate in (key, *aliases):
        value = str(env.get(candidate) or os.getenv(candidate) or "").strip()
        if value:
            return value
    return None


def _write_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        line = raw
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _mask(token: str | None) -> str:
    if not token:
        return "missing"
    token = str(token)
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def renew_access_token(current_token: str, client_id: str | None = None) -> dict[str, Any]:
    headers = {"access-token": current_token}
    if client_id:
        headers["dhanClientId"] = client_id
    resp = requests.get(RENEW_URL, headers=headers, timeout=20)
    if not resp.ok:
        body = resp.text.strip()
        raise RuntimeError(f"Dhan RenewToken failed: HTTP {resp.status_code} {body[:500]}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Dhan RenewToken response shape")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Renew Dhan access token and overwrite backend/.env")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Path to backend .env file")
    parser.add_argument("--current-token", default="", help="Current active Dhan access token")
    parser.add_argument("--client-id", default="", help="Dhan client id")
    parser.add_argument("--dry-run", action="store_true", help="Print the new token but do not write files")
    args = parser.parse_args()

    env_path = args.env_file
    env_values = _load_env_file(env_path)

    current_token = str(args.current_token or "").strip() or _resolve_env_value(env_values, "DHAN_ACCESS_TOKEN", "DHANTOPTTOKEN")
    if not current_token:
        raise SystemExit("Missing DHAN_ACCESS_TOKEN (or DHANTOPTTOKEN) in env file")

    client_id = str(args.client_id or "").strip() or _resolve_env_value(env_values, "DHAN_CLIENT_ID", "DHAN_ID")

    print(f"[DHAN] renewing token for client={client_id} current={_mask(current_token)}")
    payload = renew_access_token(current_token=current_token, client_id=client_id)

    new_token = (
        payload.get("accessToken")
        or payload.get("access_token")
        or payload.get("access-token")
        or ""
    )
    if not new_token:
        raise SystemExit(f"RenewToken response did not include accessToken: {json.dumps(payload, ensure_ascii=False)[:400]}")

    new_token = str(new_token).strip()
    if not new_token:
        raise SystemExit("RenewToken returned an empty access token")

    print(f"[DHAN] new token received: {_mask(new_token)}")
    if args.dry_run:
        return 0

    _write_env_file(env_path, {"DHAN_ACCESS_TOKEN": new_token, "DHAN_CLIENT_ID": client_id})
    print(f"[DHAN] env overwritten: {env_path}")
    subprocess.run(["systemctl", "restart", "market-context-live"], check=True)
    print("[DHAN] market-context-live restarted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
