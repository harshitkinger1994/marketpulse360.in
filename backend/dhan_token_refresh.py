from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pyotp
import requests

try:
    from dhanhq import DhanLogin
except Exception as exc:  # pragma: no cover - handled at runtime
    DhanLogin = None  # type: ignore[assignment]
    DHANH_Q_IMPORT_ERROR = exc
else:
    DHANH_Q_IMPORT_ERROR = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / "backend" / ".env"


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
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(raw)
            continue
        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(raw)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _mask(value: str | None) -> str:
    if not value:
        return "missing"
    value = str(value)
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _extract_access_token(payload: Any) -> str | None:
    if isinstance(payload, str):
        token = payload.strip()
        return token or None
    if isinstance(payload, dict):
        for key in ("access_token", "accessToken", "access-token", "token"):
            token = str(payload.get(key) or "").strip()
            if token:
                return token
    return None


def _telegram_config(env_values: dict[str, str]) -> tuple[str | None, str | None]:
    token = _resolve_env_value(env_values, "TELEGRAM_BOT_TOKEN")
    chat_id = _resolve_env_value(
        env_values,
        "TELEGRAM_PERSONAL_CHAT_ID",
        "TELEGRAM_STATUS_CHAT_ID",
        "TELEGRAM_CHAT_ID",
    )
    return token, chat_id


def _notify_failure(env_values: dict[str, str], message: str) -> None:
    token, chat_id = _telegram_config(env_values)
    if not token or not chat_id:
        print(f"[DHAN] failure notification skipped; Telegram not configured: {message}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=20,
        ).raise_for_status()
        print("[DHAN] failure alert sent to Telegram")
    except Exception as exc:  # pragma: no cover - best effort only
        print(f"[DHAN] failed to send Telegram alert: {exc}")


def generate_fresh_token(client_id: str, pin: str, totp_secret: str) -> dict[str, Any]:
    if DhanLogin is None:
        raise RuntimeError(
            "dhanhq is not installed. Install the DhanHQ SDK before running the token refresh script."
        ) from DHANH_Q_IMPORT_ERROR
    current_totp = pyotp.TOTP(totp_secret).now()
    dhan_login = DhanLogin(client_id)
    return dhan_login.generate_token(pin, current_totp)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a fresh Dhan access token from PIN + TOTP and overwrite backend/.env")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Path to backend .env file")
    parser.add_argument("--dry-run", action="store_true", help="Print the new token but do not write files")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart market-context-live after writing the token")
    args = parser.parse_args()

    env_path = args.env_file
    env_values = _load_env_file(env_path)

    client_id = _resolve_env_value(env_values, "DHAN_ID", "DHAN_CLIENT_ID")
    if not client_id:
        raise RuntimeError("Missing DHAN_ID (or DHAN_CLIENT_ID) in env file")

    pin = _resolve_env_value(env_values, "DHAN_PIN")
    if not pin:
        raise RuntimeError("Missing DHAN_PIN in env file")

    totp_secret = _resolve_env_value(env_values, "DHAN_TOTP_SECRET", "TOTP_KEY", "DHANTOPTTOKEN")
    if not totp_secret:
        raise RuntimeError("Missing DHAN_TOTP_SECRET (or TOTP_KEY / DHANTOPTTOKEN) in env file")

    print(f"[DHAN] generating fresh token for client={client_id} pin={_mask(pin)} totp={_mask(totp_secret)}")
    try:
        payload = generate_fresh_token(client_id=client_id, pin=pin, totp_secret=totp_secret)
        print(f"[DHAN] raw auth response type: {type(payload).__name__}")

        new_token = _extract_access_token(payload)
        if not new_token:
            raise RuntimeError(
                f"Auth response did not include access token: {json.dumps(payload, ensure_ascii=False)[:500]}"
            )

        print(f"[DHAN] new token received: {_mask(new_token)}")
        if args.dry_run:
            return 0

        _write_env_file(env_path, {"DHAN_ACCESS_TOKEN": new_token, "DHAN_CLIENT_ID": client_id})
        print(f"[DHAN] env overwritten: {env_path}")

        if not args.no_restart:
            subprocess.run(["systemctl", "restart", "market-context-live"], check=True)
            print("[DHAN] market-context-live restarted")
        return 0
    except Exception as exc:
        error_message = f"[DHAN] token refresh failed for client={client_id}: {exc}"
        print(error_message)
        if not args.dry_run:
            _notify_failure(
                env_values,
                "\n".join(
                    [
                        "Dhan token refresh failed",
                        f"Client: {client_id}",
                        f"Error: {exc}",
                    ]
                ),
            )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
