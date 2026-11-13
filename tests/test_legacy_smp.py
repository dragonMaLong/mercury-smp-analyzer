"""Legacy volume records must never pass through the capacitance calculation.

Synthetic fixtures contain no experimental data. Optional local regressions
check the independently decoded reference files without committing them.
"""
from pathlib import Path
import math
import struct

import numpy as np
import pytest

from smp_parser import SMPParser, export_csv
from mercury_app.core import calculate_microactive, load_smp, summary_metrics


def legacy_file(tmp_path, *, negative=False, blank=None, correction_code=None):
    """``blank`` = (points, intrusion_count) embeds a SUBSET628 blank curve."""
    pressures = [1.0, -20.0, 100.0, 1000.0, 500.0, 100.0]
    volumes = [0.0, 0.0, 0.1, -0.02 if negative else 0.4, 0.3, 0.35]
    blocks = {}
    size628 = 224 if blank is None else 226 + 16 * len(blank[0])
    for sid, size, revision in [(626, 924, 7), (627, 729, 1),
                                (628, size628, 2), (630, 217 + 16 * 6, 2)]:
        block = bytearray(18 + size)
        block[:10] = f'SUBSET{sid}\0'.encode('ascii')
        struct.pack_into('<HI', block, 12, sid, size)
        block[18] = revision
        blocks[sid] = block
    struct.pack_into('<d', blocks[626], 19, 0.2)
    blocks[626][29:39] = b'synthetic\0'
    struct.pack_into('<d', blocks[626], 751, 120.0)
    struct.pack_into('<4d', blocks[627], 298, 140.0, 140.0, 485.0, 13.53)
    struct.pack_into('<5d', blocks[628], 115, 11.0, 60.0, 5.0, 0.4, 4.45)
    blocks[628][236] = correction_code if correction_code is not None else (1 if blank is None else 2)
    if blank is not None:
        points, intrusion_count = blank
        blocks[628][155:170] = b'C:\\BLANK\\B.SMP\0'
        struct.pack_into('<H2xH', blocks[628], 238, len(points), intrusion_count)
        for i, values in enumerate(points):
            struct.pack_into('<dd', blocks[628], 244 + 16 * i, *values)
    struct.pack_into('<IH', blocks[630], 229, 6, 4)
    for i, values in enumerate(zip(pressures, volumes)):
        struct.pack_into('<dd', blocks[630], 235 + i * 16, *values)
    data = bytearray(128)
    data[2:11] = b'MIC##&&FS'
    data[12:16] = b'0200'
    data[32:42] = b'SUBSET101\0'
    struct.pack_into('<HI', data, 44, 101, 78)
    struct.pack_into('<H', data, 51, len(blocks))
    offsets = {}
    for i, (sid, block) in enumerate(blocks.items()):
        offsets[sid] = len(data)
        struct.pack_into('<HII', data, 53 + i * 10, sid, len(data), len(block) - 18)
        data.extend(block)
    path = tmp_path / '旧版 测试# (1).SMP'
    path.write_bytes(data)
    return path, offsets


def test_legacy_import_preserves_units_branches_and_extrusion(tmp_path):
    path, _ = legacy_file(tmp_path)
    smp = SMPParser().parse(str(path))
    assert smp.sample_name == 'synthetic'
    assert smp.sample_mass_g == pytest.approx(0.2)
    assert smp.mercury_density_gmL == pytest.approx(13.53)
    assert smp.adv_contact_angle_deg == 140.0
    assert smp.measurement_basis == 'intrusion_volume_mL'
    assert smp.intrusion_point_count == 4
    assert len(smp.raw_points) == 6
    assert smp.raw_points[1].pressure_psia == -20.0
    assert all(p.capacitance_pF is None and p.timestamp is None for p in smp.raw_points)
    result = calculate_microactive(smp)
    np.testing.assert_allclose(result.pressure, [1, 100, 1000, 500, 100])
    np.testing.assert_allclose(result.cum_volume, [0, 0.5, 2, 1.5, 1.75])
    np.testing.assert_array_equal(result.is_extrusion, [0, 0, 0, 1, 1])
    assert result.metadata['warnings']
    smp.penetrometer.constant_uL_per_pF = 0
    smp.penetrometer.max_head_psia = 999
    edited = calculate_microactive(smp, adv_contact_angle_deg=130)
    np.testing.assert_array_equal(result.pressure, edited.pressure)
    np.testing.assert_array_equal(result.cum_volume, edited.cum_volume)
    assert not np.allclose(result.diameter, edited.diameter)


def test_negative_legacy_volume_remains_signed_and_porosity_unavailable(tmp_path):
    path, _ = legacy_file(tmp_path, negative=True)
    result = load_smp(path)
    assert result.total_pore_volume == pytest.approx(-0.1)
    assert math.isnan(summary_metrics(result).porosity)
    assert math.isnan(summary_metrics(result).apparent_density)
    assert math.isnan(summary_metrics(result).average_pore_diameter)
    assert math.isnan(summary_metrics(result).median_volume_diameter)
    assert np.all(np.isnan(result.pct_total))
    assert any('总进汞量' in warning for warning in result.metadata['warnings'])


SYNTHETIC_BLANK = ([(1.0, 0.0), (100.0, -0.01), (1000.0, -0.04),   # intrusion branch
                    (500.0, -0.03), (100.0, -0.02)], 3)            # extrusion branch


def test_embedded_blank_is_subtracted_on_matching_branch(tmp_path):
    path, _ = legacy_file(tmp_path, blank=SYNTHETIC_BLANK)
    smp = SMPParser().parse(str(path))
    assert smp.correction_type_code == 2
    assert smp.blank_curve.reference_path == 'C:\\BLANK\\B.SMP'
    assert smp.blank_curve.intrusion_count == 3
    # Raw records stay uncorrected; only the calculated result subtracts the blank.
    assert smp.raw_points[3].intrusion_volume_mL == pytest.approx(0.4)
    result = calculate_microactive(smp)
    # Blank nodes coincide with sample pressures, so interpolation is exact.
    # 100 psia: intrusion branch blank -0.01, extrusion branch blank -0.02.
    np.testing.assert_allclose(result.cum_volume,
                               np.array([0.0, 0.11, 0.44, 0.33, 0.37]) / 0.2)
    assert any('空白' in w for w in result.metadata['warnings'])


def test_blank_interpolation_is_clamped_and_constant_outside_branch():
    from smp_parser import BlankCurve as LegacyBlankCurve, blank_curve_value as legacy_blank_volume_mL
    curve = LegacyBlankCurve('', [(1.0, 0.0), (2.0, -1.0), (3.0, -1.0), (10.0, -1.1),
                                  (20.0, -2.0), (5.0, -1.5)], 5)
    # A steep step followed by a plateau makes plain Akima overshoot (3, 10).
    for p in np.linspace(3.0, 10.0, 15):
        assert -1.1 <= legacy_blank_volume_mL(curve, p, extrusion=False) <= -1.0
    assert legacy_blank_volume_mL(curve, 0.5, extrusion=False) == 0.0
    assert legacy_blank_volume_mL(curve, 50.0, extrusion=False) == -2.0
    # Extrusion branch excludes the maximum-pressure point: single point -> constant.
    assert legacy_blank_volume_mL(curve, 15.0, extrusion=True) == -1.5


@pytest.mark.parametrize('damage', ['length', 'split', 'nonfinite'])
def test_damaged_blank_curve_is_rejected(tmp_path, damage):
    path, offsets = legacy_file(tmp_path, blank=SYNTHETIC_BLANK)
    data = bytearray(path.read_bytes())
    start = offsets[628]
    if damage == 'length':
        struct.pack_into('<H', data, start + 238, 6)
    elif damage == 'split':
        struct.pack_into('<H', data, start + 242, 0)
    else:
        struct.pack_into('<d', data, start + 244 + 8, float('inf'))
    path.write_bytes(data)
    with pytest.raises(ValueError):
        load_smp(path)


def test_unverified_correction_code_is_reported_not_applied(tmp_path):
    path, _ = legacy_file(tmp_path, correction_code=3)
    result = load_smp(path)
    assert result.raw_smp.blank_curve is None
    assert result.total_pore_volume == pytest.approx(2.0)
    assert any('校正类型代码为 3' in w for w in result.metadata['warnings'])


def test_legacy_test_time_comes_from_analysis_records(tmp_path):
    path, offsets = legacy_file(tmp_path)
    header_time = SMPParser().parse(str(path)).created      # no analysis records: header fallback
    data = bytearray(path.read_bytes())
    start = offsets[630]
    # struct tm: sec, min, hour, mday, mon (0-based), year - 1900, wday, yday, isdst
    struct.pack_into('<9H', data, start + 154, 27, 56, 11, 11, 5, 123, 0, 161, 1)
    struct.pack_into('<9H', data, start + 208, 31, 54, 12, 11, 5, 123, 0, 161, 1)
    path.write_bytes(data)
    smp = SMPParser().parse(str(path))
    assert smp.created == '2023-06-11 11:56:27' != header_time
    assert smp.modified == '2023-06-11 12:54:31'
    assert smp.analysis_times == {'lp_start': smp.created, 'hp_end': smp.modified}


