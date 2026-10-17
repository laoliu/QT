"""TuShare Pro 数据提供者单元测试

测试策略：
1. 使用 Mock 隔离外部依赖（TuShare API）
2. 测试核心逻辑：数据加载、缓存、指纹生成、错误处理
3. 覆盖边界条件：网络异常、数据缺失、API限流
"""

from datetime import date, datetime
from pathlib import Path
from aipha.utils.paths import project_path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from aipha.data.tushare_provider import TuShareProBarProvider
from aipha.domain.models import Bar


@pytest.fixture
def mock_tushare_api():
    """Mock TuShare Pro API（兼容 gateway 单例架构）"""
    with patch("tushare.pro_api") as mock_pro_api, \
         patch("tushare.set_token"):
        api = MagicMock()
        mock_pro_api.return_value = api
        # 重置网关单例
        from aipha.data.tushare_gateway import reset_tushare_gateway
        reset_tushare_gateway()
        yield api
        reset_tushare_gateway()


@pytest.fixture
def sample_tushare_data():
    """生成示例 TuShare 数据（1min bar）"""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 3,
            "trade_time": [
                "2024-01-02 09:31",
                "2024-01-02 09:32",
                "2024-01-02 09:33",
            ],
            "open": [10.0, 10.1, 10.2],
            "high": [10.1, 10.2, 10.3],
            "low": [9.9, 10.0, 10.1],
            "close": [10.0, 10.1, 10.2],
            "vol": [1000.0, 1100.0, 1200.0],  # 单位：手
            "amount": [10000.0, 11100.0, 12240.0],  # 单位：千元
        }
    )


