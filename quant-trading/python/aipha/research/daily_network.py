"""Process-local proxy failover for the unattended TuShare worker."""
from functools import partial
import os

import requests
from requests.utils import get_environ_proxies, select_proxy


class ProxyFallbackApi:
    """Retry a failed proxy request once without a proxy, then stay direct."""
    def __init__(self, api, *, on_fallback=None):
        self._api = api
        self._on_fallback = on_fallback
        self._url = getattr(api, "_DataApi__http_url", "http://api.waditu.com/dataapi")
        self._direct = False

    def query(self, api_name, fields="", **kwargs):
        proxy = None if self._direct else select_proxy(self._url, get_environ_proxies(self._url))
        try:
            return self._api.query(api_name, fields=fields, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if not proxy:
                raise
            self._direct = True
            os.environ["NO_PROXY"] = "*"
            os.environ["no_proxy"] = "*"
            if self._on_fallback is not None:
                self._on_fallback(api_name, type(exc).__name__)
            return self._api.query(api_name, fields=fields, **kwargs)

    def __getattr__(self, endpoint):
        return partial(self.query, endpoint)


def configure_proxy_fallback(*, on_fallback=None):
    from aipha.data.tushare_gateway import get_tushare_gateway
    gateway = get_tushare_gateway()
    if not isinstance(gateway.api, ProxyFallbackApi):
        gateway._api = ProxyFallbackApi(gateway.api, on_fallback=on_fallback)
