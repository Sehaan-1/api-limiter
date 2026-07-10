import yaml
import threading
import os
import time
from typing import Optional
from dataclasses import dataclass

@dataclass
class Rule:
    path: str
    capacity: float
    refill_rate: float
    method: Optional[str] = None

class ConfigLoader:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance.rules = []
                cls._instance._last_mtime = 0
                cls._instance.load_config()
                cls._instance._start_watcher()
        return cls._instance

    def _start_watcher(self):
        def watch():
            while True:
                try:
                    path = os.environ.get("RATE_LIMIT_CONFIG_PATH", "config/rate_limits.yaml")
                    mtime = os.path.getmtime(path)
                    if mtime > self._last_mtime:
                        self.load_config()
                except Exception:
                    pass
                time.sleep(1)

        threading.Thread(target=watch, daemon=True).start()

    def load_config(self):
        path = os.environ.get("RATE_LIMIT_CONFIG_PATH", "config/rate_limits.yaml")
        try:
            mtime = os.path.getmtime(path)
            with open(path, "r") as f:
                data = yaml.safe_load(f)
                # ponytail: Atomic update of rules list
                self.rules = [Rule(**r) for r in data["rate_limits"]]
                self._last_mtime = mtime
        except Exception as e:
            print(f"Error loading config: {e}")

    def get_rule(self, path: str, method: str) -> Rule:
        # Match exact path and method first
        for rule in self.rules:
            if rule.path == path and rule.method == method:
                return rule
        # Match wildcard
        for rule in self.rules:
            if rule.path == "*":
                return rule
        raise ValueError("No rate limit rule found")
