from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get(
        "RAINBO_SCRAPE_DATA", str(Path.home() / ".local/share/rainbo-scrape"))))
    timeout: float = 25
    max_body: int = 5 * 1024 * 1024
    cache_bytes: int = 256 * 1024 * 1024
    cache_ttl: int = 3600
    user_agent: str = "RAINBO-Scrape/0.1 (+self-hosted; respects robots.txt)"
    host_delay: float = 0.5
    max_browser_requests: int = 80
    max_browser_bytes: int = 20 * 1024 * 1024
    # Test fixtures only. This is never exposed in MCP, CLI or crawl arguments.
    allow_test_loopback: bool = False

    def prepare(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.data_dir.chmod(0o700)
        return self


def brave_path():
    candidates = [os.environ.get("RAINBO_BRAVE_PATH", ""),
                  shutil.which("brave-browser") or "",
                  r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                  r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe"]
    for value in candidates:
        if value and Path(value).is_file() and "brave" in str(Path(value).resolve()).lower():
            return str(Path(value).resolve())
    return None