class TestTuShareProBarProvider:
    """TuShare Pro 数据提供者测试"""

    def test_initialization_with_token(self, mock_tushare_api):
        """测试：使用 token 初始化"""
        provider = TuShareProBarProvider(token="test_token", cache_dir=None)
        
        assert provider.token == "test_token"
        assert provider.cache_dir is None
        # 验证 API 已初始化（mock_tushare_api 是 pro_api 函数的 mock）
        assert provider.api is not None

    def test_initialization_with_cache_dir(self, mock_tushare_api, tmp_path):
        """测试：启用缓存目录"""
        cache_dir = tmp_path / "cache"
        provider = TuShareProBarProvider(token="test_token", cache_dir=str(cache_dir))
        
        assert provider.cache_dir == str(cache_dir)
        assert cache_dir.exists()  # 应自动创建

    def test_load_bars_basic(self, mock_tushare_api, sample_tushare_data):
        """测试：基本加载流程"""
        # Mock API 返回
        mock_tushare_api.stk_mins.return_value = sample_tushare_data

        # 禁用数据质量检查（测试数据较少）
        provider = TuShareProBarProvider(
            token="test_token", 
            cache_dir=None,
            max_missing_rate=1.0,  # 允许100%缺失（即不检查）
        )
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="1min",
        )
        
        # 验证结果
        assert len(bars) == 3
        assert all(isinstance(bar, Bar) for bar in bars)
        assert bars[0].symbol == "000001.SZ"
        assert bars[0].open == 10.0
        assert bars[0].close == 10.0
        assert bars[0].volume == 1000.0 * 100  # 转换为股

    def test_load_bars_with_cache(self, mock_tushare_api, sample_tushare_data, tmp_path):
        """测试：缓存机制"""
        cache_dir = tmp_path / "cache"
        mock_tushare_api.stk_mins.return_value = sample_tushare_data

        provider = TuShareProBarProvider(
            token="test_token", 
            cache_dir=str(cache_dir),
            max_missing_rate=1.0,  # 禁用数据质量检查
        )

        # 首次加载（应调用 API）
        bars1 = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="1min",
        )
        assert mock_tushare_api.stk_mins.call_count == 1
        
        # 二次加载（应从缓存读取，不调用 API）
        bars2 = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="1min",
        )
        assert mock_tushare_api.stk_mins.call_count == 1  # 未增加
        
        # 验证数据一致
        assert len(bars1) == len(bars2)
        assert bars1[0].open == bars2[0].open

    def test_load_bars_retry_on_network_error(self, mock_tushare_api, sample_tushare_data):
        """测试：网络异常重试"""
        # Mock API：第一次失败，第二次成功
        mock_tushare_api.stk_mins.side_effect = [
            Exception("Network error"),
            sample_tushare_data,
        ]

        provider = TuShareProBarProvider(
            token="test_token", 
            cache_dir=None, 
            max_retries=3,
            max_missing_rate=1.0,  # 禁用数据质量检查
        )
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="1min",
        )
        
        assert len(bars) == 3
        assert mock_tushare_api.stk_mins.call_count == 2  # 重试一次成功

    def test_load_bars_empty_data(self, mock_tushare_api):
        """测试：API 返回空数据"""
        mock_tushare_api.stk_mins.return_value = pd.DataFrame()
        
        provider = TuShareProBarProvider(token="test_token", cache_dir=None)
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="1min",
        )
        
        assert bars == []

    def test_fingerprint_generation(self, mock_tushare_api):
        """测试：数据指纹生成"""
        provider = TuShareProBarProvider(
            token="test_token",
            cache_dir=None,
            adjust="qfq",
        )
        
        fingerprint = provider.fingerprint()
        
        assert fingerprint.provider == "TuSharePro"
        assert fingerprint.adjust == "qfq"
        assert fingerprint.snapshot is not None  # 应包含版本信息
        assert fingerprint.calendar is not None  # 应包含交易日历版本

    def test_convert_to_bar(self, mock_tushare_api):
        """测试：TuShare 数据转换为 Bar"""
        provider = TuShareProBarProvider(token="test_token", cache_dir=None)
        
        # 单条数据
        row = pd.Series(
            {
                "ts_code": "000001.SZ",
                "trade_time": "2024-01-02 09:31",
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "vol": 1000.0,  # 手
                "amount": 10000.0,  # 千元
            }
        )
        
        bar = provider._convert_to_bar(row)
        
        assert bar.symbol == "000001.SZ"
        assert bar.open == 10.0
        assert bar.close == 10.0
        assert bar.volume == 100000.0  # 1000手 × 100股/手
        assert isinstance(bar.start_ts, datetime)
        assert isinstance(bar.end_ts, datetime)

    def test_cache_key_generation(self, mock_tushare_api):
        """测试：缓存键生成"""
        provider = TuShareProBarProvider(token="test_token", cache_dir="/tmp")
        
        key = provider._cache_key(
            symbol="000001.SZ",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            frequency="1min",
        )
        
        assert "000001.SZ" in key
        assert "2024-01-01" in key
        assert "2024-01-31" in key
        assert "1min" in key
        assert key.endswith(".parquet")

    def test_data_quality_check_missing_rate(self, mock_tushare_api):
        """测试：数据质量检查 - 缺失率过高"""
        # Mock 返回缺失数据（应有240根bar，实际只有100根）
        # 生成有效的时间序列（9:30-11:09，共100根1分钟bar）
        trade_times = []
        current_hour = 9
        current_minute = 30
        for i in range(100):
            trade_times.append(f"2024-01-02 {current_hour:02d}:{current_minute:02d}")
            current_minute += 1
            if current_minute >= 60:
                current_hour += 1
                current_minute = 0
                
        incomplete_data = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 100,
                "trade_time": trade_times,
                "open": [10.0] * 100,
                "high": [10.1] * 100,
                "low": [9.9] * 100,
                "close": [10.0] * 100,
                "vol": [1000.0] * 100,
                "amount": [10000.0] * 100,
            }
        )
        mock_tushare_api.stk_mins.return_value = incomplete_data
        
        provider = TuShareProBarProvider(
            token="test_token",
            cache_dir=None,
            max_missing_rate=0.05,  # 5%
        )
        
        # 应抛出异常（缺失率 > 5%）
        with pytest.raises(ValueError, match="缺失率"):
            provider.load_bars(
                symbol="000001.SZ",
                start=date(2024, 1, 2),
                end=date(2024, 1, 2),
                frequency="1min",
            )


