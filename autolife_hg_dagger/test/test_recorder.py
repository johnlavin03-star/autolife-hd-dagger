import json
import os
import threading
import time

import pytest

from autolife_hg_dagger.recorder import CollectorFifo, TraceWriter


def test_trace_manifest_and_ring(tmp_path):
    writer = TraceWriter(str(tmp_path), ring_seconds=5.0)
    writer.append_ring({"wall_ns": 10, "stream": "policy"})
    directory = writer.start("session-a", "int-a", False, {"schema": "base16"})
    writer.write("events", {"kind": "start"})
    writer.finish("saved", 20, "operator resume")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "saved"
    assert manifest["frame_count"] == 20
    assert (directory / "pre_failure_trace.jsonl").exists()


def test_pending_manifest_can_be_finalized_after_trace_closes(tmp_path):
    writer = TraceWriter(str(tmp_path), ring_seconds=5.0)
    directory = writer.start("session-a", "int-b", True, {"schema": "base21"})
    manifest_path = writer.finish(
        "collector_save_pending", 30, "operator resume",
        {"request_id": "request-a", "success": False},
    )
    assert manifest_path == directory / "manifest.json"
    TraceWriter.update_finished_manifest(
        manifest_path,
        "saved",
        {"request_id": "request-a", "success": True, "event": "save"},
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "saved"
    assert manifest["collector_result"]["success"] is True


def test_fifo_missing_is_safe(tmp_path):
    fifo = CollectorFifo(str(tmp_path / "rgb"), str(tmp_path / "rgbd"))
    assert not fifo.command(False, "start")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
def test_fifo_command_requires_correlated_ack(tmp_path):
    fifo_path = tmp_path / "rgb" / ".official_recording_control"
    fifo_path.parent.mkdir()
    os.mkfifo(fifo_path)
    fifo = CollectorFifo(str(fifo_path), str(tmp_path / "rgbd"))

    def collector():
        with fifo_path.open("r", encoding="utf-8") as handle:
            command, request_id = handle.readline().strip().split()
        time.sleep(0.03)
        (fifo_path.parent / ".official_recording_status.json").write_text(
            json.dumps({
                "event": command,
                "success": True,
                "request_id": request_id,
                "episode_index": 4,
                "frames": 0,
                "message": "episode started",
            }),
            encoding="utf-8",
        )

    thread = threading.Thread(target=collector)
    thread.start()
    result = fifo.command_and_wait(False, "start", timeout=1.0)
    thread.join(timeout=1.0)
    assert result.acknowledged and result.success
    assert result.episode_index == 4


def test_fifo_uses_request_scoped_ack_during_overlapping_pack(tmp_path):
    fifo_path = tmp_path / "rgb" / ".official_recording_control"
    fifo_path.parent.mkdir()
    status_path = fifo_path.parent / ".official_recording_status.json"
    status_dir = fifo_path.parent / ".official_recording_status.json.d"
    status_dir.mkdir()
    fifo = CollectorFifo(str(fifo_path), str(tmp_path / "rgbd"))
    request_id = "matching-request"

    def collector():
        # Simulate a different background packer completing first and
        # overwriting the legacy shared status file.
        status_path.write_text(
            json.dumps({
                "event": "save",
                "success": True,
                "request_id": "older-request",
                "episode_index": 2,
                "frames": 90,
                "message": "older pack complete",
            }),
            encoding="utf-8",
        )
        time.sleep(0.03)
        (status_dir / f"{request_id}.json").write_text(
            json.dumps({
                "event": "save",
                "success": True,
                "request_id": request_id,
                "episode_index": 7,
                "frames": 120,
                "message": "matching pack complete",
            }),
            encoding="utf-8",
        )

    thread = threading.Thread(target=collector)
    thread.start()
    result = fifo.wait_for_result(False, "save", request_id, timeout=1.0)
    thread.join(timeout=1.0)
    assert result.acknowledged and result.success
    assert result.episode_index == 7
    assert result.frames == 120
