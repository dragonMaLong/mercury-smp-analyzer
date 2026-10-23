"""Penetrometer constants above 20 uL/pF must still be parsed.

Regression for v1.1.1: files measured with penetrometers 10-0303 (22.065 uL/pF)
and 10-0820 (28.63 uL/pF) were rejected by the old 5-20 uL/pF window, fell back
to a fixed offset, read garbage and plotted as a flat line.
"""
from __future__ import annotations

from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smp_parser import SMPFile, SMPParser


BLOCK_SIZE = 190  # SUBSET628 size in the AutoPore V9600 files


def _subset628_block(constant: float, record_offset: int = 151) -> bytes:
    """Build a SUBSET628 payload laid out like the AutoPore V9600 files.

    As in the real files, the five-double record starts inside the block and
    runs a few bytes past its declared end.
    """
    payload = bytearray(BLOCK_SIZE + 64)
    struct.pack_into("<5d", payload, record_offset, constant, 62.201, 6.0177, 1.13, 4.45)
    return bytes(payload)


@pytest.mark.parametrize("constant", [10.898, 11.117, 22.065, 28.63])
def test_penetrometer_record_found_for_common_constants(constant: float) -> None:
    data = _subset628_block(constant)
    smp = SMPFile()

    SMPParser()._parse_subset628(data, (0, BLOCK_SIZE), smp)

    assert smp.penetrometer.constant_uL_per_pF == pytest.approx(constant)
    assert smp.penetrometer.mass_g == pytest.approx(62.201)
    assert smp.penetrometer.bulb_volume_mL == pytest.approx(6.0177)
    assert smp.penetrometer.stem_volume_mL == pytest.approx(1.13)
    assert smp.penetrometer.max_head_psia == pytest.approx(4.45)
