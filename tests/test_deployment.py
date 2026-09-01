import json
from pathlib import Path


def test_docker_defaults_to_webhook_server_on_platform_port() -> None:
    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    contents = dockerfile.read_text()
    command_line = next(
        line.removeprefix("CMD ")
        for line in contents.splitlines()
        if line.startswith("CMD ")
    )
    command = json.loads(command_line)

    assert command[:2] == ["sh", "-c"]
    assert command[2].startswith("exec gunicorn")
    assert "${PORT:-8080}" in command[2]
    assert "lefiya_schedule_bot.webhook:create_app()" in command[2]
