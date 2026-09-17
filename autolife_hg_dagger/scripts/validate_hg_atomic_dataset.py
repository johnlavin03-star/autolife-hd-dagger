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
REQUIRED_RGBD_CAMERAS = {
    "rgbd_head_color",
    "rgbd_head_depth",
    "hand_left",
    "hand_right",
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
    details["manifest_saved"] = manifest.get("status") == "saved"
    if str(manifest.get("episode_id")) != episode_id:
        errors.append(f"{episode_id}: manifest episode_id mismatch")
    if int(manifest.get("frames", -1)) != expected:
        errors.append(f"{episode_id}: manifest/index frame count mismatch")

    parquet_files = sorted((episode / "data").rglob("*.parquet"))
    parquet_rows = 0
    columns: set[str] = set()
    parquet_episode_indices: set[int] = set()
    for parquet in parquet_files:
        try:
            metadata = pq.ParquetFile(parquet).metadata
            parquet_rows += metadata.num_rows
            schema_names = pq.read_schema(parquet).names
            columns.update(schema_names)
            if "episode_index" in schema_names:
                parquet_episode_indices.update(
                    int(value)
                    for value in pq.read_table(
                        parquet, columns=["episode_index"]
                    ).column("episode_index").to_pylist()
                )
        except Exception as exc:
            errors.append(f"{episode_id}: unreadable Parquet {parquet.name}: {exc}")
    if not parquet_files:
        errors.append(f"{episode_id}: no Parquet files")
    if parquet_rows != expected:
        errors.append(
            f"{episode_id}: Parquet rows={parquet_rows}, expected={expected}"
        )
    if parquet_episode_indices != {0}:
        errors.append(
            f"{episode_id}: Parquet episode_index values are "
            f"{sorted(parquet_episode_indices)}, expected [0]"
        )
    missing_columns = sorted(REQUIRED_DAGGER_COLUMNS - columns)
    if missing_columns:
        errors.append(
            f"{episode_id}: missing HG-DAGGER metadata columns: {', '.join(missing_columns)}"
        )

    info_path = episode / "meta" / "info.json"
    lerobot_episode_count = 0
    try:
        info = load_json(info_path)
        lerobot_episode_count = int(info.get("total_episodes", -1))
        if lerobot_episode_count != 1:
            errors.append(f"{episode_id}: total_episodes is not 1")
        if int(info.get("total_frames", -1)) != expected:
            errors.append(f"{episode_id}: info.json frame count mismatch")
        camera_keys = sorted(
            key.removeprefix("observation.images.")
            for key in info.get("features", {})
            if key.startswith("observation.images.")
        )
        missing_cameras = sorted(REQUIRED_RGBD_CAMERAS - set(camera_keys))
        extra_cameras = sorted(set(camera_keys) - REQUIRED_RGBD_CAMERAS)
        if missing_cameras or extra_cameras:
            errors.append(
                f"{episode_id}: camera schema mismatch; "
                f"missing={missing_cameras}, extra={extra_cameras}"
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
        parquet_episode_indices=sorted(parquet_episode_indices),
        lerobot_episode_count=lerobot_episode_count,
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
    parser.add_argument(
        "--expect-episodes",
        type=int,
        default=None,
        help="Require exactly this many committed/saved episodes.",
    )
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

    failed_dirs = [
        path for path in (root / ".failed").iterdir() if path.is_dir()
    ] if (root / ".failed").is_dir() else []
    for failed in failed_dirs:
        if not (failed / "failure.json").exists() and not (failed / "recovery.json").exists():
            errors.append(f"unlabelled failed/quarantine directory: {failed.name}")

    raw_images = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        and ".failed" not in path.parts
        and ".staging" not in path.parts
    )
    if raw_images:
        errors.append(
            f"orphan/unencoded raw image files remain outside quarantine: {len(raw_images)}"
        )

    for row in rows:
        episode_errors, episode_details = audit_episode(root, row)
        errors.extend(episode_errors)
        details.append(episode_details)

    sidecar_root = root.parent.parent / "hg_dagger_sidecar"
    sidecar_manifests: list[dict[str, Any]] = []
    for path in sidecar_root.glob("session-*/interventions/*/manifest.json"):
        try:
            sidecar_manifests.append(load_json(path))
        except Exception as exc:
            errors.append(f"invalid sidecar manifest {path}: {exc}")
    sidecar_saved_count = sum(
        1 for manifest in sidecar_manifests if manifest.get("status") == "saved"
    )
    manifest_saved_count = sum(
        int(detail.get("manifest_saved", False)) for detail in details
    )
    lerobot_episode_count = sum(
        int(detail.get("lerobot_episode_count", 0)) for detail in details
    )
    parquet_episode_index_count = sum(
        len(detail.get("parquet_episode_indices", [])) for detail in details
    )
    video_episode_counts = {
        camera: sum(
            1 for detail in details
            if camera in detail.get("video_frames", {})
            and detail["video_frames"][camera] == detail.get("frames")
        )
        for camera in sorted(REQUIRED_RGBD_CAMERAS)
    }
    expected_count = len(rows)
    count_values = {
        "atomic_manifest_saved": manifest_saved_count,
        "sidecar_manifest_saved": sidecar_saved_count,
        "lerobot": lerobot_episode_count,
        "parquet_episode_index": parquet_episode_index_count,
        **{f"video:{key}": value for key, value in video_episode_counts.items()},
    }
    for label, value in count_values.items():
        if value != expected_count:
            errors.append(
                f"count mismatch: {label}={value}, episode_index={expected_count}"
            )
    if args.expect_episodes is not None and expected_count != args.expect_episodes:
        errors.append(
            f"expected {args.expect_episodes} committed episodes, found {expected_count}"
        )

    report = {
        "success": not errors,
        "dataset_root": str(root),
        "episodes": len(rows),
        "frames": sum(max(0, int(row.get("frames", 0))) for row in rows),
        "active_staging": staging,
        "quarantined_failed_episodes": len(failed_dirs),
        "orphan_raw_images": len(raw_images),
        "manifest_saved_count": manifest_saved_count,
        "sidecar_manifest_saved_count": sidecar_saved_count,
        "lerobot_episode_count": lerobot_episode_count,
        "parquet_episode_index_count": parquet_episode_index_count,
        "video_episode_counts": video_episode_counts,
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
        print(
            "COUNTS "
            f"atomic_manifest_saved={report['manifest_saved_count']} "
            f"sidecar_manifest_saved={report['sidecar_manifest_saved_count']} "
            f"lerobot={report['lerobot_episode_count']} "
            f"parquet_episode_index={report['parquet_episode_index_count']} "
            f"videos={report['video_episode_counts']} "
            f"staging={len(staging)} orphan_images={len(raw_images)}"
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
