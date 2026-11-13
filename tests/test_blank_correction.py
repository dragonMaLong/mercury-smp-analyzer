"""Blank correction selection: file default, none, external blank file, 9600 layout.

Vendor references were exported from MicroActive 7.00 on copies of the files.
Local experimental files are optional; those tests skip when they are absent.
"""
from pathlib import Path
import struct

import numpy as np
import pytest

from smp_parser import SMPFile, SMPParser
from mercury_app.core import blank_curve_from_smp, calculate_microactive, load_smp
from test_legacy_smp import DATA_ROOT, SYNTHETIC_BLANK, TARGETS, VENDOR_REF, legacy_file


def vendor_table(name):
    lines = (VENDOR_REF / name).read_text(encoding='utf-8').splitlines()[2:]
    return np.array([[float(x) for x in line.split(',')] for line in lines])


def modern_blank_block(records, split, path='C:\\data\\blank.SMP', code=2):
    """SUBSET628 rev 3 with the tail layout MicroActive 7.00 writes."""
    body = bytearray(18 + 40)
    body[:10] = b'SUBSET628\0'
    body[18] = 3
    body += path.encode('utf-16le') + b'\0\0'
    body += bytes([3]) + struct.pack('<HI', code, len(records))
    if records:
        body += struct.pack('<H', split)
        for pressure, x in records:
            body += struct.pack('<ddd', pressure, x, 0.0)
    return bytes(body)


def test_modern_embedded_blank_is_decoded_from_block_tail():
    block = modern_blank_block([(1.0, 0.0), (10.0, -2.0), (100.0, -4.0), (50.0, -3.0)], 3)
    smp = SMPFile()
    smp.penetrometer.constant_uL_per_pF = 10.0
    SMPParser._parse_modern_blank(block, (0, len(block) - 18), smp)
    assert smp.correction_type_code == 2
    curve = smp.blank_curve
    assert curve.per_gram and curve.intrusion_count == 3 and curve.label == 'blank.SMP'
    # -x * k / 1000 -> mL per gram of the blank file's mass.
    assert curve.points == [(1.0, 0.0), (10.0, 0.02), (100.0, 0.04), (50.0, 0.03)]


def test_modern_file_without_blank_reports_code_one():
    block = modern_blank_block([], 0, path='No Blank Data', code=1)
    smp = SMPFile()
    smp.penetrometer.constant_uL_per_pF = 10.0
    SMPParser._parse_modern_blank(block, (0, len(block) - 18), smp)
    assert smp.correction_type_code == 1 and smp.blank_curve is None


def test_selection_none_file_and_external(tmp_path):
    sample, _ = legacy_file(tmp_path, blank=SYNTHETIC_BLANK)
    blank_dir = tmp_path / 'blank'
    blank_dir.mkdir()
    blank_path, _ = legacy_file(blank_dir)          # mass 0.2 g, no blank of its own
    smp = SMPParser().parse(str(sample))

    default = calculate_microactive(smp)
    assert default.metadata['blank_correction_source'] == 'file'
    assert default.metadata['blank_correction_label'] == 'B.SMP'
    assert default.total_pore_volume == pytest.approx(0.44 / 0.2)

    smp.blank_selection = None
    none = calculate_microactive(smp)
    assert none.metadata['blank_correction_source'] == 'none'
    assert none.total_pore_volume == pytest.approx(0.4 / 0.2)
    assert any('当前选择为不校正' in w for w in none.metadata['warnings'])

    smp.blank_selection = blank_curve_from_smp(blank_path)
    external = calculate_microactive(smp)
    assert external.metadata['blank_correction_source'] == 'external'
    # Same raw data as the sample and the same mass: specific volumes cancel.
    np.testing.assert_allclose(external.cum_volume, 0.0, atol=1e-12)
    # Parameter edits keep the selection.
    edited = calculate_microactive(smp, adv_contact_angle_deg=130)
    np.testing.assert_allclose(edited.cum_volume, 0.0, atol=1e-12)


def test_external_blank_uses_blank_files_own_mass(tmp_path):
    sample, _ = legacy_file(tmp_path)
    blank_dir = tmp_path / 'blank'
    blank_dir.mkdir()
    blank_path, offsets = legacy_file(blank_dir)
    data = bytearray(blank_path.read_bytes())
    struct.pack_into('<d', data, offsets[626] + 19, 0.4)   # blank mass 0.4 g vs sample 0.2 g
    blank_path.write_bytes(data)
    smp = SMPParser().parse(str(sample))
    smp.blank_selection = blank_curve_from_smp(blank_path)
    result = calculate_microactive(smp)
    # 0.4 mL / 0.2 g - 0.4 mL / 0.4 g at the 1000 psia peak.
    assert result.total_pore_volume == pytest.approx(2.0 - 1.0)


def test_legacy_external_blank_matches_microactive():
    reference = VENDOR_REF / '2513-1_with_2513-2_as_blank_tabular.csv'
    if not (TARGETS['2513-1'].exists() and TARGETS['2513-2'].exists() and reference.exists()):
        pytest.skip('Local MicroActive 7.00 reference export is unavailable.')
    vendor = vendor_table(reference.name)
    smp = SMPParser().parse(str(TARGETS['2513-1']))
    smp.blank_selection = blank_curve_from_smp(TARGETS['2513-2'])
    result = calculate_microactive(smp)
    np.testing.assert_allclose(result.pressure, vendor[:, 0], rtol=1e-5)
    np.testing.assert_allclose(result.cum_volume, vendor[:, 1], rtol=0, atol=1e-5)


