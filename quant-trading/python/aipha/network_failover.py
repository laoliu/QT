"""Process local HTTP proxy failover for external QT data links.

The desktop/system proxy remains the first transport. A connection or read
timeout then gets one direct retry, and the host is remembered as direct for
the lifetime of the process. Local QMT/Receiver addresses never use a proxy.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_DIRECT_HOSTS: set[str] = set()
_INSTALLED = False
_ORIGINAL_SEND = None
_ORIGINAL_URLOPEN = None


def _is_local_host(host: str | None) -> bool:
    if not host:
        return True
    value = host.lower().rstrip(".")
    if value in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value.endswith(".local")
    return address.is_loopback or address.is_private or address.is_link_local


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def install_requests_failover() -> None:
    """Install the shared Requests fallback once per interpreter."""
    global _INSTALLED, _ORIGINAL_SEND, _ORIGINAL_URLOPEN
    if _INSTALLED:
        return
    original = requests.Session.send

    def send(session, request, **kwargs):
        host = _host(request.url)
        proxies = kwargs.get("proxies") or {}
        if _is_local_host(host) or host in _DIRECT_HOSTS or not proxies:
            return original(session, request, **kwargs)
        try:
            return original(session, request, **kwargs)
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            direct_kwargs = dict(kwargs)
            direct_kwargs["proxies"] = {}
            response = original(session, request, **direct_kwargs)
            with _LOCK:
                _DIRECT_HOSTS.add(host)
            logger.warning("network proxy fallback: host=%s error=%s transport=direct",
                           host, type(exc).__name__)
            return response

    send._qt_network_failover = True  # type: ignore[attr-defined]
    requests.Session.send = send  # type: ignore[method-assign]
    _ORIGINAL_SEND = original
    urlopen = urllib.request.urlopen

    def open_url(url, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, *, context=None):
        target = url.full_url if isinstance(url, urllib.request.Request) else str(url)
        host = _host(target)
        if _is_local_host(host) or host in _DIRECT_HOSTS:
            return urlopen(url, data=data, timeout=timeout, context=context)
        try:
            return urlopen(url, data=data, timeout=timeout, context=context)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if not urllib.request.getproxies():
                raise
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            response = opener.open(url, data=data, timeout=timeout)
            with _LOCK:
                _DIRECT_HOSTS.add(host)
            logger.warning("network proxy fallback: host=%s error=%s transport=direct",
                           host, type(exc).__name__)
            return response

    open_url._qt_network_failover = True  # type: ignore[attr-defined]
    urllib.request.urlopen = open_url  # type: ignore[assignment]
    _ORIGINAL_URLOPEN = urlopen
    _INSTALLED = True


def reset_for_tests() -> None:
    """Restore the Requests method for isolated tests."""
    global _INSTALLED, _ORIGINAL_SEND, _ORIGINAL_URLOPEN
    if _INSTALLED and _ORIGINAL_SEND is not None:
        requests.Session.send = _ORIGINAL_SEND  # type: ignore[method-assign]
    if _INSTALLED and _ORIGINAL_URLOPEN is not None:
        urllib.request.urlopen = _ORIGINAL_URLOPEN  # type: ignore[assignment]
    _DIRECT_HOSTS.clear()
    _ORIGINAL_SEND = None
    _ORIGINAL_URLOPEN = None
    _INSTALLED = False
