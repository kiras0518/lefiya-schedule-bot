import json
from pathlib import Path


def test_docker_defaults_to_scheduler_and_webhook_entrypoint() -> None:
    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    contents = dockerfile.read_text()
    command_line = next(
        line.removeprefix("CMD ")
        for line in contents.splitlines()
        if line.startswith("CMD ")
    )
    command = json.loads(command_line)

    assert command == ["/app/entrypoint.sh"]


def test_entrypoint_schedules_automatic_job_and_starts_webhook() -> None:
    entrypoint = Path(__file__).parents[1] / "entrypoint.sh"
    contents = entrypoint.read_text()

    assert entrypoint.stat().st_mode & 0o111
    assert "exec python -m lefiya_schedule_bot.service" in contents
