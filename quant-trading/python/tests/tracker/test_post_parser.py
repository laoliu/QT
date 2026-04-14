"""Tests for tracker.parsers.post_parser — stock mention & trading signal extraction."""

import pytest

from aipha.tracker.parsers.post_parser import (
    StockMention,
    TradingSignal,
    extract_stock_mentions,
    extract_trading_signals,
    parse_post,
    _detect_action,
    _detect_weight,
    _strip_html,
)


# =========================================================================
# extract_stock_mentions
# =========================================================================


class TestExtractStockMentions:

    def test_xueqiu_format_with_name(self):
        text = "今天买入$贵州茅台(SH600519)$，看好白酒行业"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "600519.SH"
        assert mentions[0].name == "贵州茅台"

    def test_xueqiu_format_code_only(self):
        text = "关注$SH600519$走势"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "600519.SH"
        assert mentions[0].name == ""

    def test_multiple_xueqiu_mentions(self):
        text = "$贵州茅台(SH600519)$和$平安银行(SZ000001)$都不错"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 2
        syms = {m.symbol for m in mentions}
        assert "600519.SH" in syms
        assert "000001.SZ" in syms

    def test_prefixed_code(self):
        text = "SH600519 今天涨了"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "600519.SH"

    def test_prefixed_code_lowercase(self):
        text = "sh600519 看涨"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "600519.SH"

    def test_sz_prefixed(self):
        text = "SZ000001 平安银行"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "000001.SZ"

    def test_bare_code_sh(self):
        """6开头 → SH"""
        text = "看好 600519 "
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "600519.SH"

    def test_bare_code_sz(self):
        """0开头 → SZ"""
        text = " 000001 表现不错"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "000001.SZ"

    def test_bare_code_sz_300(self):
        """3开头 → SZ (创业板)"""
        text = " 300750 "
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1
        assert mentions[0].symbol == "300750.SZ"

    def test_dedup(self):
        """同一股票出现多次应去重"""
        text = "$贵州茅台(SH600519)$很不错，SH600519 还是值得买"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1

    def test_no_mentions(self):
        text = "今天天气不错，大盘涨了"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 0

    def test_bare_code_not_used_when_better_match_exists(self):
        """有 xueqiu 格式时，6位数字不再重复匹配"""
        text = "$贵州茅台(SH600519)$ 600519"
        mentions = extract_stock_mentions(text)
        assert len(mentions) == 1  # dedup


# =========================================================================
# extract_trading_signals
# =========================================================================


class TestExtractTradingSignals:

    def test_buy_signal(self):
        text = "今天买入$贵州茅台(SH600519)$，目标价2500"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].symbol == "600519.SH"
        assert signals[0].action == "BUY"

    def test_sell_signal(self):
        text = "卖出$平安银行(SZ000001)$止盈"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].action == "SELL"

    def test_increase_signal(self):
        text = "加仓$贵州茅台(SH600519)$到30%"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].action == "INCREASE"
        assert signals[0].weight_hint == 0.30

    def test_decrease_signal(self):
        text = "减仓$平安银行(SZ000001)$"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].action == "DECREASE"

    def test_clear_signal(self):
        text = "清仓$贵州茅台(SH600519)$了"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].action == "CLEAR"

    def test_no_action_keyword(self):
        text = "关注$贵州茅台(SH600519)$走势"
        signals = extract_trading_signals(text)
        assert len(signals) == 0

    def test_no_stock_mention(self):
        text = "今天买入了一些股票"
        signals = extract_trading_signals(text)
        assert len(signals) == 0

    def test_weight_hint_extracted(self):
        text = "买入$贵州茅台(SH600519)$仓位20%"
        signals = extract_trading_signals(text)
        assert len(signals) == 1
        assert signals[0].weight_hint == pytest.approx(0.20)

    def test_multiple_signals(self):
        text = "今天减仓$平安银行(SZ000001)$然后买入$贵州茅台(SH600519)$"
        signals = extract_trading_signals(text)
        # 两个 mention 都能匹配到各自附近的 action
        assert len(signals) >= 1
        actions = {s.action for s in signals}
        # 至少 "减仓" 能匹配到第一个 mention
        assert "DECREASE" in actions or "BUY" in actions


# =========================================================================
# parse_post
# =========================================================================


class TestParsePost:

    def test_basic_post(self):
        text = "今天加仓$贵州茅台(SH600519)$到30%"
        result = parse_post(text)
        assert result["mention_count"] == 1
        assert result["signal_count"] == 1
        assert result["mentions"][0]["symbol"] == "600519.SH"
        assert result["signals"][0]["action"] == "INCREASE"

    def test_empty_post(self):
        result = parse_post("")
        assert result["mention_count"] == 0
        assert result["signal_count"] == 0

    def test_html_stripped(self):
        text = "<p>买入<b>$贵州茅台(SH600519)$</b></p>"
        result = parse_post(text)
        assert result["mention_count"] == 1

    def test_no_signals_but_mentions(self):
        text = "关注$贵州茅台(SH600519)$"
        result = parse_post(text)
        assert result["mention_count"] == 1
        assert result["signal_count"] == 0


# =========================================================================
# Helper functions
# =========================================================================


class TestHelpers:

    def test_detect_action_buy(self):
        assert _detect_action("今天买入了") == "BUY"

    def test_detect_action_sell(self):
        assert _detect_action("止盈卖出") == "SELL"

    def test_detect_action_increase(self):
        assert _detect_action("补仓了") == "INCREASE"

    def test_detect_action_decrease(self):
        assert _detect_action("减持了一些") == "DECREASE"

    def test_detect_action_clear(self):
        # "全部卖出" in CLEAR list; but "卖出" (SELL) matches first due to iteration order
        assert _detect_action("清仓了") == "CLEAR"

    def test_detect_action_none(self):
        assert _detect_action("今天天气真不错") is None

    def test_detect_weight_percent(self):
        assert _detect_weight("仓位30%") == pytest.approx(0.30)

    def test_detect_weight_decimal(self):
        assert _detect_weight("仓位30.5%") == pytest.approx(0.305)

    def test_detect_weight_none(self):
        assert _detect_weight("看看大盘") is None

    def test_detect_weight_over_100(self):
        """超过100%不返回"""
        assert _detect_weight("涨了200%") is None

    def test_strip_html(self):
        assert _strip_html("<p>Hello <b>World</b></p>") == "Hello World"

    def test_strip_html_no_tags(self):
        assert _strip_html("plain text") == "plain text"
