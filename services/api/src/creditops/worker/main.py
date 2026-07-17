import logging

from creditops.config import Settings
from creditops.observability import configure_structured_logging, log_event


def main() -> None:
    settings = Settings()
    configure_structured_logging(
        service_name=settings.service_name,
        level=settings.log_level,
    )
    log_event(
        logging.getLogger(__name__),
        logging.CRITICAL,
        "Worker execution refused because the processing runtime is not implemented",
        {"event": "worker_runtime_not_ready"},
    )
    raise SystemExit(78)


if __name__ == "__main__":
    main()
