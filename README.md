# 压汞 SMP 分析工具

这是一个用于查看和比较 Micromeritics AutoPore / MicroActive `.SMP` 压汞数据文件的桌面软件。

当前目标是提供一个轻量的单文件/多文件分析流程：导入一个或多个 SMP 文件，计算类 MicroActive 的孔结构数据，比较孔径分布，临时调整 Washburn 方程关键参数，并将选中的结果导出到 Excel。

## 功能

- 通过“导入文件”窗口从文件夹中选择一个或多个 `.SMP` 文件。
- 支持将 `.SMP` 文件直接拖入软件导入。
- 显示样品元数据、测试时间、测试仪器、软件版本、测试人员、提交者、膨胀计常数、样品质量、类 MicroActive 汇总参数和最大压力。
- 绘图：
  - 孔径分布：作为主图显示，横坐标为对数孔径，曲线使用 Akima 插值平滑显示。
  - 压力 - 累计孔体积。
- 通过样品列表左侧的蓝色圆点控制每个样品显示/隐藏。
- 按测试时间对已加载文件排序。
- 在样品列表中编辑进汞接触角和汞表面张力。
- 修改参数后实时重新计算孔径和孔径分布。
- 交互式选择压力范围，并实时更新孔径范围、孔容占比和峰值孔径等参数。
- 将所有选中的样品导出到一个 `.xlsx` 工作簿。
- 通过“软件更新”检查并安装新版本。

## 项目结构

```text
mercury_smp_app_zh/
  app.py
  requirements.txt
  smp_parser.py
  mercury_app/
    core/
      excel_export.py
      metrics.py
      microactive_calc.py
      models.py
      smp_parser.py
    ui/
      main_window.py
      plots.py
  tests/
    test_smp_vs_xls.py
```

## 安装

建议使用 Python 3.10 或更新版本。

```powershell
python -m pip install -r requirements.txt
```

## 启动图形界面

```powershell
python app.py --ui
```

如果 SMP 文件不在本文件夹内，也可以在“导入文件”窗口中选择对应文件夹。

## 命令行运行

```powershell
python app.py D:\path\to\sample.SMP
```

可选 CSV 导出：

```powershell
python app.py D:\path\to\sample.SMP --csv output.csv
```

## Excel 导出

在界面中点击 `导出文件`。软件只导出蓝色圆点处于选中状态的样品。

工作簿包含：

- `汇总`：每个选中样品一行。
- 每个样品一个独立工作表。
- 压力、孔径、累计孔体积、增量孔体积、平滑后的 `dV/dlogD` 和百分比列。

默认不导出未平滑的微分列，以保持表格简洁。

## 计算说明

孔径通过 Washburn 方程计算，使用：

- 进汞接触角
- 汞表面张力
- 修正后的压力

SMP 文件中可能包含这些方法参数。只要文件内存在有效值，软件会优先使用 SMP 文件中保存的值。如果没有有效值，解析器会回退到常见的 Micromeritics 风格默认值：

- 进汞接触角：`130°`
- 表面张力：`485 dynes/cm`

界面允许你对每个样品临时覆盖接触角和表面张力。这些修改只保存在内存中，不会改写原始 SMP 文件。

孔径分布显示使用平滑后的 `dV/dlogD` 值，并在 `log10(孔径)` 上绘制高密度 Akima 插值曲线。圆点仍然保留为实际计算数据点。

汇总参数尽量遵循 MicroActive 报告风格：

- 总孔面积按区间计算：`4000 * 增量孔体积 / 区间中点孔径`。
- 体积中值孔径和面积中值孔径分别在半总孔体积、半总孔面积处插值得到压力，再用同一压力-孔径关系换算。
- 平均孔径使用 `4V/A`。
- 表观骨架密度和孔隙率由体积密度和总入汞体积推算。

## 测试

测试会在本地存在 SMP/XLS 参考文件时运行数据校验。如果样品文件不在项目旁边，相关测试会自动跳过。

```powershell
pytest -q
```

## 状态

这是一个聚焦单文件和多文件查看流程的早期版本。它还不是 MicroActive 报告、数据库流程或批量生产报告的完整替代品。
