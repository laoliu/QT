from .base import LibraryCollector


class BigquantCollector(LibraryCollector):
    platform_name = "bigquant"
    profile_template = "https://bigquant.com/community/u/{uid}"
