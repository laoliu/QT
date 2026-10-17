"""aipha.utils.paths —— 项目根定位与配置路径锚定。

这个模块守的是一整个失效家族：**路径静默指错**。它不抛异常，只是从此
读到一个空目录 / 把文件写进另一棵树，所以只能靠测试钉住。

两个来源要分开看：

* ``project_path()`` 管**代码里写死的兜底值**（``Path("data/cache")`` 这类）；
* ``resolve_path()`` 管**配置/环境变量传进来的值** —— ``configs/backtest/*.yml``
  里写的是 ``cache_dir: data/cache/tushare`` 这种仓库相对路径，直接
  ``Path(...)`` 就又回到吃当前工作目录了。

见 aipha/utils/paths.py 的模块 docstring（Kill Switch 曾因此永久失效）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from aipha.utils.paths import (
    CONFIGS_DIR,
    DATA_DIR,
    KILL_SWITCH_PATH,
    LOGS_DIR,
    PROJECT_ROOT,
    project_path,
    resolve_path,
    runtime_project_path,
)


class TestProjectRoot:
    """项目根必须是「带 configs/ 的那一层」，而不是它的上一级。"""

    def test_root_is_absolute(self):
        assert PROJECT_ROOT.is_absolute()

    def test_root_contains_configs(self):
        """认路标而不是数层数 —— configs/ 就是那个路标。"""
        assert (PROJECT_ROOT / "configs").is_dir(), (
            f"项目根定位错误: {PROJECT_ROOT} 下没有 configs/"
        )

    def test_root_contains_package(self):
        assert (PROJECT_ROOT / "aipha" / "__init__.py").is_file()

    def test_constants_all_under_root(self):
        for p in (CONFIGS_DIR, LOGS_DIR, DATA_DIR, KILL_SWITCH_PATH):
            assert p.is_absolute()
            assert PROJECT_ROOT in p.parents, f"{p} 不在项目根下"

    def test_kill_switch_in_configs(self):
        """§14.2 L3：读方写方必须是同一个常量、同一个目录。"""
        assert KILL_SWITCH_PATH.parent == CONFIGS_DIR
        assert KILL_SWITCH_PATH.name == "kill_switch.flag"


class TestProjectPath:
    def test_joins_under_root(self):
        assert project_path("configs", "x.yml") == PROJECT_ROOT / "configs" / "x.yml"

    def test_no_args_is_root(self):
        assert project_path() == PROJECT_ROOT

    def test_runtime_path_isolated_only_during_pytest(self, monkeypatch):
        monkeypatch.delenv("AIPHA_TEST_RUNTIME", raising=False)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/utils/test_paths.py::runtime")
        isolated = runtime_project_path("logs", "audit")
        assert "aipha-pytest-runtime" in isolated.parts
        monkeypatch.delenv("PYTEST_CURRENT_TEST")
        assert runtime_project_path("logs", "audit") == PROJECT_ROOT / "logs" / "audit"

    def test_runtime_path_uses_inherited_session_marker(self, monkeypatch):
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("AIPHA_TEST_RUNTIME", "1")
        isolated = runtime_project_path("logs", "audit")
        assert "aipha-pytest-runtime" in isolated.parts

    def test_runtime_path_marker_is_inherited_by_child_process(self):
        env = os.environ.copy()
        env.pop("PYTEST_CURRENT_TEST", None)
        code = (
            "from aipha.utils.paths import runtime_project_path; "
            "print(runtime_project_path('logs', 'audit'))"
        )
        child_path = subprocess.check_output(
            [sys.executable, "-c", code], env=env, text=True,
        ).strip()
        assert "aipha-pytest-runtime" in Path(child_path).parts


class TestResolvePath:
    """配置值锚定：相对的锚到项目根，绝对的原样放行。"""

    def test_relative_is_anchored(self):
        assert resolve_path("data/cache/tushare") == (
            PROJECT_ROOT / "data" / "cache" / "tushare"
        )

    def test_nested_relative_is_anchored(self):
        assert resolve_path("logs/paper").is_absolute()

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_falls_back_to_default(self, value):
        """配置没写 → 用兜底值拼项目根，而不是返回项目根本身。"""
        assert resolve_path(value, "data", "paper_state") == (
            PROJECT_ROOT / "data" / "paper_state"
        )

    def test_absolute_passes_through(self):
        """容器挂载 /data、开发机指 G:/qtdata，都不能被重新锚定。"""
        if sys.platform == "win32":
            assert resolve_path("G:/qtdata/cache") == Path("G:/qtdata/cache")
        else:
            assert resolve_path("/mnt/data/cache") == Path("/mnt/data/cache")

    def test_rooted_without_drive_passes_through(self):
        """Windows 上 ``Path("/custom/logs")`` 的 is_absolute() 是 False。

        只判 is_absolute() 会把用户显式写的根路径拼到项目根后面，得到
        ``E:/custom/logs``。带 root 就必须当绝对处理。
        """
        assert resolve_path("/custom/logs") == Path("/custom/logs")

    def test_accepts_path_object(self):
        assert resolve_path(Path("data/cache")) == PROJECT_ROOT / "data" / "cache"


class TestCwdIndependence:
    """真正的验收条件：换个工作目录，解析结果不变。

    前面那些断言都在 pytest 的 rootdir 下跑，而 rootdir 恰好就是项目根 ——
    相对路径在这里「碰巧是对的」。所以必须另起一个进程、从别的目录启动，
    才能真的把 CWD 依赖照出来。
    """

    @staticmethod
    def _probe(cwd: Path) -> dict[str, str]:
        # 关键：报告 resolve() 之后的绝对路径，而不是原样 str()。
        # 相对路径 Path("logs") 在任何目录下 str() 都是 "logs" —— 直接比
        # 字符串是比不出 CWD 依赖的，必须先落地成绝对路径才会分叉。
        code = (
            "import json;"
            "from pathlib import Path;"
            "from aipha.utils.paths import PROJECT_ROOT, resolve_path;"
            "from aipha.config import get_cache_dir, get_log_dir;"
            "from aipha.monitor.metrics_collector import MetricsConfig;"
            "f=lambda p: str(Path(p).resolve());"
            "print(json.dumps({"
            "  'root': f(PROJECT_ROOT),"
            "  'rel': f(resolve_path('data/cache')),"
            "  'log': f(get_log_dir()),"
            "  'cache': f(get_cache_dir()),"
            "  'metrics': f(MetricsConfig().output_dir),"
            "}))"
        )
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        # 这几个会改变预期结果，清掉以免开发机上的设置干扰
        for k in ("LOG_DIR", "DATA_CACHE_DIR", "QTDATA_DIR"):
            env.pop(k, None)
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, f"子进程失败:\n{out.stderr}"
        import json

        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_same_result_from_unrelated_cwd(self, tmp_path):
        from_root = self._probe(PROJECT_ROOT)
        from_elsewhere = self._probe(tmp_path)
        assert from_root == from_elsewhere, (
            "换工作目录后路径解析结果变了 —— 说明仍有 CWD 相对路径:\n"
            f"  从项目根: {from_root}\n"
            f"  从 {tmp_path}: {from_elsewhere}"
        )

    def test_paths_do_not_leak_into_cwd(self, tmp_path):
        """从无关目录启动时，解析出来的路径不能落在那个目录下。"""
        got = self._probe(tmp_path)
        for key, value in got.items():
            if key == "root":
                continue
            assert not str(value).startswith(str(tmp_path)), (
                f"{key} 指向了当前工作目录: {value}"
            )
