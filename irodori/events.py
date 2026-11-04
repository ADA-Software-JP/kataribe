import json
import queue
import threading


class Bus:
    """SSE 配信用。購読者ごとにキューを持ち、遅い購読者は古いものから捨てる。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = set()

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def count(self):
        """今つながっている購読者(画面)の数。"""
        with self._lock:
            return len(self._subs)

    def emit(self, event, data=None):
        payload = f"event: {event}\ndata: {json.dumps(data if data is not None else {}, ensure_ascii=False)}\n\n"
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass
