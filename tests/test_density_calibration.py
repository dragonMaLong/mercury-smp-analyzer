"""Density protocol tests and independent official-PDF regression checks."""
from pathlib import Path
import re
import struct
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mercury_app.core import load_smp, summary_metrics, calculate_pore_structure, calculate_mayer_stowe
from smp_parser import SMPFile, SMPParser, _parse_mercury_temperature, _resolve_mercury_density


def test_density_records_pair_temperature_with_following_density():
    # A standalone configured value precedes the ascending calibration table.
    rows = [(25., 13.5335), (18., 13.5512), (19., 13.5487), (20., 13.5462)]
    payload = bytes(180) + b''.join(struct.pack('<Bdd', 1, t, d) for t, d in rows) + bytes(20)
    smp = SMPFile()
    SMPParser()._parse_subset627(payload, (0, len(payload)), smp)
    assert smp._density_map == dict(rows[1:])
    smp.mercury_temperature_C = 18.72690773010254
    _resolve_mercury_density(smp)
    assert smp.mercury_density_gmL == pytest.approx(13.549382730674743, abs=1e-12)


def test_temperature_trailer_does_not_require_free_space_or_block_order():
    smp = SMPFile()
    data = bytes(140) + struct.pack('<d', 21.37911605834961) + bytes(100)
    _parse_mercury_temperature(data, {0x276: (100, 30), 0x2c1: (148, 60)}, smp)
    assert smp.mercury_temperature_C == 21.37911605834961


REFERENCE_FILES = sorted(p for p in ROOT.parent.glob('*.SMP') if p.with_suffix('.pdf').exists())


@pytest.mark.parametrize('path', REFERENCE_FILES, ids=lambda p: p.stem)
def test_densities_match_official_pdf(path):
    pypdf = pytest.importorskip('pypdf')
    text = pypdf.PdfReader(path.with_suffix('.pdf')).pages[0].extract_text()
    if not text.strip():
        pytest.skip('Image-only PDF has no extractable summary text')
    bulk = re.search(r'Bulk density at ([\d,.]+) psia:\s*([\d,.]+)', text)
    skeletal = re.search(r'Apparent \(skeletal\) density at [\d,.]+ psia:\s*([\d,.]+)', text)
    porosity = re.search(r'Porosity:\s*([\d,.]+)', text)
    temperature = re.search(r'Mercury Temperature:\s*([\d,.]+)', text)
    assert all((bulk, skeletal, porosity, temperature)), path.name
    result = load_smp(path)
    summary = summary_metrics(result)
    actual = [summary.bulk_density, summary.apparent_density, summary.porosity]
    official = [float(v.replace(',', '')) for v in (bulk[2], skeletal[1], porosity[1])]
    # Reports contain four decimals; allow half a last digit plus 1e-6 for
    # stored single-precision volumes near a rounding boundary (1-6.SMP).
    assert actual == pytest.approx(official, rel=0, abs=0.000051)
    assert summary.bulk_density_pressure == pytest.approx(float(bulk[1]), abs=.005, rel=0)
    assert result.metadata['mercury_temperature_C'] == pytest.approx(float(temperature[1]), abs=.005, rel=0)


@pytest.mark.skipif(not (ROOT.parent / '2-QC.SMP').exists(), reason='Local sample unavailable')
def test_corrected_density_is_used_in_both_analysis_models():
    result = load_smp(ROOT.parent / '2-QC.SMP')
    summary = summary_metrics(result)
    pore = calculate_pore_structure(result, pressure_min=400, pressure_max=10000)
    mayer = calculate_mayer_stowe(result, 400, 10000)
    assert pore.bulk_density_gmL == summary.bulk_density
    assert pore.skeletal_density_gmL == summary.apparent_density
    assert mayer.bulk_density == summary.bulk_density
    assert summary.bulk_density == pytest.approx(.29973344, abs=1e-7)
    assert summary.apparent_density == pytest.approx(1.5550633, abs=1e-7)
