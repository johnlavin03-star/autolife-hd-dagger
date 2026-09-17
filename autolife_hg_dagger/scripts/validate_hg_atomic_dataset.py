#!/usr/bin/env python3
"""Offline integrity audit for an HG-DAGGER atomic episode collection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_DAGGER_COLUMNS = {
    "metadata.control_mode",
    "metadata.authority_epoch",
    "metadata.train_mask",
    "metadata.pre_failure",
    "metadata.failure_boundary",
    "metadata.anchor_timestamp_ns",
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_index(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"index line {number} is not an object")
        rows.append(value)
    return rows


def video_frame_count(path: Path) -> int:
    import av

    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        count = int(stream.frames or 0)
        if count <= 0:
            count = sum(1 for _ in container.decode(stream))
        return count
    finally:
        container.close()


def audit_episode(root: Path, row: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    import pyarrow.parquet as pq

    errors: list[str] = []
    episode_id = str(row.get("episode_id", ""))
    relative = row.get("path")
    episode = root / str(relative) if relative else root / "episodes" / episode_id
    expected = int(row.get("frames", -1))
    details: dict[str, Any] = {"episode_id": episode_id, "path": str(episode)}
    if not episode.is_dir():
        return [f"{episode_id}: committed directory is missing: {episode}"], details
    try:
        manifest = load_json(episode / "episode_manifest.json")
    except Exception as exc:
        return [f"{episode_id}: invalid episode manifest: {exc}"], details
    if manifest.get("status") != "saved":
        errors.append(f"{episode_id}: manifest status is not saved")
    if str(manifest.get("episode_id")) != episode_id:
        errors.append(f"{episode_id}: manifest episode_id mismatch")
    if int(manifest.get("frames", -1)) != expected:
        errors.append(f"{episode_id}: manifest/index frame count mismatch")

    parquet_files = sorted((episode / "data").rglob("*.parquet"))
    parquet_rows = 0
    columns: set[str] = set()
    for parquet in parquet_files:
        try:
            metadata = pq.ParquetFile(parquet).metadata
            parquet_rows += metadata.num_rows
            columns.update(pq.read_schema(parquet).names)
        except Exception as exc:
            errors.append(f"{episode_id}: unreadable Parquet {parquet.name}: {exc}")
    if not parquet_files:
        errors.append(f"{episode_id}: no Parquet files")
    if parquet_rows != expected:
        errors.append(
            f"{episode_id}: Parquet rows={parquet_rows}, expected={expected}"
        )
    missing_columns = sorted(REQUIRED_DAGGER_COLUMNS - columns)
    if missing_columns:
        errors.append(
            f"{episode_id}: missing HG-DAGGER metadata columns: {', '.join(missing_columns)}"
        )

    info_path = episode / "meta" / "info.json"
    try:
        info = load_json(info_path)
        if int(info.get("total_episodes", -1)) != 1:
            errors.append(f"{episode_id}: total_episodes is not 1")
        if int(info.get("total_frames", -1)) != expected:
            errors.append(f"{episode_id}: info.json frame count mismatch")
        camera_keys = sorted(
            key.removeprefix("observation.images.")
            for key in info.get("features", {})
            if key.startswith("observation.images.")
        )
    except Exception as exc:
        errors.append(f"{episode_id}: invalid info.json: {exc}")
        camera_keys = []

    video_counts: dict[str, int] = {}
    for camera in camera_keys:
        feature_dir = f"observation.images.{camera}"
        videos = sorted(
            path for path in (episode / "videos").rglob("*.mp4")
            if feature_dir in path.parts
        )
        if not videos:
            errors.append(f"{episode_id}: no video for {camera}")
            continue
        try:
            count = sum(video_frame_count(video) for video in videos)
        except Exception as exc:
            errors.append(f"{episode_id}: unreadable video for {camera}: {exc}")
            continue
        video_counts[camera] = count
        if count != expected:
            errors.append(
                f"{episode_id}: {camera} video frames={count}, expected={expected}"
            )
    details.update(
        frames=expected,
        parquet_rows=parquet_rows,
        video_frames=video_counts,
        metadata_columns=sorted(REQUIRED_DAGGER_COLUMNS & columns),
    )
    return errors, details


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument(
        "--allow-active-staging",
        action="store_true",
        help="Do not fail merely because .staging contains an active episode.",
    )
    parser.add_argument("--json", action="store_true", help="Emit a JSON report.")
    args = parser.parse_args()
    root = args.dataset_root.expanduser().resolve()
    errors: list[str] = []
    details: list[dict[str, Any]] = []
    try:
        collection = load_json(root / "collection.json")
    except Exception as exc:
        collection = {}
        errors.append(f"invalid collection.json: {exc}")
    if collection.get("schema") != "autolife-hd-dagger-atomic-v1":
        errors.append("collection schema is not autolife-hd-dagger-atomic-v1")
    try:
        rows = read_index(root / "episode_index.jsonl")
    except Exception as exc:
        rows = []
        errors.append(f"invalid episode index: {exc}")

    ids = [str(row.get("episode_id", "")) for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("episode index contains duplicate episode_id values")
    sequences = [row.get("collection_sequence") for row in rows]
    if len(sequences) != len(set(sequences)):
        errors.append("episode index contains duplicate collection_sequence values")

    indexed = set(ids)
    committed = {
        path.name for path in (root / "episodes").iterdir() if path.is_dir()
    } if (root / "episodes").is_dir() else set()
    for episode_id in sorted(committed - indexed):
        errors.append(f"committed episode is absent from index: {episode_id}")
    for episode_id in sorted(indexed - committed):
        errors.append(f"indexed episode directory is absent: {episode_id}")

    staging = [
        path.name for path in (root / ".staging").iterdir() if path.is_dir()
    ] if (root / ".staging").is_dir() else []
    if staging and not args.allow_active_staging:
        errors.append(f"uncommitted staging directories remain: {', '.join(staging)}")

    for row in rows:
        episode_errors, episode_details = audit_episode(root, row)
        errors.extend(episode_errors)
        details.append(episode_details)

    report = {
        "success": not errors,
        "dataset_root": str(root),
        "episodes": len(rows),
        "frames": sum(max(0, int(row.get("frames", 0))) for row in rows),
        "active_staging": staging,
        "errors": errors,
        "details": details,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"{'PASS' if not errors else 'FAIL'}  episodes={report['episodes']} "
            f"frames={report['frames']} root={root}"
        )
        for error in errors:
            print(f"ERROR {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ImportError as exc:
        print(
            f"ERROR missing collector dependency: {exc}. Run with the "
            "lerobot_data_collector Python environment.",
            file=sys.stderr,
        )
        raise SystemExit(2)
