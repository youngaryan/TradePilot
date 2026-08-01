"""Gunicorn lifecycle hooks for prometheus-client multiprocess cleanup."""


def child_exit(server, worker) -> None:  # noqa: ANN001
    del server
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
    except Exception:
        # Metrics cleanup must not prevent a worker from exiting. Stale files are
        # removed on container startup and remain scoped to the container tmpfs.
        return
