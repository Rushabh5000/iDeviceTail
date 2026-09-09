"""Runtime configuration. Everything is overridable by CLI flag or environment
variable (``IDEVICETAIL_*``). No config file is required for normal use."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"IDEVICETAIL_{name}", default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(f"IDEVICETAIL_{name}", str(default)))
    except ValueError:
        return default


def default_data_dir() -> Path:
    # Keep app data next to the repo by default so it is easy to find / wipe.
    return Path(_env("DATA_DIR", str(Path.cwd() / "data")))


@dataclass(slots=True)
class Config:
    # --- Web UI + browser WebSocket + REST (project-hub "backend" port) ---
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 3017))

    # --- Reserved "frontend" port (single-process app; kept for the port registry) ---
    frontend_port: int = field(default_factory=lambda: _env_int("FRONTEND_PORT", 3016))

    # --- Engine B: framed TCP listener the iOS agent connects to (infra port) ---
    agent_host: str = field(default_factory=lambda: _env("AGENT_HOST", "0.0.0.0"))
    agent_port: int = field(default_factory=lambda: _env_int("AGENT_PORT", 45455))

    # --- Bind policy -----------------------------------------------------------
    # "lan"   -> refuse connections whose source IP is not private/loopback
    # "any"   -> accept anything that reaches the socket
    # "local" -> bind only to 127.0.0.1 (no network at all)
    bind_policy: str = field(default_factory=lambda: _env("BIND_POLICY", "lan"))

    # --- Discovery -----------------------------------------------------------
    discover_apple: bool = field(default_factory=lambda: _env("DISCOVER_APPLE", "1") != "0")
    discover_agent: bool = field(default_factory=lambda: _env("DISCOVER_AGENT", "1") != "0")

    # --- Storage -----------------------------------------------------------
    store_enabled: bool = field(default_factory=lambda: _env("STORE", "1") != "0")
    data_dir: Path = field(default_factory=default_data_dir)
    db_flush_rows: int = 500
    db_flush_seconds: float = 1.0

    # --- In-memory ring buffer served to late-joining UI clients ---
    ring_size: int = field(default_factory=lambda: _env_int("RING_SIZE", 200_000))

    # --- Engine A -----------------------------------------------------------
    pymd_bin: str = field(default_factory=lambda: _env("PYMD", "pymobiledevice3"))
    device_reconnect_min: float = 1.0
    device_reconnect_max: float = 30.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "idevicetail.sqlite"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "crashes").mkdir(exist_ok=True)
        (self.data_dir / "sysdiagnose").mkdir(exist_ok=True)
        (self.data_dir / "exports").mkdir(exist_ok=True)
