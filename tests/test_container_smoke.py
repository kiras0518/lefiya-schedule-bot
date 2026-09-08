"""Opt-in Linux container smoke tests: LEFIYA_TEST_IMAGE=<built image> pytest."""

import os
import shutil
import subprocess

import pytest

IMAGE = os.environ.get("LEFIYA_TEST_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE or not shutil.which("docker"),
    reason="requires Docker and LEFIYA_TEST_IMAGE pointing to a built image",
)


def run_container(*command):
    return subprocess.run(
        ["docker", "run", "--rm", IMAGE, *command],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_default_entrypoint_fails_fast_without_credentials():
    result = run_container()
    assert result.returncode == 1
    assert "LINE_CHANNEL_ACCESS_TOKEN is required" in result.stderr


def test_module_help_exits():
    result = run_container("python", "-m", "lefiya_schedule_bot", "--help")
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_scheduler_starts_with_mock_job_and_exits():
    code = """
from datetime import datetime
from zoneinfo import ZoneInfo
from lefiya_schedule_bot.scheduler import scheduler_loop
from lefiya_schedule_bot.logging_config import configure_logging
configure_logging("INFO")
def stop(_):
    raise SystemExit(0)
scheduler_loop(ZoneInfo("Asia/Taipei"),
    clock=lambda: datetime(2026, 9, 7, 14, tzinfo=ZoneInfo("Asia/Taipei")),
    runner=lambda: 0, sleeper=stop)
"""
    result = run_container("python", "-c", code)
    assert result.returncode == 0
    assert "scheduler_started" in result.stderr
    assert "scheduler_job_completed" in result.stderr
