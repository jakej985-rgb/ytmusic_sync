import threading
from collections import deque
from datetime import datetime
from typing import Optional, List, Dict, Any


class MetadataTracker:
    """Thread-safe tracker for metadata changes, tag rewrites, and artwork updates."""

    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self._items = deque(maxlen=max_history)

    def log_change(
        self,
        title: str,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        thumbnail: Optional[str] = None,
        source: str = "Metadata Editor",
        detail: str = "Updated tags & artwork",
        status: str = "completed",
        error: Optional[str] = None
    ) -> dict:
        item = {
            "id": f"meta_{int(datetime.now().timestamp() * 1000)}",
            "category": "metadata_change",
            "title": title or "Untitled",
            "artist": artist,
            "album": album,
            "thumbnail": thumbnail,
            "status": status,
            "current_step": detail,
            "source": source,
            "created_at": datetime.now().isoformat(),
            "error": error
        }
        with self._lock:
            self._items.appendleft(item)
        return item

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._items)[:limit]

    def clear(self):
        with self._lock:
            self._items.clear()


metadata_tracker = MetadataTracker()
