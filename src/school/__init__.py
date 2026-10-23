"""School Information Adapter — root package.

One subpackage per module in the design plan (§3): domain, merge, spi,
store, driver_hac, driver_web, driver_classroom_web, vault, mcp_server, app.
Pattern: package-per-module; dependency direction is enforced by review
(app -> everything; drivers -> spi + domain + store; domain/merge/spi are
dependency-free).
"""
