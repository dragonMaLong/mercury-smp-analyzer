from pathlib import Path

import pytest

from mercury_app.ui.main_window import FILE_COLUMN, MainWindow, QtCore, QtWidgets, _natural_sort_key


@pytest.fixture
def sample_window(qt_runtime):
    source = Path(__file__).resolve().parents[2] / "1-QC3.0.SMP"
    if not source.exists():
        pytest.skip("Local SMP reference file is unavailable")
    window = MainWindow()
    window.load_files([source, source, source])
    for result, name in zip(window.results, ["样品10.SMP", "样品2.SMP", "样品1.SMP"]):
        result.metadata["file_name"] = name
    window._build_metric_tabs()
    yield window
    window.close()
    qt_runtime.processEvents()


def test_natural_order_matches_bet():
    assert sorted(["A10", "a2", "A1"], key=_natural_sort_key) == ["A1", "a2", "A10"]
    assert sorted(["10.SMP", "2.SMP", "01.SMP", "1.SMP"], key=_natural_sort_key) == [
        "01.SMP", "1.SMP", "2.SMP", "10.SMP",
    ]


def test_file_header_sort_preserves_sample_state(sample_window):
    window = sample_window
    originals = list(window.results)
    window.visible_results = [True, False, True]
    colors = list(window.sample_colors[:3])
    window.on_active_tab_changed(0)
    window.on_sample_header_clicked(FILE_COLUMN)
    assert [r.metadata["file_name"] for r in window.results] == ["样品1.SMP", "样品2.SMP", "样品10.SMP"]
    assert window.results[2] is originals[0]
    assert window.result is originals[0]
    assert window.active_index == 2
    assert window.visible_results == [True, False, True]
    assert window.sample_colors[:3] == list(reversed(colors))
    assert window.sample_list.horizontalHeader().sortIndicatorOrder() == QtCore.Qt.AscendingOrder
    window.on_sample_header_clicked(FILE_COLUMN)
    assert [id(r) for r in window.results] == [id(r) for r in originals]
    assert window.active_index == 0
    assert window.sample_colors[:3] == colors
    assert window.sample_list.horizontalHeader().isSortIndicatorShown()
    window._sort_pore_structure_summary(1)
    assert not window.sample_list.horizontalHeader().isSortIndicatorShown()
    assert not window.sample_list.frozen_header().isSortIndicatorShown()


def test_empty_area_menu_only_offers_clear(sample_window, monkeypatch):
    window = sample_window
    menus = []

    def choose_clear(menu, position):
        menus.append([action.text() for action in menu.actions()])
        return menu.actions()[0]

    monkeypatch.setattr(QtWidgets.QMenu, "exec_", choose_clear)
    window.show_sample_context_menu(QtCore.QPoint(1, 10000))
    assert menus == [["清空列表"]]
    assert window.results == []
    assert window.visible_results == []
    assert window.result is None
    assert window.sample_list.rowCount() == 0
    assert window.pore_structure_results == []
    assert window.mayer_stowe_results == []
    assert window.pore_structure_options == {}
    assert window.region is None
    assert window.pore_structure_pressure_region is None
    window.load_file(Path(__file__).resolve().parents[2] / "1-QC3.0.SMP")
    assert len(window.results) == 1
    assert window.pore_structure_results


def test_deleting_sample_keeps_remaining_colors(sample_window):
    window = sample_window
    originals = list(window.results)
    colors = list(window.sample_colors[:3])
    window.delete_sample(0)
    assert window.results[0] is originals[1]
    assert window.sample_colors[:2] == colors[1:]
    assert id(originals[0]) not in window.pore_structure_options


def test_delete_more_than_palette_keeps_last_sample_color(sample_window):
    window = sample_window
    source = Path(__file__).resolve().parents[2] / "1-QC3.0.SMP"
    palette_size = len(window.sample_colors)
    window.load_files([source] * (palette_size + 1))
    remaining = window.results[-1]
    expected_color = window.sample_colors[palette_size % len(window.sample_colors)]
    window.delete_samples(range(palette_size))
    assert len(window.results) == 1
    assert window.results[0] is remaining
    assert window.sample_colors[0] == expected_color
    assert window.pore_structure_results
    window.delete_sample(0)
    assert window.results == []
    assert window.sample_colors  # A later import has a nonempty palette.


def test_sort_and_move_over_palette_keep_color_bound_to_sample(sample_window):
    window = sample_window
    source = Path(__file__).resolve().parents[2] / "1-QC3.0.SMP"
    window.load_files([source] * (len(window.sample_colors) + 2))
    for number, result in enumerate(window.results):
        result.metadata["file_name"] = f"sample{number + 1}.SMP"
    colors_by_id = {id(result): window.sample_colors[index % len(window.sample_colors)]
                    for index, result in enumerate(window.results)}
    visible_by_id = {id(result): index % 2 == 0 for index, result in enumerate(window.results)}
    window.visible_results = [visible_by_id[id(result)] for result in window.results]
    active = window.results[-1]
    window.on_active_tab_changed(len(window.results) - 1)
    window.move_sample_row(len(window.results) - 1, 0)
    window.sort_samples_by_file_name(False)
    window._sort_pore_structure_summary(1)
    window.sort_samples_by_file_name(True)
    assert window.results[0].metadata["file_name"] == "sample1.SMP"
    assert window.result is active
    assert window.results[window.active_index] is active
    assert window.sample_colors[:len(window.results)] == [colors_by_id[id(result)] for result in window.results]
    assert window.visible_results == [visible_by_id[id(result)] for result in window.results]
    table = window.pore_structure_summary_table
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        window._sample_row_label(result) for result in window.results
    ]
