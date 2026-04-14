"""Tests for interface Protocol compliance.

Verify that all concrete execution models satisfy their Protocol contracts:
- FillModel Protocol
- FeeModel Protocol
- SlippageModel Protocol
"""

from __future__ import annotations

import pytest

from aipha.execution.fees import FeeModel, FeeModelCNEquity, CommissionModel, StampTaxModel, TransferFeeModel
from aipha.execution.fill_models import (
    BarLimitTouchFillModel,
    FillModel,
    NextBarCloseFillModel,
    NextBarOpenFillModel,
    SameBarCloseFillModel,
    TwapSliceFillModel,
    VolumeConstrainedFillModel,
    VWAPFillModel,
)
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    LinearImpactSlippageModel,
    SlippageModel,
    SquareRootImpactSlippageModel,
)


# ======================== FillModel Protocol ============================

class TestFillModelProtocol:
    """All FillModel implementations satisfy the Protocol."""

    @pytest.mark.parametrize(
        "cls",
        [
            NextBarOpenFillModel,
            NextBarCloseFillModel,
            SameBarCloseFillModel,
            BarLimitTouchFillModel,
            VWAPFillModel,
        ],
    )
    def test_fill_model_isinstance(self, cls: type) -> None:
        instance = cls()
        assert isinstance(instance, FillModel)

    def test_volume_constrained_isinstance(self) -> None:
        inner = NextBarOpenFillModel()
        vc = VolumeConstrainedFillModel(inner)
        assert isinstance(vc, FillModel)

    def test_twap_slice_isinstance(self) -> None:
        tw = TwapSliceFillModel(n_slices=5)
        assert isinstance(tw, FillModel)

    def test_same_bar_property(self) -> None:
        assert SameBarCloseFillModel().is_same_bar is True
        assert NextBarOpenFillModel().is_same_bar is False


# ======================== FeeModel Protocol =============================

class TestFeeModelProtocol:
    """FeeModelCNEquity satisfies FeeModel Protocol."""

    def test_cn_equity_isinstance(self) -> None:
        fee = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0003, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.0005),
            transfer_fee=TransferFeeModel(rate=0.00001),
        )
        assert isinstance(fee, FeeModel)

    def test_protocol_has_calc(self) -> None:
        """FeeModel Protocol defines calc method."""
        assert hasattr(FeeModel, "calc")


# ======================== SlippageModel Protocol ========================

class TestSlippageModelProtocol:
    """All SlippageModel implementations satisfy the Protocol."""

    def test_fixed_bps_isinstance(self) -> None:
        m = FixedBpsSlippageModel(bps=2.0)
        assert isinstance(m, SlippageModel)

    def test_linear_impact_isinstance(self) -> None:
        m = LinearImpactSlippageModel(base_bps=1.0, impact_coeff=0.1)
        assert isinstance(m, SlippageModel)

    def test_sqrt_impact_isinstance(self) -> None:
        m = SquareRootImpactSlippageModel(
            sigma_daily=0.02,
            impact_coeff=0.5,
        )
        assert isinstance(m, SlippageModel)


# ======================== Cross-checks ==================================

class TestInterfaceConsistency:
    """Verify that interface patterns are consistent across the project."""

    def test_all_protocols_are_runtime_checkable(self) -> None:
        """All model protocols should support isinstance() checks."""
        from typing import runtime_checkable

        # FeeModel and SlippageModel are typing.Protocol
        for protocol_cls in [FeeModel, SlippageModel]:
            # runtime_checkable protocols have _is_runtime_protocol attribute
            assert getattr(protocol_cls, "_is_runtime_protocol", False), (
                f"{protocol_cls.__name__} is not @runtime_checkable"
            )

        # FillModel is a regular base class (not Protocol), isinstance works via inheritance
        assert isinstance(NextBarOpenFillModel(), FillModel)