def splice_block(base: bytes, new_block: bytes, sid=628) -> bytes:
    data = bytearray(base)
    old_offset, old_size = SMPParser()._parse_directory(base)[sid]
    delta = len(new_block) - (18 + old_size)
    pos = data.find(b'SUBSET101')
    for i in range(struct.unpack_from('<H', data, pos + 19)[0]):
        entry = pos + 21 + i * 10
        type_id, offset, _ = struct.unpack_from('<HII', data, entry)
        if type_id == sid:
            struct.pack_into('<I', data, entry + 6, len(new_block) - 18)
        elif offset > old_offset:
            struct.pack_into('<I', data, entry + 2, offset + delta)
    data[old_offset:old_offset + 18 + old_size] = new_block
    return bytes(data)


def test_9600_blank_intrusion_matches_microactive(tmp_path):
    sample, blank = DATA_ROOT / 'M5G2.SMP', DATA_ROOT / '2-M5G2.SMP'
    reference = VENDOR_REF / 'M5G2_with_2-M5G2_as_blank_tabular.csv'
    block = VENDOR_REF / 'M5G2_SUBSET628_with_2-M5G2_blank.bin'
    if not all(path.exists() for path in (sample, blank, reference, block)):
        pytest.skip('Local 9600 reference files are unavailable.')
    vendor = vendor_table(reference.name)

    plain = load_smp(sample)
    assert plain.raw_smp.correction_type_code == 1
    assert plain.metadata['blank_correction_source'] == 'none'
    intrusion = plain.is_extrusion == 0

    # User-selected blank file.
    plain.raw_smp.blank_selection = blank_curve_from_smp(blank)
    external = calculate_microactive(plain.raw_smp)
    np.testing.assert_allclose(external.cum_volume[intrusion], vendor[intrusion, 1], rtol=0, atol=5e-6)

    # Blank embedded in the file (SUBSET628 as saved by MicroActive 7.00): applied by default.
    embedded_path = tmp_path / 'M5G2_embedded.SMP'
    embedded_path.write_bytes(splice_block(sample.read_bytes(), block.read_bytes()))
    embedded = load_smp(embedded_path)
    assert embedded.metadata['blank_correction_source'] == 'file'
    assert embedded.metadata['blank_correction_label'] == 'm_b.SMP'
    np.testing.assert_allclose(embedded.cum_volume[intrusion], vendor[intrusion, 1], rtol=0, atol=5e-6)
    # Extrusion: pressure-matched subtraction, not MicroActive's accumulating values.
    np.testing.assert_allclose(embedded.cum_volume, external.cum_volume, rtol=0, atol=5e-6)
    assert embedded.cum_volume.min() > -1.0 and vendor[:, 1].min() < -40


def blank_combo(window, index):
    from mercury_app.ui.main_window import QtWidgets
    table = window.metric_tables[index]
    for row in range(table.rowCount()):
        if table.item(row, 0).text() == '空白校正':
            widget = table.cellWidget(row, 1)
            assert isinstance(widget, QtWidgets.QComboBox)
            assert table.item(row - 1, 0).text() == '样品名称'
            return widget
    raise AssertionError('空白校正 row is missing')


def test_gui_blank_dropdown_recalculates(tmp_path, qt_runtime, monkeypatch):
    from mercury_app.ui.main_window import MainWindow

    sample, _ = legacy_file(tmp_path, blank=SYNTHETIC_BLANK)
    blank_dir = tmp_path / 'blank'
    blank_dir.mkdir()
    blank_path, _ = legacy_file(blank_dir)
    window = MainWindow()
    window.load_files([sample])
    qt_runtime.processEvents()

    combo = blank_combo(window, 0)
    assert [combo.itemText(i) for i in range(combo.count())] == ['无', 'B.SMP（文件内嵌）', '选择空白文件…']
    assert combo.currentData() == 'file'
    assert window.results[0].total_pore_volume == pytest.approx(2.2)

    window.on_blank_correction_selected(0, 'none')
    assert window.results[0].total_pore_volume == pytest.approx(2.0)
    assert blank_combo(window, 0).currentData() == 'none'

    monkeypatch.setattr(window, '_choose_blank_file', lambda start: str(blank_path))
    window.on_blank_correction_selected(0, 'browse')
    assert window.results[0].total_pore_volume == pytest.approx(0.0, abs=1e-12)
    combo = blank_combo(window, 0)
    assert combo.currentData() == 'external' and combo.currentText() == blank_path.name

    # Cancelled dialog and unreadable blank keep the current selection.
    monkeypatch.setattr(window, '_choose_blank_file', lambda start: '')
    window.on_blank_correction_selected(0, 'browse')
    assert blank_combo(window, 0).currentData() == 'external'
    bad = tmp_path / 'bad.SMP'
    bad.write_bytes(b'not an smp file')
    monkeypatch.setattr(window, '_choose_blank_file', lambda start: str(bad))
    window.on_blank_correction_selected(0, 'browse')
    assert blank_combo(window, 0).currentData() == 'external'

    window.on_blank_correction_selected(0, 'file')
    assert window.results[0].total_pore_volume == pytest.approx(2.2)
    window.close()
