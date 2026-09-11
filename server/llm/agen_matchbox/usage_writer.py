"""用量落库的后台写入器。

设计目标（对应用户需求“完全后台异步，不带来性能影响”）：

- 模型调用返回路径（``on_llm_end`` / ``on_llm_error``）只做内存计算，
  把真正的 SQLite 写入 + 点数结算投递到后台单线程；
- 流式中断/失败同样走后台写入，保证“中断也记账”；
- 后台线程崩溃不影响模型调用本身，失败任务会计数并丢弃（避免无界堆积）。

线程模型：进程级单例 ``BackgroundUsageWriter``，内部 ``queue.Queue`` +
单守护线程。``max_queue_size`` 有界，满时丢弃最旧任务并计数。
"""

from __future__ import annotations

import atexit
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class UsageWriteTask:
    """单次用量落库任务；``record`` 负责真正的 DB 写入。"""

    record: Callable[[], Optional[Dict[str, Any]]]
    on_recorded: Optional[Callable[[Dict[str, Any]], None]] = None
    on_error: Optional[Callable[[BaseException], None]] = None
    enqueued_at: float = field(default_factory=time.time)


class BackgroundUsageWriter:
    """后台单线程用量写入器。"""

    def __init__(self, *, max_queue_size: int = 10000, thread_name: str = "matchbox-usage-writer"):
        self._tasks: "queue.Queue[Optional[UsageWriteTask]]" = queue.Queue(maxsize=max(1, int(max_queue_size)))
        self._dropped_tasks = 0
        self._failed_tasks = 0
        self._completed_tasks = 0
        self._lock = threading.Lock()
        self._stopped = False
        self._thread = threading.Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    def submit(self, task: UsageWriteTask) -> bool:
        """投递任务；队列满时丢弃最旧任务并计数，永不阻塞调用线程。"""
        if self._stopped:
            return False
        try:
            self._tasks.put_nowait(task)
            return True
        except queue.Full:
            try:
                self._tasks.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self._dropped_tasks += 1
            try:
                self._tasks.put_nowait(task)
                return True
            except queue.Full:
                return False

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            dropped = self._dropped_tasks
            failed = self._failed_tasks
            completed = self._completed_tasks
        return {
            "queued": self._tasks.qsize(),
            "dropped_tasks": dropped,
            "failed_tasks": failed,
            "completed_tasks": completed,
            "alive": self._thread.is_alive(),
        }

    def _run(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                self._tasks.task_done()
                return
            try:
                summary = task.record()
                with self._lock:
                    self._completed_tasks += 1
                if summary is not None and task.on_recorded is not None:
                    try:
                        task.on_recorded(summary)
                    except Exception:
                        pass
            except Exception as exc:
                with self._lock:
                    self._failed_tasks += 1
                if task.on_error is not None:
                    try:
                        task.on_error(exc)
                    except Exception:
                        pass
            finally:
                self._tasks.task_done()

    def shutdown(self, *, wait: bool = False, timeout: float = 5.0) -> None:
        """停止后台线程；默认不等待，避免阻塞进程退出。"""
        self._stopped = True
        try:
            self._tasks.put_nowait(None)
        except queue.Full:
            pass
        if wait:
            self._thread.join(timeout=timeout)


_default_writer: Optional[BackgroundUsageWriter] = None
_default_writer_lock = threading.Lock()


def get_default_usage_writer() -> BackgroundUsageWriter:
    """返回进程级默认后台写入器（懒创建）。"""
    global _default_writer
    if _default_writer is not None:
        return _default_writer
    with _default_writer_lock:
        if _default_writer is None:
            _default_writer = BackgroundUsageWriter()
            atexit.register(_default_writer.shutdown)
        return _default_writer


def set_default_usage_writer(writer: Optional[BackgroundUsageWriter]) -> None:
    """覆盖进程级默认写入器（主要供测试使用）。"""
    global _default_writer
    with _default_writer_lock:
        _default_writer = writer
