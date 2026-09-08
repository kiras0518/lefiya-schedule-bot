"""Container PID 1: supervise scheduler and webhook process groups."""

import logging
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

from .config import Settings, WebhookSettings
from .locking import open_lock
from .logging_config import configure_logging, log_event


def supervise(
    commands,
    *,
    popen=subprocess.Popen,
    sleeper=time.sleep,
    monotonic=time.monotonic,
    grace_seconds=10,
):
    children = {}
    stopping = None

    def stop(signum, _frame):
        nonlocal stopping
        stopping = signum

    previous = {
        sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        for name, command in commands.items():
            children[name] = popen(command, start_new_session=True)
        while stopping is None:
            for name, child in children.items():
                status = child.poll()
                if status is not None:
                    log_event(
                        logging.getLogger(__name__),
                        logging.ERROR,
                        "service_child_exited",
                        child=name,
                        exit_code=status,
                    )
                    return 1
            sleeper(0.2)
        return 0
    finally:
        # Leaders may exit before their workers; signal whole groups regardless.
        def send(sig):
            for child in children.values():
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    log_event(
                        logging.getLogger(__name__),
                        logging.WARNING,
                        "service_signal_denied",
                        child_pid=child.pid,
                        signal=int(sig),
                    )
                    # Keep cleaning up other groups even if a group is inaccessible.
                    if child.poll() is None:
                        with suppress(ProcessLookupError):
                            child.send_signal(sig)

        send(stopping or signal.SIGTERM)
        deadline = monotonic() + grace_seconds
        while monotonic() < deadline:
            alive = False
            for child in children.values():
                child.poll()
                try:
                    os.killpg(child.pid, 0)
                    alive = True
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # A denied liveness probe is not evidence that a group exited.
                    alive = True
            if not alive:
                break
            sleeper(0.1)
        send(signal.SIGKILL)
        for child in children.values():
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    configure_logging("INFO")
    try:
        settings = Settings.from_env()
        WebhookSettings.from_env()
        with open_lock(settings.job_lock_path):
            pass
        configure_logging(settings.log_level)
        return supervise(
            {
                "scheduler": [sys.executable, "-m", "lefiya_schedule_bot.scheduler"],
                "webhook": [
                    sys.executable,
                    "-m",
                    "gunicorn",
                    "--workers",
                    "2",
                    "--bind",
                    f"0.0.0.0:{os.environ.get('PORT', '8080')}",
                    "lefiya_schedule_bot.webhook:create_app()",
                ],
            }
        )
    except Exception as error:
        log_event(
            logging.getLogger(__name__),
            logging.ERROR,
            "service_failed",
            error_type=type(error).__name__,
            error=str(error),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
