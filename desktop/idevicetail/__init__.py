"""iDeviceTail — wireless iOS/iPadOS log collection.

Two capture engines feed one normalized log bus:

* ``engine_device``  — real system logs / crash reports / sysdiagnose via the
  ``pymobiledevice3`` CLI over Wi-Fi (requires a one-time USB pairing + Trust and,
  on iOS 16+, Developer Mode).
* ``engine_agent``   — app-level logs streamed by the bundled Swift agent app
  (``IDeviceTailKit``) over a framed TCP protocol. No pairing, no cable, no
  Developer Mode; limited to the agent's own process by the iOS sandbox.

See ``docs/FEASIBILITY.md`` for exactly what iOS does and does not allow.
"""

__version__ = "0.1.0"
