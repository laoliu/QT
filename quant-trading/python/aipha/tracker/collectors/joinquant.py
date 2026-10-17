from .base import LibraryCollector


class JoinquantCollector(LibraryCollector):
    platform_name = "joinquant"
    profile_template = "https://www.joinquant.com/user/{uid}"
