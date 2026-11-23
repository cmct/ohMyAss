"""统一的后台任务：工作线程 + 主线程事件轮询。

背景：界面里原先有 5 套 `queue.Queue()` + `after()` 轮询，逻辑重复且容易
漏掉异常处理。这里把「后台执行 → 主线程处理事件 → 结束时停止」收敛成一个
小类，所有耗时操作（生成字幕、同步、下载、检测）都可以复用。

用法：
    runner = TaskRunner(self, interval=120)

    def work(emit):
        ...
        emit('progress', 50)
        emit('done', result)

    def on_event(kind, *payload):
        if kind == 'progress':
            return True          # 继续轮询
        ...
        return False             # 结束

    runner.start(work, on_event)
"""


import queue
import threading

from config_store import log_exception


class TaskRunner:
    def __init__(self, widget, interval: int = 120):
        self.widget = widget
        self.interval = max(20, int(interval))
        self.queue = queue.Queue()
        self._handler = None
        self._running = False
        self._after_id = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self, work, on_event):
        """work(emit) 在后台线程运行；on_event(kind, *payload) 在主线程运行。

        on_event 返回 False 时停止轮询；返回其它值继续。
        """
        if self._running:
            raise RuntimeError('已有后台任务在运行')
        self._handler = on_event
        self._running = True
        self.queue = queue.Queue()

        def worker():
            try:
                work(self.emit)
            except Exception as exc:
                log_exception('后台任务', exc)
                self.emit('error', str(exc) or type(exc).__name__)

        threading.Thread(target=worker, daemon=True).start()
        self._schedule()
        return self

    def emit(self, kind, *payload):
        """线程安全：把事件放入队列，由主线程处理。"""
        self.queue.put((kind, payload))

    def stop(self):
        self._running = False
        self._cancel_after()

    def _cancel_after(self):
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None

    def _schedule(self):
        if self._running:
            try:
                self._after_id = self.widget.after(self.interval, self._poll)
            except Exception:
                self._running = False

    def _poll(self):
        self._after_id = None
        if not self._running:
            return
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            keep = True
            try:
                keep = self._handler(kind, *payload)
            except Exception as exc:
                log_exception('后台任务回调', exc)
                keep = False
            if keep is False:
                self._running = False
                return
        self._schedule()
