"""tests/api/ 共享夹具。

``create_app()`` 每调用一次，FastAPI 就要为全部路由重建一遍 pydantic
校验模型。单独跑 ``tests/api`` 没问题，但在全量套件的同一个进程里
（此时所有测试模块都已在收集期 import 完，C 栈余量小得多），第四次
调用会在 pydantic-core 递归建 schema 时踩爆 Windows 的 1MB 线程栈：

    Windows fatal exception: access violation
      File "pydantic/_internal/_config.py", line ??? in core_config
      File "aipha/api/app.py", line 302 in create_app
      File "tests/api/test_system_trading_router.py", line 260 in ...

整个套件因此在 2% 处 segfault，必须拆成多条命令才能跑完。

改成会话级只建一次即可绕开，而且本来也该这么写 —— 建 app 是纯开销。
共用是安全的：app 不持有 store，每次请求经 ``deps.get_result_store()``
解析，而各用例在发请求前都会重绑 ``deps._DB_PATH`` 并 ``cache_clear()``。
"""
from __future__ import annotations

import pytest

from aipha.api.app import create_app


@pytest.fixture(scope="session")
def app():
    """全会话共用的 FastAPI 应用实例。"""
    return create_app()
