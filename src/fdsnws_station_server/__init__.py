"""FDSNWS Station MCP Server: FDSN station metadata from any fdsnws-station 1.1 Datacenter."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-fdsnws-station-server")
except PackageNotFoundError:  # running from an uninstalled source tree
    __version__ = "0.0.0+unknown"