def test_legacy_raw_calculation_and_csv_accept_missing_capacitance(tmp_path):
    path, _ = legacy_file(tmp_path)
    parser = SMPParser()
    smp = parser.parse(str(path))
    rows = parser.calculate(smp)
    peak = max(rows, key=lambda row: row.pressure_psia)
    assert peak.cumulative_volume_mLg == pytest.approx(2.0)
    output = tmp_path / 'raw.csv'
    export_csv(rows, str(output), smp)
    assert output.exists()
    assert '2.000000000' in output.read_text(encoding='utf-8-sig')


@pytest.mark.parametrize('damage', ['truncated', 'count', 'boundary', 'block_id', 'nonfinite'])
def test_damaged_legacy_records_are_rejected(tmp_path, damage):
    path, offsets = legacy_file(tmp_path)
    data = bytearray(path.read_bytes())
    start = offsets[630]
    if damage == 'truncated':
        data = data[:-5]
    elif damage == 'count':
        struct.pack_into('<I', data, start + 229, 7)
    elif damage == 'boundary':
        struct.pack_into('<H', data, start + 233, 7)
    elif damage == 'block_id':
        struct.pack_into('<H', data, start + 12, 629)
    else:
        struct.pack_into('<d', data, start + 243, float('nan'))
    path.write_bytes(data)
    with pytest.raises(ValueError):
        load_smp(path)


@pytest.mark.parametrize('mass', [0.0, -1.0, float('nan')])
def test_invalid_legacy_mass_is_rejected(tmp_path, mass):
    path, offsets = legacy_file(tmp_path)
    data = bytearray(path.read_bytes())
    struct.pack_into('<d', data, offsets[626] + 19, mass)
    path.write_bytes(data)
    with pytest.raises(ValueError):
        load_smp(path)


DATA_ROOT = Path(__file__).resolve().parents[2]


TARGETS = {
    '2513-1': DATA_ROOT / '2513-1# (1).SMP',
    '2513-2': Path.home() / 'Desktop' / '2513-2# (1).SMP',
    '2513-3': Path.home() / 'Desktop' / '2513-3#.SMP',
}
VENDOR_REF = DATA_ROOT / 'MicroActive7_reference'


# raw_peak = stored (uncorrected) mL at max pressure; vendor = MicroActive 7.00
# Summary report with Blank correction K08-0196.SMP (total mL/g, bulk, skeletal, porosity %).
@pytest.mark.parametrize('path,count,mass,raw_peak,vendor', [
    (TARGETS['2513-1'], 78, 0.1037, 0.019631916657090187, (0.5784, 0.9462, 2.0902, 54.7310)),
    (TARGETS['2513-2'], 78, 0.1238, 0.029571032151579857, (0.5648, 0.8918, 1.7970, 50.3716)),
    (TARGETS['2513-3'], 77, 0.0732, -0.003142471658065915, (0.5083, 0.2919, 0.3428, 14.8378)),
    (DATA_ROOT / '008-804.SMP', 100, 0.1428, 0.28722691535949707, None),
])
def test_local_legacy_reference_files(path, count, mass, raw_peak, vendor):
    if not path.exists():
        pytest.skip('Local experimental files are not included in the repository.')
    result = load_smp(path)
    smp = result.raw_smp
    assert len(smp.raw_points) == count
    assert result.data_point_count == count - 1
    assert result.metadata['sample_mass_g'] == pytest.approx(mass)
    peak = max(smp.raw_points[:smp.intrusion_point_count], key=lambda p: p.pressure_psia)
    assert peak.intrusion_volume_mL == pytest.approx(raw_peak)
    if vendor is not None:
        total, bulk, skeletal, porosity = vendor
        assert smp.blank_curve is not None and len(smp.blank_curve.points) == 74
        summary = summary_metrics(result)
        assert result.total_pore_volume == pytest.approx(total, abs=0.00005)
        # Vendor shows 4 decimals and appears to truncate (0.89188 -> 0.8918).
        assert summary.bulk_density == pytest.approx(bulk, abs=0.0001)
        # 1/(1/bulk - V) amplifies the sub-display bulk difference (2-1: 2.09030 vs 2.0902).
        assert summary.apparent_density == pytest.approx(skeletal, abs=0.0002)
        # 2513-3 differs by 1e-3 % from rounding of the vendor's bulk density.
        assert summary.porosity == pytest.approx(porosity, abs=0.002)
    # Vendor report headers: "Serial # 830  Port 1/1" (2513, MicroActive 7.00)
    # and "Serial # 1404  Port 2/2" (008-804, AutoPore IV 9500 2.03.01 XLS).
    serial, ports = ('1404', '2/2') if path.name == '008-804.SMP' else ('830', '1/1')
    assert (smp.instrument_model, smp.instrument_serial, smp.instrument_ports) == ('94XX', serial, ports)
    assert result.metadata['instrument_name'] == f'94XX（序列号 {serial}）'
    if path.name == '008-804.SMP':
        assert smp.correction_type_code == 1 and smp.blank_curve is None
        # Vendor XLS: LP Analysis Time 2025/3/7 15:32:29, HP Analysis Time 16:39:14.
        assert smp.analysis_times == {
            'lp_start': '2025-03-07 15:04:31', 'lp_end': '2025-03-07 15:32:29',
            'hp_start': '2025-03-07 15:52:16', 'hp_end': '2025-03-07 16:39:14'}
        assert result.metadata['created'] == '2025-03-07 15:04:31'
        # Independent original AutoPore IV 9500 XLS report, summary row 29.
        assert result.total_pore_volume == pytest.approx(2.0114, abs=0.00005)
        summary = summary_metrics(result)
        assert summary.bulk_density == pytest.approx(0.3792, abs=0.00005)
        assert summary.apparent_density == pytest.approx(1.5983, abs=0.00005)
        assert summary.porosity == pytest.approx(76.2740, abs=0.00005)
    if path.name == '2513-3#.SMP':
        # MicroActive 7.00: LP Analysis Time 2023/6/11 12:16:08, HP 12:54:31.
        assert smp.analysis_times['lp_end'] == '2023-06-11 12:16:08'
        assert smp.analysis_times['hp_end'] == '2023-06-11 12:54:31'
        assert result.metadata['created'] == '2023-06-11 11:56:27'


