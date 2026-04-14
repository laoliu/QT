"""Tests for tracker.utils — symbol normalization."""

from aipha.tracker.utils import normalize_symbol


class TestNormalizeSymbol:
    """Test normalize_symbol with various input formats."""

    # -- Standard format (pass-through) -------------------------------------

    def test_already_standard_sh(self):
        assert normalize_symbol("600519.SH") == "600519.SH"

    def test_already_standard_sz(self):
        assert normalize_symbol("000001.SZ") == "000001.SZ"

    def test_already_standard_bj(self):
        assert normalize_symbol("430001.BJ") == "430001.BJ"

    # -- Xueqiu prefix format -----------------------------------------------

    def test_xueqiu_sh(self):
        assert normalize_symbol("SH600519") == "600519.SH"

    def test_xueqiu_sz(self):
        assert normalize_symbol("SZ000001") == "000001.SZ"

    def test_xueqiu_bj(self):
        assert normalize_symbol("BJ430001") == "430001.BJ"

    # -- Bare 6-digit codes -------------------------------------------------

    def test_bare_sh_600(self):
        assert normalize_symbol("600519") == "600519.SH"

    def test_bare_sh_688(self):
        assert normalize_symbol("688001") == "688001.SH"

    def test_bare_sh_900(self):
        assert normalize_symbol("900001") == "900001.SH"

    def test_bare_sz_000(self):
        assert normalize_symbol("000001") == "000001.SZ"

    def test_bare_sz_002(self):
        assert normalize_symbol("002001") == "002001.SZ"

    def test_bare_sz_300(self):
        assert normalize_symbol("300750") == "300750.SZ"

    def test_bare_bj_430(self):
        assert normalize_symbol("430001") == "430001.BJ"

    def test_bare_bj_830(self):
        assert normalize_symbol("830001") == "830001.BJ"

    # -- Edge cases ----------------------------------------------------------

    def test_lowercase(self):
        assert normalize_symbol("sh600519") == "600519.SH"

    def test_whitespace(self):
        assert normalize_symbol("  SH600519  ") == "600519.SH"

    def test_unknown_format_passthrough(self):
        result = normalize_symbol("AAPL")
        assert result == "AAPL"
