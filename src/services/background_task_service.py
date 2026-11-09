"""Track independent classification jobs without the retired prompt-generation workflow."""
import asyncio
import threading


class BackgroundTaskService:
    def __init__(self):
        self._workers = set()
        self._lock = threading.Lock()

    def track(self, coroutine):
        def run():
            try:
                asyncio.run(coroutine)
            finally:
                with self._lock:
                    self._workers.discard(thread)
        thread = threading.Thread(target=run,daemon=True)
        with self._lock:
            self._workers.add(thread)
        thread.start()
