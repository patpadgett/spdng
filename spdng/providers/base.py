"""The provider interface.

Everything a provider must be able to do:

  * find candidate servers, best first
  * measure latency to one of them
  * measure download throughput
  * measure upload throughput

And the rules every provider must obey:

  * A failed measurement is NEVER a number. Return None or record an error.
  * Latency averages only over successful probes.
  * Throughput is computed from bytes actually transferred over the
    measured window.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..measure import LatencyResult, ThroughputResult
from ..servers import Server


class ProviderError(RuntimeError):
    """The provider could not do what was asked."""


@dataclass(slots=True)
class Provider:
    """Base class for a speedtest provider."""

    id: str = "base"
    label: str = "Base"
    #: Human note about whose infrastructure this uses.
    infrastructure: str = ""
    #: Whether the provider can pick a specific server from a list.
    has_server_list: bool = True

    def list_servers(self, limit: int = 10) -> list[Server]:
        """Candidate servers, best (nearest) first."""
        raise NotImplementedError

    def measure_latency(self, server: Server, count: int = 5,
                        timeout: float = 10.0) -> LatencyResult:
        raise NotImplementedError

    def measure_download(self, server: Server, duration: float = 10.0,
                         threads: int = 4) -> ThroughputResult:
        raise NotImplementedError

    def measure_upload(self, server: Server, duration: float = 10.0,
                       threads: int = 2) -> ThroughputResult:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.label} ({self.infrastructure})"


def merge_results(results: list) -> object:
    """Combine several provider results into one comparable view.

    Used by `spdng compare`, which runs the same measurement against
    multiple providers and lines the numbers up.
    """
    return results
