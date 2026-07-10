import yaml
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
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.rules = []
            cls._instance.load_config()
        return cls._instance

    def load_config(self):
        with open("config/rate_limits.yaml", "r") as f:
            data = yaml.safe_load(f)
            self.rules = [Rule(**r) for r in data["rate_limits"]]

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
