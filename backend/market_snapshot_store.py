from __future__ import annotations

import json
import os
import tempfile
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_ROOT = ROOT / "backend" / "data" / "center_store"
DEFAULT_LATEST_FILENAME = "latest.parquet"
CANDLE_HISTORY_DIRNAME = "candle_history"
ARTIFACTS_DIRNAME = "artifacts"
JSON_SUFFIXES = {
    "day_range",
    "option_chain",
    "strategy",
    "pre_strategy",
    "contract",
    "summary",
    "risk",
    "market_context",
    "event_context",
    "data_source",
    "extra",
}

TIMEFRAME_DIRS = {
    "minute": "everyminute_center_daa",
    "1m": "everyminute_center_daa",
    "5m": "everyminute_center_daa",
    "15m": "15_min_center_data",
    "15_min": "15_min_center_data",
    "75m": "75_min_center_data",
    "75_min": "75_min_center_data",
    "3h": "3h_center_data",
    "4h": "4h_center_data",
    "weekly": "weekly_center_data",
    "monthly": "monthly_center_data",
    "commodities_daily": "commodities_daily",
    "dashboard": "dashboard_center_data",
    "daily": "dashboard_center_data",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_load(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            tmp_path = Path(handle.name)
        tmp_path.replace(path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _ensure_parquet_support() -> None:
    try:
        import importlib.util

        if importlib.util.find_spec("pyarrow") is None and importlib.util.find_spec("fastparquet") is None:
            raise RuntimeError(
                "Parquet support requires pyarrow or fastparquet. Install one of them to use the market snapshot store."
            )
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(
            "Unable to verify parquet support. Install pyarrow or fastparquet to use the market snapshot store."
        ) from exc


def _normalize_timeframe(timeframe: str) -> str:
    text = str(timeframe or "").strip().lower()
    if not text:
        return "dashboard"
    return TIMEFRAME_DIRS.get(text, text)


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".NS"):
        text = text[:-3]
    return text


def _to_json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, pd.Timestamp)):
        try:
            ts = pd.Timestamp(value)
            if pd.isna(ts):
                return None
            if ts.tzinfo is None:
                ts = ts.tz_localize(timezone.utc)
            return ts.tz_convert(timezone.utc).isoformat()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(key): _to_json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _is_nullish(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _stringify_nested_columns(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in list(work.columns):
        if column.endswith("_json"):
            continue
        if column in JSON_SUFFIXES and column in work.columns:
            work[f"{column}_json"] = work[column].apply(
                lambda value: json.dumps(_to_json_safe(value), ensure_ascii=False) if not _is_nullish(value) else ""
            )
            work.drop(columns=[column], inplace=True)
    for column in list(work.columns):
        if column.endswith("_json"):
            continue
        series = work[column]
        try:
            has_nested = any(isinstance(value, (dict, list, tuple, set)) for value in series.dropna().tolist())
        except Exception:
            has_nested = False
        if has_nested:
            work[f"{column}_json"] = work[column].apply(
                lambda value: json.dumps(_to_json_safe(value), ensure_ascii=False) if not _is_nullish(value) else ""
            )
            work.drop(columns=[column], inplace=True)
    for column in ("timestamp", "candle_time_ist", "generated_at", "stored_at"):
        if column in work.columns:
            work[column] = work[column].map(lambda value: _to_json_safe(value))
    return work


def _decode_json_column(value: Any) -> Any:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _clean_scalar(value: Any) -> Any:
    if _is_nullish(value):
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        if value.tzinfo is None:
            value = value.tz_localize(timezone.utc)
        return value.tz_convert(timezone.utc).isoformat()
    if isinstance(value, datetime):
        dt = pd.Timestamp(value)
        if dt.tzinfo is None:
            dt = dt.tz_localize(timezone.utc)
        return dt.tz_convert(timezone.utc).isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _clean_scalar(value.item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _frame_from_records(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    normalized: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = {str(key): _clean_scalar(value) for key, value in record.items()}
        for nested_key in list(JSON_SUFFIXES):
            nested_value = row.get(nested_key)
            if isinstance(nested_value, (dict, list)):
                row[f"{nested_key}_json"] = json.dumps(_to_json_safe(nested_value), ensure_ascii=False)
                row.pop(nested_key, None)
        normalized.append(row)
    return pd.DataFrame(normalized)


def _preferred_row_key(row: dict[str, Any]) -> tuple[str, str]:
    symbol = _normalize_symbol(row.get("symbol") or row.get("ticker") or row.get("name"))
    candle_time = str(row.get("candle_time_ist") or row.get("timestamp") or row.get("generated_at") or "")
    return symbol, candle_time


class MarketSnapshotStore:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or DEFAULT_STORE_ROOT

    def timeframe_dir(self, timeframe: str) -> Path:
        return self.base_dir / _normalize_timeframe(timeframe)

    def latest_path(self, timeframe: str) -> Path:
        slug = _normalize_timeframe(timeframe)
        return self.timeframe_dir(timeframe) / f"{slug}_{DEFAULT_LATEST_FILENAME}"

    def metadata_path(self, timeframe: str) -> Path:
        return self.latest_path(timeframe).with_suffix(".meta.json")

    def history_path(self, timeframe: str, timestamp: datetime | None = None) -> Path:
        ts = timestamp or _utc_now()
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        slug = _normalize_timeframe(timeframe)
        return self.timeframe_dir(timeframe) / "history" / ts.strftime("%Y%m%d") / f"{slug}_{ts.strftime('%Y%m%d_%H%M%S')}.parquet"

    def candle_history_path(self, timeframe: str, symbol: str, market: str, interval: str) -> Path:
        safe_symbol = _normalize_symbol(symbol)
        safe_market = str(market or "").strip().lower().replace("/", "_") or "india"
        safe_interval = str(interval or "").strip().lower().replace("/", "_") or "15m"
        return self.timeframe_dir(timeframe) / CANDLE_HISTORY_DIRNAME / safe_market / safe_interval / f"{safe_symbol}.parquet"

    def raw_candle_history_path(self, timeframe: str, symbol: str, market: str, interval: str) -> Path:
        return self.candle_history_path(timeframe, symbol, market, interval)

    def strategy_artifact_path(
        self,
        layer: str,
        strategy_id: str,
        symbol: str,
        market: str,
        interval: str,
    ) -> Path:
        safe_layer = str(layer or "").strip().lower().replace("/", "_") or "signals"
        safe_strategy = str(strategy_id or "").strip().lower().replace("/", "_") or "strategy"
        safe_symbol = _normalize_symbol(symbol)
        safe_market = str(market or "").strip().lower().replace("/", "_") or "india"
        safe_interval = str(interval or "").strip().lower().replace("/", "_") or "15m"
        return (
            self.base_dir
            / ARTIFACTS_DIRNAME
            / safe_layer
            / safe_strategy
            / safe_market
            / safe_interval
            / f"{safe_symbol}.parquet"
        )

    def signal_artifact_path(self, strategy_id: str, symbol: str, market: str, interval: str) -> Path:
        return self.strategy_artifact_path("signals", strategy_id, symbol, market, interval)

    def feature_artifact_path(self, strategy_id: str, symbol: str, market: str, interval: str) -> Path:
        return self.strategy_artifact_path("features", strategy_id, symbol, market, interval)

    def alert_artifact_path(self, strategy_id: str, symbol: str, market: str, interval: str) -> Path:
        return self.strategy_artifact_path("alert_events", strategy_id, symbol, market, interval)

    def alert_event_path(
        self,
        strategy_id: str,
        symbol: str,
        market: str,
        interval: str,
        signature: str,
    ) -> Path:
        safe_signature = hashlib.sha1(str(signature or "").encode("utf-8")).hexdigest()[:16]
        return self.alert_artifact_path(
            f"{strategy_id}_{safe_signature}",
            symbol,
            market,
            interval,
        )

    def _write_frame_to_parquet(self, frame: pd.DataFrame, path: Path) -> None:
        _ensure_parquet_support()
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False, engine="pyarrow")

    def _read_frame_from_parquet(self, path: Path) -> pd.DataFrame:
        _ensure_parquet_support()
        return pd.read_parquet(path, engine="pyarrow")

    def _normalize_candle_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        work = frame.copy()
        if "dt_utc" not in work.columns:
            if isinstance(work.index, pd.DatetimeIndex):
                work = work.reset_index()
                if "index" in work.columns and "dt_utc" not in work.columns:
                    work = work.rename(columns={"index": "dt_utc"})
            else:
                return pd.DataFrame()
        work["dt_utc"] = pd.to_datetime(work["dt_utc"], utc=True, errors="coerce")
        if "dt_ist" in work.columns:
            work["dt_ist"] = pd.to_datetime(work["dt_ist"], utc=True, errors="coerce")
        for column in ("open", "high", "low", "close", "volume", "oi"):
            if column in work.columns:
                work[column] = pd.to_numeric(work[column], errors="coerce")
        work = work.dropna(subset=["dt_utc"]).sort_values("dt_utc").drop_duplicates(subset=["dt_utc"], keep="last")
        return work.reset_index(drop=True)

    def _prepare_frame(self, payload: Any, *, timeframe: str, metadata: dict[str, Any] | None = None) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        payload_meta: dict[str, Any] | None = None
        if isinstance(payload, pd.DataFrame):
            records = payload.to_dict(orient="records")
        elif isinstance(payload, dict):
            if isinstance(payload.get("snapshots"), list):
                records = [row for row in payload.get("snapshots") if isinstance(row, dict)]
            elif isinstance(payload.get("data"), dict):
                for symbol, row in payload.get("data", {}).items():
                    if not isinstance(row, dict):
                        continue
                    record = dict(row)
                    record.setdefault("symbol", symbol)
                    record.setdefault("ticker", symbol)
                    records.append(record)
            elif isinstance(payload.get("items"), list):
                records = [row for row in payload.get("items") if isinstance(row, dict)]
            else:
                records = [payload]
            payload_meta = {
                str(key): _to_json_safe(value)
                for key, value in payload.items()
                if key not in {"snapshots", "data", "items", "rows"}
            }
        elif isinstance(payload, list):
            records = [row for row in payload if isinstance(row, dict)]
        else:
            records = []

        frame = _frame_from_records(records)
        if frame.empty:
            return frame

        frame["store_timeframe"] = _normalize_timeframe(timeframe)
        frame["stored_at"] = _utc_now().isoformat()
        if metadata:
            for key, value in metadata.items():
                if key not in frame.columns:
                    frame[key] = _clean_scalar(value)
        if payload_meta:
            frame["payload_meta_json"] = json.dumps(payload_meta, ensure_ascii=False)
        frame = _stringify_nested_columns(frame)
        for column in ("symbol", "ticker", "name"):
            if column in frame.columns:
                frame[column] = frame[column].map(_normalize_symbol)
        return frame

    def write_payload(
        self,
        payload: Any,
        *,
        timeframe: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        frame = self._prepare_frame(payload, timeframe=timeframe, metadata=metadata)
        if frame.empty:
            return None

        dedupe_keys = [key for key in ("symbol", "interval", "candle_time_ist", "timestamp", "generated_at") if key in frame.columns]
        if dedupe_keys:
            frame = frame.sort_values(by=dedupe_keys).drop_duplicates(subset=dedupe_keys, keep="last")
        else:
            frame = frame.drop_duplicates(keep="last")

        latest_path = self.latest_path(timeframe)
        history_path = self.history_path(timeframe)
        metadata_payload = {
            "timeframe": _normalize_timeframe(timeframe),
            "row_count": int(len(frame)),
            "written_at": _utc_now().isoformat(),
            "latest_path": str(latest_path),
            "history_path": str(history_path),
        }
        if "payload_meta_json" in frame.columns:
            try:
                payload_meta_series = frame["payload_meta_json"]
                if not payload_meta_series.empty:
                    metadata_payload["payload_meta_json"] = str(payload_meta_series.iloc[0])
            except Exception:
                pass
        if metadata:
            metadata_payload["extra"] = metadata

        self._write_frame_to_parquet(frame, latest_path)
        _json_write_atomic(self.metadata_path(timeframe), metadata_payload)
        self._write_frame_to_parquet(frame, history_path)
        return latest_path

    def write_artifact_payload(
        self,
        payload: Any,
        path: Path,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        frame = self._prepare_frame(payload, timeframe="artifact", metadata=metadata)
        if frame.empty:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_frame_to_parquet(frame, path)
        return path

    def read_candle_history(self, timeframe: str, symbol: str, market: str, interval: str) -> pd.DataFrame | None:
        path = self.candle_history_path(timeframe, symbol, market, interval)
        if not path.exists():
            return None
        try:
            frame = self._read_frame_from_parquet(path)
        except Exception:
            return None
        frame = self._normalize_candle_frame(frame)
        if frame.empty:
            return None
        return frame.set_index("dt_utc")

    def write_candle_history(
        self,
        timeframe: str,
        symbol: str,
        market: str,
        interval: str,
        frame: pd.DataFrame,
        *,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> Path | None:
        work = self._normalize_candle_frame(frame)
        if work.empty:
            return None
        if retention_days is not None and retention_days > 0:
            cutoff = (now or _utc_now()) - timedelta(days=retention_days)
            cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
            work = work.loc[work["dt_utc"] >= pd.Timestamp(cutoff)]
        if work.empty:
            return None

        path = self.candle_history_path(timeframe, symbol, market, interval)
        existing = self.read_candle_history(timeframe, symbol, market, interval)
        if existing is not None and not existing.empty:
            existing = existing.reset_index()
            work = pd.concat([existing, work], axis=0, ignore_index=True)
            work = self._normalize_candle_frame(work)
            if retention_days is not None and retention_days > 0:
                cutoff = (now or _utc_now()) - timedelta(days=retention_days)
                cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
                work = work.loc[work["dt_utc"] >= pd.Timestamp(cutoff)]
        if work.empty:
            return None
        self._write_frame_to_parquet(work, path)
        return path

    def cleanup_timeframe_history(
        self,
        timeframe: str,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> dict[str, int]:
        stats = {"snapshot_dirs_removed": 0, "snapshot_files_removed": 0, "candle_files_trimmed": 0, "candle_files_removed": 0}
        if retention_days <= 0:
            return stats

        effective_now = now or _utc_now()
        cutoff = effective_now - timedelta(days=retention_days)
        cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)
        timeframe_root = self.timeframe_dir(timeframe)

        snapshot_history_root = timeframe_root / "history"
        if snapshot_history_root.exists():
            for dated_dir in sorted(snapshot_history_root.glob("*")):
                if not dated_dir.is_dir():
                    continue
                if len(dated_dir.name) == 8 and dated_dir.name.isdigit():
                    try:
                        dir_date = datetime.strptime(dated_dir.name, "%Y%m%d").date()
                    except Exception:
                        dir_date = None
                    if dir_date is not None and dir_date < cutoff.date():
                        for child in dated_dir.glob("*.parquet"):
                            try:
                                child.unlink()
                                stats["snapshot_files_removed"] += 1
                            except Exception:
                                pass
                        try:
                            dated_dir.rmdir()
                            stats["snapshot_dirs_removed"] += 1
                        except Exception:
                            pass

        candle_root = timeframe_root / CANDLE_HISTORY_DIRNAME
        if candle_root.exists():
            for candle_file in candle_root.rglob("*.parquet"):
                try:
                    frame = self._read_frame_from_parquet(candle_file)
                except Exception:
                    continue
                frame = self._normalize_candle_frame(frame)
                if frame.empty:
                    try:
                        candle_file.unlink()
                        stats["candle_files_removed"] += 1
                    except Exception:
                        pass
                    continue
                trimmed = frame.loc[frame["dt_utc"] >= pd.Timestamp(cutoff)]
                if trimmed.empty:
                    try:
                        candle_file.unlink()
                        stats["candle_files_removed"] += 1
                    except Exception:
                        pass
                    continue
                if len(trimmed) < len(frame):
                    self._write_frame_to_parquet(trimmed.reset_index(drop=True), candle_file)
                    stats["candle_files_trimmed"] += 1

        return stats

    def cleanup_all_timeframes(
        self,
        *,
        retention_days_by_timeframe: dict[str, int],
        now: datetime | None = None,
    ) -> dict[str, dict[str, int]]:
        results: dict[str, dict[str, int]] = {}
        for timeframe, retention_days in retention_days_by_timeframe.items():
            try:
                results[timeframe] = self.cleanup_timeframe_history(timeframe, retention_days=retention_days, now=now)
            except Exception:
                results[timeframe] = {"snapshot_dirs_removed": 0, "snapshot_files_removed": 0, "candle_files_trimmed": 0, "candle_files_removed": 0}
        return results

    def read_payload(self, timeframe: str) -> dict[str, Any] | None:
        latest_path = self.latest_path(timeframe)
        if not latest_path.exists():
            return None
        try:
            frame = self._read_frame_from_parquet(latest_path)
        except Exception:
            return None
        if frame is None or frame.empty:
            return None
        return self.frame_to_payload(frame, timeframe=timeframe)

    def frame_to_payload(self, frame: pd.DataFrame, *, timeframe: str) -> dict[str, Any]:
        if frame.empty:
            return {}

        work = frame.copy()
        for column in work.columns:
            if column.endswith("_json"):
                base = column[:-5]
                work[base] = work[column].map(_decode_json_column)

        if "symbol" in work.columns:
            work["symbol"] = work["symbol"].map(_normalize_symbol)
        if "ticker" in work.columns:
            work["ticker"] = work["ticker"].map(_normalize_symbol)
        if "name" in work.columns:
            work["name"] = work["name"].map(_normalize_symbol)

        rows = work.to_dict(orient="records")
        rows.sort(key=lambda row: _preferred_row_key(row))
        data: dict[str, dict[str, Any]] = {}
        latest_dt: datetime | None = None
        for row in rows:
            symbol = _normalize_symbol(row.get("symbol") or row.get("ticker") or row.get("name"))
            if not symbol:
                continue
            record = {key: _clean_scalar(value) for key, value in row.items() if not key.endswith("_json")}
            for column in list(row.keys()):
                if column.endswith("_json") and row.get(column) not in (None, "", "null"):
                    record[column[:-5]] = _decode_json_column(row.get(column))
            record["symbol"] = symbol
            record.setdefault("ticker", symbol)
            record.setdefault("name", symbol)
            record.setdefault("current_price", record.get("close"))
            record.setdefault("price", record.get("close"))
            timestamp = record.get("candle_time_ist") or record.get("timestamp") or record.get("generated_at") or record.get("stored_at")
            if timestamp:
                record["generated_at"] = record.get("generated_at") or timestamp
                try:
                    parsed = pd.Timestamp(timestamp)
                    if not pd.isna(parsed):
                        if parsed.tzinfo is None:
                            parsed = parsed.tz_localize(timezone.utc)
                        latest_dt = max(latest_dt, parsed.to_pydatetime()) if latest_dt else parsed.to_pydatetime()
                except Exception:
                    pass
            existing = data.get(symbol)
            if existing is None:
                data[symbol] = record
                continue
            existing_key = str(existing.get("candle_time_ist") or existing.get("timestamp") or existing.get("generated_at") or "")
            record_key = str(record.get("candle_time_ist") or record.get("timestamp") or record.get("generated_at") or "")
            if record_key >= existing_key:
                data[symbol] = record

        generated_at = None
        published_at = None
        meta = _json_load(self.metadata_path(timeframe)) or {}
        if isinstance(meta, dict):
            published_at = meta.get("written_at") or meta.get("generated_at")
            generated_at = published_at
        if generated_at is None and latest_dt is not None:
            generated_at = latest_dt.astimezone(timezone.utc).isoformat()
        if published_at is None:
            published_at = generated_at
        if generated_at is None:
            generated_at = _utc_now().isoformat()
        if published_at is None:
            published_at = generated_at

        payload_meta = {}
        if isinstance(meta, dict) and meta.get("payload_meta_json") is not None:
            decoded_meta = _decode_json_column(meta.get("payload_meta_json"))
            payload_meta = decoded_meta if isinstance(decoded_meta, dict) else {}

        payload = {
            "generated_at": generated_at,
            "published_at": published_at,
            "source": {
                "store_root": str(self.base_dir),
                "timeframe": _normalize_timeframe(timeframe),
                "latest_path": str(self.latest_path(timeframe)),
                "metadata_path": str(self.metadata_path(timeframe)),
            },
            "data": data,
            "snapshots": list(data.values()),
            "store": {
                "timeframe": _normalize_timeframe(timeframe),
                "row_count": len(data),
            },
            **payload_meta,
        }
        if isinstance(meta, dict):
            payload["store_generated_at"] = meta.get("generated_at")
            payload["store_written_at"] = meta.get("written_at")
            if meta.get("written_at") and not payload.get("published_at"):
                payload["published_at"] = meta.get("written_at")
            if meta.get("written_at") and not payload.get("generated_at"):
                payload["generated_at"] = meta.get("written_at")
        return payload


def load_latest_market_snapshot_payload(
    timeframe_preference: Iterable[str] = ("15m", "dashboard", "minute"),
    base_dir: Path | None = None,
) -> dict[str, Any] | None:
    store = MarketSnapshotStore(base_dir=base_dir)
    for timeframe in timeframe_preference:
        payload = store.read_payload(timeframe)
        if isinstance(payload, dict) and payload.get("data"):
            return payload
    return None