@pytest.mark.parametrize('name', sorted(TARGETS))
def test_blank_corrected_curve_matches_microactive_tabular_report(name):
    reference = VENDOR_REF / f'{name}_tabular.csv'
    if not (TARGETS[name].exists() and reference.exists()):
        pytest.skip('Local MicroActive 7.00 reference export is unavailable.')
    rows = [line.split(',') for line in reference.read_text(encoding='utf-8').splitlines()[2:]]
    vendor = np.array([[float(p), float(v)] for p, v in rows])
    result = load_smp(TARGETS[name])
    np.testing.assert_allclose(result.pressure, vendor[:, 0], rtol=1e-5)
    # Vendor table is single precision; residual is in the extrusion end interval.
    np.testing.assert_allclose(result.cum_volume, vendor[:, 1], rtol=0, atol=2e-5)


def test_gui_imports_mixed_old_new_files_and_exports_signed_volume(tmp_path, qt_runtime):
    from mercury_app.ui.main_window import MainWindow
    from mercury_app.core import export_results_xlsx
    from openpyxl import load_workbook

    paths = [DATA_ROOT / '2513-1# (1).SMP',
             Path.home() / 'Desktop' / '2513-2# (1).SMP',
             Path.home() / 'Desktop' / '2513-3#.SMP',
             DATA_ROOT / 'M5G2.SMP']
    if not all(path.exists() for path in paths):
        pytest.skip('Local mixed-format reference files are unavailable.')
    window = MainWindow()
    window.load_files(paths)
    qt_runtime.processEvents()
    assert len(window.results) == 4
    corrected = next(r for r in window.results if r.metadata['file_name'] == '2513-3#')
    # Stored peak volume is negative; blank-corrected total matches MicroActive 7.00.
    assert corrected.raw_smp.raw_points[corrected.raw_smp.intrusion_point_count - 1].intrusion_volume_mL < 0
    assert corrected.total_pore_volume == pytest.approx(0.5083, abs=0.00005)
    assert any(r.metadata['measurement_basis'] == 'capacitance_pF' for r in window.results)
    output = export_results_xlsx(window.results, tmp_path / 'mixed.xlsx')
    workbook = load_workbook(output, data_only=True)
    values = {row[0]: row[1] for row in workbook['2513-3#'].iter_rows(values_only=True)
              if len(row) >= 2 and isinstance(row[0], str)}
    assert values['孔隙率 (%)'] == pytest.approx(14.84, abs=0.01)
    assert values['总入汞体积 (mL/g)'] == pytest.approx(0.5083, abs=0.00005)
    assert values['空白校正'] == 'K08-0196.SMP'
    assert '数据提示' not in values
    workbook.close()
    window.close()