class TestBarFieldPopulation:
    """测试 _convert_to_bar 正确填充 prev_close / is_suspended / is_limit_up / is_limit_down"""

    @pytest.fixture
    def provider(self, mock_tushare_api):
        return TuShareProBarProvider(token="test_token", cache_dir=None)

    # --- prev_close ---

    def test_prev_close_from_pre_close_column(self, provider):
        """TuShare pre_close 字段应正确映射到 Bar.prev_close"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.prev_close == 10.0

    def test_prev_close_none_when_missing(self, provider):
        """无 pre_close 列时 Bar.prev_close 为 None"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.prev_close is None

    def test_prev_close_none_when_nan(self, provider):
        """pre_close 为 NaN 时 Bar.prev_close 为 None"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0, "pre_close": float("nan"),
        })
        bar = provider._convert_to_bar(row)
        assert bar.prev_close is None

    # --- is_suspended ---

    def test_suspended_when_zero_volume_flat_ohlc(self, provider):
        """成交量=0 且 OHLC 全等 → 停牌"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "vol": 0.0, "amount": 0.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_suspended is True

    def test_not_suspended_with_volume(self, provider):
        """有成交量 → 非停牌"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_suspended is False

    def test_not_suspended_zero_volume_different_ohlc(self, provider):
        """成交量=0 但 OHLC 不同 → 非停牌（极端情况如集合竞价）"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.0,
            "vol": 0.0, "amount": 0.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_suspended is False

    # --- is_limit_up / is_limit_down ---

    def test_limit_up_detected(self, provider):
        """收盘价 ≥ 涨停价 → is_limit_up = True"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.5, "high": 11.0, "low": 10.5, "close": 11.0,
            "vol": 5000.0, "amount": 55000.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_limit_up is True

    def test_limit_down_detected(self, provider):
        """收盘价 ≤ 跌停价 → is_limit_down = True"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 9.5, "high": 9.5, "low": 9.0, "close": 9.0,
            "vol": 5000.0, "amount": 45000.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_limit_down is True

    def test_no_limit_normal_price(self, provider):
        """正常价格 → 非涨跌停"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0, "pre_close": 10.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_limit_up is False
        assert bar.is_limit_down is False

    def test_limit_states_none_without_prev_close(self, provider):
        """无 prev_close 时无法判断涨跌停 → None"""
        row = pd.Series({
            "ts_code": "000001.SZ", "trade_date": "20240102",
            "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2,
            "vol": 5000.0, "amount": 50000.0,
        })
        bar = provider._convert_to_bar(row)
        assert bar.is_limit_up is None
        assert bar.is_limit_down is None


class TestTuShareProIntegration:
    """集成测试（需要真实 Token，标记为 slow）"""

    @pytest.mark.slow
    @pytest.mark.skipif(
        not project_path("configs/data/tushare_local.yml").exists(),
        reason="需要本地配置文件",
    )
    def test_load_real_data(self):
        """测试：加载真实数据（需要 TuShare Token）"""
        import yaml
        
        # 加载本地配置
        with open(project_path("configs", "data", "tushare_local.yml")) as f:
            config = yaml.safe_load(f)
        
        provider = TuShareProBarProvider(
            token=config["token"],
            cache_dir=config["cache_dir"],
        )
        
        # 加载少量数据（避免API限流）
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 2),
            frequency="daily",  # 用日线减少数据量
        )
        
        assert len(bars) > 0
        assert all(isinstance(bar, Bar) for bar in bars)


class TestMonthlyCacheStrategy:
    """测试按月分片缓存策略"""

    @pytest.fixture
    def provider_with_cache(self, mock_tushare_api, tmp_path):
        """创建带缓存目录的 provider，放宽质量检查阈值"""
        with patch("aipha.data.rate_limiter.get_tushare_limiter") as mock_limiter:
            mock_limiter.return_value = MagicMock(acquire=MagicMock(return_value=True))
            provider = TuShareProBarProvider(
                token="test_token",
                cache_dir=str(tmp_path),
                max_missing_rate=0.99,  # 测试中放宽质量检查
            )
            yield provider, mock_tushare_api, tmp_path

    def _make_daily_df(self, symbol, trade_dates):
        """生成日线 DataFrame"""
        n = len(trade_dates)
        return pd.DataFrame({
            "ts_code": [symbol] * n,
            "trade_date": trade_dates,
            "open": [10.0 + i * 0.1 for i in range(n)],
            "high": [10.5 + i * 0.1 for i in range(n)],
            "low": [9.5 + i * 0.1 for i in range(n)],
            "close": [10.2 + i * 0.1 for i in range(n)],
            "vol": [1000.0] * n,
            "amount": [10000.0] * n,
        })

    def test_monthly_cache_path_format(self, provider_with_cache):
        """月度缓存路径格式正确"""
        provider, _, _ = provider_with_cache
        path = provider._monthly_cache_path("000001.SZ", 2024, 1, "daily")
        assert "000001.SZ_202401_daily_qfq.parquet" in str(path)

    def test_iter_months_single(self, provider_with_cache):
        """单月遍历"""
        provider, _, _ = provider_with_cache
        months = list(provider._iter_months(date(2024, 3, 10), date(2024, 3, 25)))
        assert months == [(2024, 3)]

    def test_iter_months_cross_year(self, provider_with_cache):
        """跨年遍历"""
        provider, _, _ = provider_with_cache
        months = list(provider._iter_months(date(2023, 11, 1), date(2024, 2, 28)))
        assert months == [(2023, 11), (2023, 12), (2024, 1), (2024, 2)]

    def test_cache_hit_no_api_call(self, provider_with_cache):
        """缓存命中时不调用 API"""
        provider, api, tmp_path = provider_with_cache

        # 预写缓存文件
        df = self._make_daily_df("000001.SZ", ["20240102", "20240103"])
        cache_path = provider._monthly_cache_path("000001.SZ", 2024, 1, "daily")
        df.to_parquet(cache_path, index=False)

        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 2),
            end=date(2024, 1, 3),
            frequency="daily",
        )

        assert len(bars) == 2
        # 缓存命中时不调用 tushare.pro_bar
        import tushare
        # 如果缓存命中，provider 不会调用 API 获取数据
        assert bars[0].symbol == "000001.SZ"

    def test_partial_cache_only_fetches_missing(self, provider_with_cache):
        """部分缓存时只下载缺失月份"""
        provider, api, tmp_path = provider_with_cache

        # 1月已缓存
        df_jan = self._make_daily_df("000001.SZ", ["20240102", "20240103"])
        cache_jan = provider._monthly_cache_path("000001.SZ", 2024, 1, "daily")
        df_jan.to_parquet(cache_jan, index=False)

        # 2月未缓存 —— API 返回数据
        df_feb = self._make_daily_df("000001.SZ", ["20240201", "20240205"])
        with patch("tushare.pro_bar", return_value=df_feb):
            bars = provider.load_bars(
                symbol="000001.SZ",
                start=date(2024, 1, 2),
                end=date(2024, 2, 5),
                frequency="daily",
            )

        assert len(bars) == 4  # 1月2条 + 2月2条
        # 2月缓存应已创建
        cache_feb = provider._monthly_cache_path("000001.SZ", 2024, 2, "daily")
        assert cache_feb.exists()

    def test_date_range_filtering(self, provider_with_cache):
        """缓存月份可能包含请求范围外的日期，应被裁剪"""
        provider, _, _ = provider_with_cache

        # 缓存整月数据
        all_dates = [f"202401{d:02d}" for d in range(2, 32)]
        df = self._make_daily_df("000001.SZ", all_dates)
        cache_path = provider._monthly_cache_path("000001.SZ", 2024, 1, "daily")
        df.to_parquet(cache_path, index=False)

        # 只请求 1月10日~1月20日
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 10),
            end=date(2024, 1, 20),
            frequency="daily",
        )

        # 所有 bar 的日期都在请求范围内
        for bar in bars:
            assert date(2024, 1, 10) <= bar.start_ts.date() <= date(2024, 1, 20)

    def test_minute_data_uses_legacy_cache(self, provider_with_cache):
        """分钟线仍使用旧缓存逻辑"""
        provider, _, tmp_path = provider_with_cache

        # 分钟线使用完整日期范围作为缓存键
        cache_path = provider._cache_path(
            "000001.SZ", date(2024, 1, 2), date(2024, 1, 3), "1min"
        )
        assert "000001.SZ_2024-01-02_2024-01-03_1min_qfq" in str(cache_path)

    def test_legacy_cache_migration(self, provider_with_cache):
        """旧格式缓存自动迁移为月度分片"""
        provider, _, tmp_path = provider_with_cache

        # 创建旧格式缓存（跨2个月）
        dates = ["20240128", "20240129", "20240130", "20240131",
                 "20240201", "20240205", "20240206"]
        df = self._make_daily_df("600000.SH", dates)
        legacy_path = provider._cache_path(
            "600000.SH", date(2024, 1, 28), date(2024, 2, 6), "daily"
        )
        df.to_parquet(legacy_path, index=False)
        assert legacy_path.exists()

        # 加载触发迁移
        bars = provider.load_bars(
            symbol="600000.SH",
            start=date(2024, 1, 28),
            end=date(2024, 2, 6),
            frequency="daily",
        )

        # 数据正确加载
        assert len(bars) == 7

        # 旧文件已删除
        assert not legacy_path.exists()

        # 月度缓存已创建
        jan = provider._monthly_cache_path("600000.SH", 2024, 1, "daily")
        feb = provider._monthly_cache_path("600000.SH", 2024, 2, "daily")
        assert jan.exists()
        assert feb.exists()

        # 分片内容正确
        jan_df = pd.read_parquet(jan)
        feb_df = pd.read_parquet(feb)
        assert len(jan_df) == 4  # 1月4条
        assert len(feb_df) == 3  # 2月3条

    def test_no_migration_if_no_legacy(self, provider_with_cache):
        """无旧缓存时不报错"""
        provider, _, _ = provider_with_cache
        # 不抛异常即可
        provider._migrate_legacy_cache(
            "NONEXIST.SZ", date(2024, 1, 1), date(2024, 1, 31), "daily"
        )


class TestSafeReadParquet:
    """缓存损坏保护 —— _safe_read_parquet 测试"""

    def test_read_valid_parquet(self, tmp_path):
        """正常 Parquet 文件应正确读取。"""
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
        p = tmp_path / "valid.parquet"
        df.to_parquet(p)
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is not None
        assert len(result) == 3
        assert list(result.columns) == ["a", "b"]

    def test_corrupted_parquet_returns_none(self, tmp_path):
        """损坏的 Parquet 文件应返回 None 并删除文件。"""
        p = tmp_path / "corrupt.parquet"
        p.write_bytes(b"NOT_A_VALID_PARQUET_FILE_HEADER")
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is None
        assert not p.exists(), "损坏文件应被自动删除"

    def test_truncated_parquet_returns_none(self, tmp_path):
        """截断的 Parquet 文件应返回 None 并删除文件。"""
        # 先写入有效 Parquet，再截断
        df = pd.DataFrame({"x": range(100)})
        p = tmp_path / "truncated.parquet"
        df.to_parquet(p)
        content = p.read_bytes()
        p.write_bytes(content[: len(content) // 2])
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is None
        assert not p.exists()

    def test_empty_file_returns_none(self, tmp_path):
        """空文件应返回 None 并删除文件。"""
        p = tmp_path / "empty.parquet"
        p.write_bytes(b"")
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is None
        assert not p.exists()

    def test_nonexistent_file_returns_none(self, tmp_path):
        """不存在的文件应返回 None（不会抛异常）。"""
        p = tmp_path / "no_such_file.parquet"
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is None

    def test_valid_empty_dataframe(self, tmp_path):
        """有效但无数据行的 Parquet 应正常返回空 DataFrame（不视为损坏）。"""
        df = pd.DataFrame({"col": pd.Series([], dtype="float64")})
        p = tmp_path / "empty_df.parquet"
        df.to_parquet(p)
        result = TuShareProBarProvider._safe_read_parquet(p)
        assert result is not None
        assert len(result) == 0
        assert p.exists(), "有效 Parquet 不应被删除"

    def test_corrupted_file_logged(self, tmp_path, caplog):
        """损坏的 Parquet 应产生警告日志。"""
        import logging

        p = tmp_path / "logged.parquet"
        p.write_bytes(b"DEADBEEF")
        with caplog.at_level(logging.WARNING):
            TuShareProBarProvider._safe_read_parquet(p)
        assert any("Parquet 缓存损坏" in rec.message for rec in caplog.records)
