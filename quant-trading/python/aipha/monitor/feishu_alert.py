"""飞书告警 — lark-cli Bot 优先 + Webhook 降级

供 cron 脚本 / paper engine 直接调用的同步飞书告警函数。
优先使用 lark-cli（飞书官方 CLI）以 Bot 身份发送交互式卡片；
若 lark-cli 不可用则降级为 Webhook URL 模式（urllib，零额外依赖）。

环境变量:
    FEISHU_CHAT_ID: 飞书群聊 chat_id（lark-cli 模式必填，oc_xxx 格式）
    FEISHU_WEBHOOK_URL: 飞书自定义机器人 Webhook 地址（降级模式）
    FEISHU_WEBHOOK_SECRET: Webhook 签名密钥（可选）
    FEISHU_WEBHOOK_PROD: 生产环境专用 Webhook（优先于 FEISHU_WEBHOOK_URL）
    FEISHU_WEBHOOK_DEV: 开发环境专用 Webhook（优先于 FEISHU_WEBHOOK_URL）
    FEISHU_CHAT_ID_PROD: 生产环境专用 chat_id（优先于 FEISHU_CHAT_ID）
    FEISHU_CHAT_ID_DEV: 开发环境专用 chat_id（优先于 FEISHU_CHAT_ID）

双环境路由 (§13.14 / WF-N):
    通过 system_settings.json 的 ``environment`` 字段（prod / dev）或环境变量
    ``AIPHA_ENVIRONMENT`` 决定路由目标。开发环境推送标题自动带 [DEV] 前缀。

用法::

    from aipha.monitor.feishu_alert import send_feishu_alert
    send_feishu_alert("标题", "内容", level="CRITICAL")

告警去重: 相同 (title, level) 组合在 DEDUP_WINDOW 内不重复发送。
跨平台: Windows / CentOS 均可使用。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from typing import Any
from aipha.utils.paths import PROJECT_ROOT, project_path

logger = logging.getLogger(__name__)

# 去重窗口（秒）
DEDUP_WINDOW = 3600  # 1 小时内相同告警不重发
_dedup_cache: dict[str, float] = {}

# lark-cli 可执行文件路径缓存
_lark_cli_path: str | None = None
_lark_cli_resolved: bool = False

# Windows: node.exe 直调命令缓存（绕过 .cmd wrapper）
_lark_cli_node_cmd: list[str] | None = None
_lark_cli_node_resolved: bool = False

# 级别 → 飞书卡片颜色
_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "grey",
    "INFO": "blue",
    "WARNING": "orange",
    "ERROR": "red",
    "CRITICAL": "red",
}

# 级别 → emoji
_LEVEL_EMOJI: dict[str, str] = {
    "DEBUG": "\U0001f50d",
    "INFO": "\u2139\ufe0f",
    "WARNING": "\u26a0\ufe0f",
    "ERROR": "\u274c",
    "CRITICAL": "\U0001f6a8",
}

# =========================================================================
# 双环境路由 (§13.14 / WF-N)
# =========================================================================

# 缓存 — 仅解析一次
_resolved_env: str | None = None  # "prod" / "dev" / None
_resolved_env_done: bool = False


def _resolve_environment() -> str:
    """解析当前运行环境（prod / dev），结果缓存。

    解析优先级:
      1. 环境变量 ``AIPHA_ENVIRONMENT``（最高优先级）
      2. ``configs/paper/system_settings.json`` 的 ``environment`` 字段
      3. 默认 ``"dev"``（安全兜底：未配置视为开发环境，避免污染生产群）
    """
    global _resolved_env, _resolved_env_done
    if _resolved_env_done:
        return _resolved_env or "dev"

    env = os.environ.get("AIPHA_ENVIRONMENT", "").strip().lower()
    if env in ("prod", "dev"):
        _resolved_env = env
        _resolved_env_done = True
        return env

    # 从 system_settings.json 读取
    try:
        settings_path = (
            PROJECT_ROOT
            / "configs" / "paper" / "system_settings.json"
        )
        if settings_path.exists():
            with open(settings_path, encoding="utf-8") as f:
                data = json.load(f)
            env = str(data.get("environment", "")).strip().lower()
            if env in ("prod", "dev"):
                _resolved_env = env
                _resolved_env_done = True
                return env
    except Exception:
        pass

    _resolved_env = "dev"
    _resolved_env_done = True
    return "dev"


def get_environment() -> str:
    """返回当前运行环境标识 ('prod' / 'dev')."""
    return _resolve_environment()


def _get_env_webhook_url() -> str:
    """按环境路由获取 Webhook URL。

    优先级: FEISHU_WEBHOOK_{PROD|DEV} → FEISHU_WEBHOOK_URL → 空字符串
    """
    env = _resolve_environment()
    if env == "prod":
        url = os.environ.get("FEISHU_WEBHOOK_PROD", "")
        if url:
            return url
    else:
        url = os.environ.get("FEISHU_WEBHOOK_DEV", "")
        if url:
            return url
    return os.environ.get("FEISHU_WEBHOOK_URL", "")


def _get_env_chat_id() -> str:
    """按环境路由获取 chat_id。

    优先级: FEISHU_CHAT_ID_{PROD|DEV} → FEISHU_CHAT_ID → 空字符串
    """
    env = _resolve_environment()
    if env == "prod":
        cid = os.environ.get("FEISHU_CHAT_ID_PROD", "")
        if cid:
            return cid
    else:
        cid = os.environ.get("FEISHU_CHAT_ID_DEV", "")
        if cid:
            return cid
    return os.environ.get("FEISHU_CHAT_ID", "")


def _env_prefix_title(title: str) -> str:
    """开发环境标题加 [DEV] 前缀，生产环境不加。"""
    env = _resolve_environment()
    if env != "prod" and not title.startswith("[DEV]"):
        return f"[DEV] {title}"
    return title


def reset_environment_cache() -> None:
    """重置环境缓存（仅用于测试）。"""
    global _resolved_env, _resolved_env_done
    _resolved_env = None
    _resolved_env_done = False


# =========================================================================
# lark-cli 路径解析
# =========================================================================
def _resolve_lark_cli() -> str | None:
    """查找 lark-cli 可执行文件，缓存结果。"""
    global _lark_cli_path, _lark_cli_resolved  # noqa: PLW0603
    if _lark_cli_resolved:
        return _lark_cli_path
    _lark_cli_resolved = True
    for name in ("lark-cli.cmd", "lark-cli"):
        p = shutil.which(name)
        if p:
            _lark_cli_path = p
            return _lark_cli_path
    return None


def _parse_cmd_for_node(cmd_path: str) -> tuple[str, str] | None:
    """从 Windows .cmd 包装器中提取 (node_exe, js_script) 路径。

    npm 安装的 .cmd 文件通过 cmd.exe 执行，会破坏 JSON 参数中的特殊字符。
    解析 .cmd 内容找到实际的 node.js 入口点，以便绕过 cmd.exe 直调。
    """
    try:
        cmd_dir = os.path.dirname(os.path.abspath(cmd_path))
        with open(cmd_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # 匹配 JS 入口: "%dp0%\node_modules\...\run.js" 或 %~dp0 变体
        match = re.search(
            r'["\']?%(?:~dp0|dp0%)[\\\/]([^"\'%\s]+\.js)["\']?',
            content, re.IGNORECASE,
        )
        if not match:
            return None
        js_rel = match.group(1).replace("\\", os.sep).replace("/", os.sep)
        js_path = os.path.join(cmd_dir, js_rel)
        if not os.path.isfile(js_path):
            return None
        # 查找 node.exe
        local_node = os.path.join(cmd_dir, "node.exe")
        node_exe = (
            local_node if os.path.isfile(local_node)
            else shutil.which("node")
        )
        if not node_exe:
            return None
        return (node_exe, js_path)
    except Exception:
        return None


def _get_lark_cli_cmd() -> list[str] | None:
    """获取 lark-cli 命令列表，Windows 上绕过 .cmd 直调 node.exe。"""
    global _lark_cli_node_cmd, _lark_cli_node_resolved  # noqa: PLW0603
    if _lark_cli_node_resolved:
        return _lark_cli_node_cmd
    _lark_cli_node_resolved = True
    cli = _resolve_lark_cli()
    if not cli:
        return None
    if sys.platform == "win32" and cli.lower().endswith((".cmd", ".bat")):
        pair = _parse_cmd_for_node(cli)
        if pair:
            _lark_cli_node_cmd = [pair[0], pair[1]]
            logger.debug("lark-cli 解析为 node 直调: %s", _lark_cli_node_cmd)
            return _lark_cli_node_cmd
        logger.warning("无法解析 .cmd 包装器, 将使用原始路径: %s", cli)
    _lark_cli_node_cmd = [cli]
    return _lark_cli_node_cmd


def _get_signed_url(
    webhook_url: str, secret: str,
) -> tuple[str, str | None, str | None]:
    """构造飞书签名参数。

    Returns:
        (webhook_url, timestamp, sign)。无密钥时 timestamp/sign 为 None。
    """
    if not secret:
        return webhook_url, None, None
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return webhook_url, timestamp, sign


def _build_card_payload(
    title: str, content: str, level: str,
    timestamp_str: str | None = None,
    sign: str | None = None,
) -> dict[str, Any]:
    """构造飞书 Interactive Card 消息体。"""
    emoji = _LEVEL_EMOJI.get(level, "\U0001f4e2")
    color = _LEVEL_COLORS.get(level, "blue")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_content = (
        f"{emoji} **{title}**\n"
        f"时间: {now_str}\n"
        f"级别: {level}\n\n"
        f"{content}"
    )

    payload: dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"{emoji} {title}",
                },
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": md_content},
            ],
        },
    }
    if timestamp_str and sign:
        payload["timestamp"] = timestamp_str
        payload["sign"] = sign
    return payload


# =========================================================================
# lark-cli 发送（优先通道）
# =========================================================================
def _build_card_json(title: str, content: str, level: str) -> str:
    """构造 lark-cli --msg-type interactive 所需的纯卡片 JSON。"""
    emoji = _LEVEL_EMOJI.get(level, "\U0001f4e2")
    color = _LEVEL_COLORS.get(level, "blue")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md_content = (
        f"**系统**: Aipha-Q 量化交易\n"
        f"**级别**: {level}\n"
        f"**时间**: {now_str}\n\n"
        f"{content}"
    )

    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{emoji} {title}"},
            "template": color,
        },
        "elements": [
            {"tag": "markdown", "content": md_content},
        ],
    }
    return json.dumps(card, ensure_ascii=False)


def _send_via_lark_cli(
    title: str, content: str, level: str, chat_id: str,
    *, timeout: float = 15.0,
) -> bool:
    """通过 lark-cli 以 Bot 身份发送交互式卡片。

    Windows 上通过 _get_lark_cli_cmd() 绕过 .cmd 包装器直调 node.exe,
    避免 cmd.exe 破坏 JSON 参数中的特殊字符 (**, {}, ¥ 等)。
    """
    cmd = _get_lark_cli_cmd()
    if not cmd:
        return False
    card_json = _build_card_json(title, content, level)
    try:
        # 卡片模式: 直接传 JSON 字符串作为 --content 参数
        proc = subprocess.run(
            cmd + ["im", "+messages-send",
                   "--chat-id", chat_id,
                   "--msg-type", "interactive",
                   "--content", card_json],
            capture_output=True, text=True,
            timeout=timeout,
        )
        if proc.returncode == 0:
            try:
                resp = json.loads(proc.stdout)
                if resp.get("ok"):
                    logger.info("飞书告警(lark-cli card)发送成功: %s", title)
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
            logger.warning("飞书告警(lark-cli card)API错误: %s",
                           proc.stdout[:200])
        else:
            logger.warning("飞书告警(lark-cli card)退出码%d: %s",
                           proc.returncode,
                           (proc.stderr or proc.stdout)[:200])

        # 卡片失败时降级为纯文本
        text = f"{_LEVEL_EMOJI.get(level, '')} {title}\n{content}"
        proc2 = subprocess.run(
            cmd + ["im", "+messages-send",
                   "--chat-id", chat_id, "--text", text],
            capture_output=True, text=True,
            timeout=timeout,
        )
        if proc2.returncode == 0:
            logger.info("飞书告警(lark-cli text)降级成功: %s", title)
            return True
        logger.warning("飞书告警(lark-cli text)退出码%d: %s",
                       proc2.returncode,
                       (proc2.stderr or proc2.stdout)[:200])
        return False
    except subprocess.TimeoutExpired:
        logger.warning("飞书告警(lark-cli)超时(%.0fs)", timeout)
        return False
    except Exception as exc:
        logger.warning("飞书告警(lark-cli)异常: %s", exc)
        return False


# =========================================================================
# 告警审计日志 — 本地 JSONL 持久化 (§13.13 告警即生命线)
# =========================================================================
_ALERT_AUDIT_DIR = project_path("logs", "audit")


def _log_alert_audit(
    title: str, content: str, level: str,
    *, success: bool, channel: str, error: str = "",
) -> None:
    """将告警记录写入本地 JSONL 审计文件。

    每日一个文件: logs/audit/feishu_alerts_YYYYMMDD.jsonl
    此函数绝不抛出异常，避免干扰告警发送主流程。
    """
    try:
        _ALERT_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = _ALERT_AUDIT_DIR / f"feishu_alerts_{today}.jsonl"
        record = {
            "ts": datetime.now().isoformat(),
            "title": title,
            "level": level,
            "content": content[:500],
            "ok": success,
            "channel": channel,
            "error": error[:300] if error else "",
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 审计日志不得阻断告警发送


def send_feishu_alert(
    title: str,
    content: str,
    *,
    level: str = "WARNING",
    chat_id: str | None = None,
    webhook_url: str | None = None,
    secret: str | None = None,
    dedup: bool = True,
    timeout: float = 15.0,
) -> bool:
    """发送飞书告警 — lark-cli 优先，Webhook 降级。

    Args:
        title: 告警标题
        content: 告警内容（支持 Markdown）
        level: 级别 DEBUG/INFO/WARNING/ERROR/CRITICAL
        chat_id: 飞书群聊 chat_id（lark-cli 模式，默认从环境变量读取）
        webhook_url: 飞书 Webhook URL（降级模式，默认从环境变量读取）
        secret: Webhook 签名密钥（默认从环境变量读取）
        dedup: 是否启用告警去重
        timeout: 超时秒数

    Returns:
        是否发送成功
    """
    cid = chat_id or _get_env_chat_id()
    url = webhook_url or _get_env_webhook_url()

    # WF-N: 开发环境自动加 [DEV] 前缀
    title = _env_prefix_title(title)

    if not cid and not url:
        logger.warning(
            "FEISHU_CHAT_ID / FEISHU_WEBHOOK_URL 均未配置，飞书告警跳过。"
            " 请在 .env / .env.paper 中至少配置一项。"
        )
        _log_alert_audit(title, content, level,
                         success=False, channel="none",
                         error="FEISHU_CHAT_ID/FEISHU_WEBHOOK_URL 未配置")
        return False

    # 去重检查
    if dedup:
        dedup_key = f"{title}|{level}"
        now = time.time()
        last_sent = _dedup_cache.get(dedup_key, 0.0)
        if now - last_sent < DEDUP_WINDOW:
            logger.debug("告警去重: %s (距上次 %.0fs)", dedup_key, now - last_sent)
            return False
        _dedup_cache[dedup_key] = now
        # 清理过期缓存
        expired = [k for k, v in _dedup_cache.items() if now - v > DEDUP_WINDOW * 2]
        for k in expired:
            _dedup_cache.pop(k, None)

    # 1) lark-cli（优先）
    if cid and _resolve_lark_cli():
        if _send_via_lark_cli(title, content, level, cid, timeout=timeout):
            _log_alert_audit(title, content, level,
                             success=True, channel="lark-cli")
            return True
        logger.info("lark-cli 失败，尝试 webhook 降级")

    # 2) Webhook（降级）
    if url:
        ok = _send_via_webhook(
            title, content, level, url,
            secret=secret, timeout=timeout,
        )
        _log_alert_audit(title, content, level,
                         success=ok,
                         channel="webhook",
                         error="" if ok else "webhook 发送失败")
        return ok

    _log_alert_audit(title, content, level,
                     success=False, channel="all-failed",
                     error="所有通道均失败")
    return False


def _send_via_webhook(
    title: str, content: str, level: str, url: str,
    *, secret: str | None = None, timeout: float = 10.0,
) -> bool:
    """通过 Webhook URL 发送飞书告警（降级通道）。"""
    sec = secret or os.environ.get("FEISHU_WEBHOOK_SECRET", "")

    # 确保消息包含安全关键词（自定义机器人校验）
    _KEYWORD = "Aipha"
    if _KEYWORD not in title and _KEYWORD not in content:
        title = f"[Aipha-Q] {title}"

    # 构造签名
    _, ts_str, sign_str = _get_signed_url(url, sec)

    payload = _build_card_payload(title, content, level, ts_str, sign_str)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            code = body.get("code", body.get("StatusCode", -1))
            if code == 0:
                logger.info("飞书告警(webhook)发送成功: %s", title)
                return True
            else:
                logger.error("飞书告警(webhook)失败: %s", body)
                return False
    except Exception as exc:
        logger.warning("飞书告警(webhook)发送异常: %s", exc)
        return False


# =========================================================================
# MDD 告警推送 — 统一入口 (§13.2 / WF-11c)
# =========================================================================

def notify_mdd_alert(
    profile: str,
    trade_date: str,
    *,
    nav: float,
    peak_nav: float,
    mdd_status: str,
    alert_threshold: float = 0.15,
    halt_threshold: float = 0.30,
) -> bool:
    """MDD 告警统一推送入口。

    所有 Paper/Live 路径的 MDD 告警必须通过此函数推送，
    禁止在各脚本中自建 MDD 告警格式。

    Args:
        profile: 组合名称
        trade_date: 交易日期 YYYYMMDD
        nav: 当前 NAV
        peak_nav: 历史峰值 NAV
        mdd_status: "alert" 或 "halt"
        alert_threshold: 告警阈值
        halt_threshold: 熔断阈值

    Returns:
        是否发送成功
    """
    dd = 1.0 - nav / peak_nav if peak_nav > 0 else 0.0

    if mdd_status == "halt":
        level = "CRITICAL"
        title = f"🚨 MDD 熔断 | {profile}"
        content = (
            f"**交易日**: {trade_date}\n"
            f"**MDD**: {dd:.2%} ≥ 熔断线 {halt_threshold:.0%}\n"
            f"**NAV**: {nav:,.2f} | **Peak**: {peak_nav:,.2f}\n"
            f"**动作**: 暂停所有调仓，等待人工决策"
        )
    elif mdd_status == "alert":
        level = "WARNING"
        title = f"⚠️ MDD 告警 | {profile}"
        content = (
            f"**交易日**: {trade_date}\n"
            f"**MDD**: {dd:.2%} ≥ 告警线 {alert_threshold:.0%}\n"
            f"**NAV**: {nav:,.2f} | **Peak**: {peak_nav:,.2f}\n"
            f"**动作**: 继续交易，密切监控"
        )
    else:
        return False

    return send_feishu_alert(title, content, level=level)


# =========================================================================
# 交易日报推送 — 统一入口 (§13.2 单一实现)
# =========================================================================

def send_trade_daily_report(
    profile: str,
    trade_date: str,
    *,
    nav: float,
    pre_trade_nav: float | None = None,
    cash: float,
    positions: list[dict],
    trades: list[dict],
    peak_nav: float = 0.0,
) -> bool:
    """推送结构化交易日报到飞书 — 所有 paper 脚本的唯一调用入口.

    Args:
        profile: 组合名称
        trade_date: 交易日期 YYYYMMDD
        nav: 日终 NAV
        pre_trade_nav: 调仓前 NAV（有则显示涨跌幅）
        cash: 日终现金
        positions: 持仓列表 [{code, shares, component, ...}]
        trades: 今日成交 [{date, code, side, shares, price, amount, cost, component}]
        peak_nav: 历史最高 NAV（计算回撤用）
    """
    today_trades = [t for t in trades if t.get("date") == trade_date]

    # ── 构造卡片内容 ──
    lines: list[str] = []

    # 概览行
    nav_str = f"**NAV**: {nav:,.0f}"
    if pre_trade_nav and pre_trade_nav > 0:
        chg = (nav / pre_trade_nav - 1) * 100
        emoji = "📈" if chg >= 0 else "📉"
        nav_str += f" ({emoji}{chg:+.2f}%)"
    lines.append(nav_str)

    # 回撤
    if peak_nav > 0:
        dd = (1 - nav / peak_nav) * 100
        dd_str = f"回撤 {dd:.1f}%" if dd > 0.1 else "无回撤"
        lines.append(
            f"**现金**: {cash:,.0f}  |  "
            f"**持仓**: {len(positions)} 只  |  {dd_str}"
        )
    else:
        lines.append(
            f"**现金**: {cash:,.0f}  |  **持仓**: {len(positions)} 只"
        )

    # 持仓分布
    comp_pos: dict[str, int] = {}
    for p in positions:
        c = p.get("component", "?")
        comp_pos[c] = comp_pos.get(c, 0) + 1
    if comp_pos:
        dist = "  ".join(f"{k}:{v}" for k, v in sorted(comp_pos.items()))
        lines.append(f"**分布**: {dist}")

    lines.append("")  # 空行分隔

    if not today_trades:
        lines.append("今日无交易")
    else:
        buys = [t for t in today_trades if t["side"] == "buy"]
        sells = [t for t in today_trades if t["side"] == "sell"]
        total_cost = sum(t.get("cost", 0) for t in today_trades)
        buy_amt = sum(t.get("amount", 0) for t in buys)
        sell_amt = sum(t.get("amount", 0) for t in sells)

        lines.append(
            f"🟢买 {len(buys)} 笔 ¥{buy_amt:,.0f}  "
            f"🔴卖 {len(sells)} 笔 ¥{sell_amt:,.0f}  "
            f"💰成本 ¥{total_cost:,.2f}"
        )

        # 按组件汇总
        comp_map: dict[str, dict] = {}
        for t in today_trades:
            c = t.get("component", "?")
            if c not in comp_map:
                comp_map[c] = {"b": 0, "s": 0, "amt": 0.0}
            comp_map[c]["b" if t["side"] == "buy" else "s"] += 1
            comp_map[c]["amt"] += t.get("amount", 0)

        for c, info in sorted(comp_map.items()):
            lines.append(
                f"  📌 **{c}**: 买{info['b']} 卖{info['s']} "
                f"¥{info['amt']:,.0f}"
            )

        # 逐笔明细（前 15 笔）
        lines.append("")
        for t in today_trades[:15]:
            e = "🟢" if t["side"] == "buy" else "🔴"
            amt = t.get("amount", 0)
            lines.append(
                f"{e} {t['code']} ×{t['shares']} "
                f"@{t['price']:.3f} ¥{amt:,.0f} "
                f"[{t.get('component', '')}]"
            )
        if len(today_trades) > 15:
            lines.append(f"... 共 {len(today_trades)} 笔")

    title = f"[Daily] Aipha-Q {profile} {trade_date}"
    body = "\n".join(lines)

    return send_feishu_alert(title, body, level="INFO", dedup=False)


# =========================================================================
# CLI 测试入口 — python -m aipha.monitor.feishu_alert
# =========================================================================
def _cli_test() -> None:
    """发送测试消息验证飞书告警链路。

    Usage::

        python -m aipha.monitor.feishu_alert          # 简单测试
        python -m aipha.monitor.feishu_alert --card    # 日报卡片测试
    """
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    # Windows GBK 终端兼容
    if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    cid = os.environ.get("FEISHU_CHAT_ID", "")
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    print(f"FEISHU_CHAT_ID  = {cid[:15]}..." if cid else "FEISHU_CHAT_ID  = (未配置)")
    print(f"FEISHU_WEBHOOK_URL = {url[:30]}..." if url else "FEISHU_WEBHOOK_URL = (未配置)")
    print(f"lark-cli = {_resolve_lark_cli() or '(未找到)'}")
    print()

    if "--card" in sys.argv:
        from datetime import datetime as _dt

        td = _dt.now().strftime("%Y%m%d")
        ok = send_trade_daily_report(
            "TEST", td,
            nav=1_234_567.89,
            pre_trade_nav=1_220_000.0,
            cash=234_567.89,
            positions=[
                {"code": "128053.SZ", "shares": 100, "component": "CB"},
                {"code": "000001.SZ", "shares": 200, "component": "ML"},
            ],
            trades=[
                {"date": td, "code": "128053.SZ", "side": "buy",
                 "shares": 100, "price": 125.0, "amount": 12500,
                 "cost": 5.0, "component": "CB"},
                {"date": td, "code": "000001.SZ", "side": "sell",
                 "shares": 200, "price": 15.5, "amount": 3100,
                 "cost": 3.2, "component": "ML"},
            ],
            peak_nav=1_250_000.0,
        )
    else:
        ok = send_feishu_alert(
            "🔧 Aipha-Q 告警测试",
            "这是一条**测试消息**，验证飞书告警链路是否畅通。\n\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "如果你看到此消息，说明配置正确 ✅",
            level="INFO",
            dedup=False,
        )

    print(f"\n发送结果: {'✅ 成功' if ok else '❌ 失败'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    _cli_test()
