"""Speedtest providers.

A provider is a service that can measure your bandwidth. Each one has its
own server network, its own API, and its own idea of what a "test" looks
like. We abstract that behind a common interface so you can compare your
connection against more than one network at a time.

Currently supported:

  ookla       speedtest.net's server network. The classic. Many servers
              worldwide, chosen by distance from you. Ookla is a commercial
              company and this API is unofficial, so we treat it as one
              option among several rather than the only one.

  cloudflare  Cloudflare's own measurement endpoint, used by
              speed.cloudflare.com. It measures to the nearest Cloudflare
              data centre, which is the network your traffic to most of
              the internet actually crosses. No API key.

  librespeed  LibreSpeed, the open-source speedtest server. The public
              server list is community-run and self-hostable, so this
              provider also works against a LibreSpeed instance you run
              yourself. Useful for testing a specific vantage point.

Netflix's fast.com is deliberately NOT supported: its API is behind
Akamai bot protection that rejects non-browser clients with a 403, and
working around that would mean impersonating a browser we are not. If they
ever open it up, it slots in here as a fourth provider.
"""
from __future__ import annotations

from .base import Provider, ProviderError
from .cloudflare import CloudflareProvider
from .librespeed import LibreSpeedProvider
from .ookla import OoklaProvider

_PROVIDERS: dict[str, Provider] = {
    "ookla": OoklaProvider(),
    "cloudflare": CloudflareProvider(),
    "librespeed": LibreSpeedProvider(),
}

ALIASES = {
    "speedtest.net": "ookla",
    "ookla": "ookla",
    "speedtest": "ookla",
    "cf": "cloudflare",
    "cloudflare": "cloudflare",
    "speed.cloudflare.com": "cloudflare",
    "ls": "librespeed",
    "librespeed": "librespeed",
    "open": "librespeed",
}


def get_provider(name: str) -> Provider:
    """Look up a provider by name or alias."""
    key = ALIASES.get(name.lower().strip())
    if key is None:
        raise ProviderError(
            f"unknown provider {name!r}; "
            f"choose from: {', '.join(sorted(_PROVIDERS))}"
        )
    return _PROVIDERS[key]


def all_providers() -> list[Provider]:
    return list(_PROVIDERS.values())


__all__ = [
    "Provider",
    "ProviderError",
    "OoklaProvider",
    "CloudflareProvider",
    "LibreSpeedProvider",
    "get_provider",
    "all_providers",
    "ALIASES",
]
