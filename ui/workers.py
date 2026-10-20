"""Background workers.

Long operations (imports, divergence scans, exports, big queries) never run on
the GUI thread.  :class:`Worker` wraps a callable in a ``QThread`` and reports
progress, completion and failure through Qt signals.

Threading contract
------------------
The worker object is moved to its own ``QThread``, so its signals are emitted
from that thread.  All user callbacks are invoked through :class:`_Dispatcher`,
a ``QObject`` created on the **calling (GUI) thread**; Qt therefore queues every
signal across the thread boundary and the callbacks always run on the GUI
thread.  Touching widgets (or Qt models) from the worker thread is what crashes
an application, so this indirection is not optional.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

#: (percent, message)
ProgressFn = Callable[[int, str], None]


class Worker(QObject):
    """Runs ``fn(*args, progress=..., is_cancelled=..., **kwargs)`` on a thread."""

    finished = Signal(object)          # result
    failed = Signal(str, str)          # message, traceback
    progress = Signal(int, str)        # percent (-1 = indeterminate), message
    done = Signal()                    # always emitted, after finished/failed

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = dict(kwargs)
        self._thread: Optional[QThread] = None
        self._cancelled = False

    # ------------------------------------------------------------------

    def start(self) -> "Worker":
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._run)
        self.finished.connect(self._thread.quit)
        self.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def join(self, timeout_ms: int = 30_000) -> bool:
        return True if self._thread is None else self._thread.wait(timeout_ms)

    # ------------------------------------------------------------------

    def _emit_progress(self, percent: int, message: str = "") -> None:
        self.progress.emit(int(percent), str(message))

    @Slot()
    def _run(self) -> None:
        try:
            result = self._fn(*self._args, progress=self._emit_progress,
                              is_cancelled=lambda: self._cancelled, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        else:
            if not self._cancelled:
                self.finished.emit(result)
        finally:
            self.done.emit()


class _Dispatcher(QObject):
    """Lives on the GUI thread and invokes the user's callbacks there."""

    def __init__(self, on_done: Optional[Callable[[Any], None]] = None,
                 on_error: Optional[Callable[[str, str], None]] = None,
                 on_progress: Optional[ProgressFn] = None,
                 on_finished: Optional[Callable[[], None]] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._on_done = on_done
        self._on_error = on_error
        self._on_progress = on_progress
        self._on_finished = on_finished

    @Slot(object)
    def handle_result(self, result: Any) -> None:
        if self._on_done is None:
            return
        try:
            self._on_done(result)
        except Exception as exc:  # noqa: BLE001 - never let a callback kill the app
            traceback.print_exc()
            if self._on_error is not None:
                self._on_error(f"{type(exc).__name__}: {exc}", traceback.format_exc())

    @Slot(str, str)
    def handle_failure(self, message: str, detail: str) -> None:
        if self._on_error is not None:
            self._on_error(message, detail)

    @Slot(int, str)
    def handle_progress(self, percent: int, message: str) -> None:
        if self._on_progress is not None:
            self._on_progress(percent, message)

    @Slot()
    def handle_done(self) -> None:
        if self._on_finished is not None:
            self._on_finished()


def run_async(parent: QObject, fn: Callable[..., Any],
              on_done: Optional[Callable[[Any], None]] = None,
              on_error: Optional[Callable[[str, str], None]] = None,
              on_progress: Optional[ProgressFn] = None,
              *args: Any, **kwargs: Any) -> Worker:
    """Start ``fn`` on a worker thread; callbacks run on the GUI thread.

    ``parent`` is used both as the Qt parent of the dispatcher and as the place
    the worker is registered, so neither the worker nor the dispatcher can be
    garbage collected before the job completes.
    """
    worker = Worker(fn, *args, **kwargs)

    registry: set = getattr(parent, "_active_workers", None)
    if registry is None:
        registry = set()
        try:
            setattr(parent, "_active_workers", registry)
        except AttributeError:  # pragma: no cover - exotic QObject
            pass

    def _cleanup() -> None:
        registry.discard(worker)

    dispatcher = _Dispatcher(on_done, on_error, on_progress,
                             on_finished=_cleanup, parent=parent)
    worker.finished.connect(dispatcher.handle_result)
    worker.failed.connect(dispatcher.handle_failure)
    worker.progress.connect(dispatcher.handle_progress)
    worker.done.connect(dispatcher.handle_done)

    registry.add(worker)
    setattr(worker, "_dispatcher", dispatcher)
    worker.start()
    return worker


def wait_for(worker: Worker, timeout_ms: int = 60_000) -> bool:
    """Block until ``worker`` finishes (used by tests and by ``--check``)."""
    return worker.join(timeout_ms)


__all__ = ["Worker", "run_async", "wait_for", "ProgressFn"]
