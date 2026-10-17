from .base import LibraryCollector


class JisiluCollector(LibraryCollector):
    platform_name = "jisilu"
    profile_template = "https://www.jisilu.cn/people/{uid}"
