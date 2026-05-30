"""
example.py — SMP 解析器使用示例
"""
from smp_parser import SMPParser, export_csv, print_summary

# ── 1. 基本使用：全自动，无需手动输入任何参数 ─────────────────────

parser = SMPParser()
smp    = parser.parse("008-804.SMP")       # 修改为你的文件路径
result = parser.calculate(smp)

print_summary(smp, result)
export_csv(result, "output.csv", smp)

# ── 2. 打印前 10 个数据点 ──────────────────────────────────────────

print(f"\n{'压力(psia)':>14}  {'孔径(nm)':>14}  {'累积体积(mL/g)':>16}  {'增量(mL/g)':>14}")
print("-" * 66)
for r in result[:10]:
    print(f"{r.pressure_psia:>14.4f}  {r.pore_diameter_nm:>14.1f}  "
          f"{r.cumulative_volume_mLg:>16.6f}  {r.incremental_volume_mLg:>14.6f}")

# ── 3. 研究接触角的影响（无需重测）────────────────────────────────

print("\n── 接触角对孔径分布的影响 ───")
print(f"{'接触角':>8}  {'第1非零孔径(nm)':>16}  {'总孔体积(mL/g)':>16}")
for angle in [110.0, 120.0, 130.0, 140.0]:
    r2 = parser.calculate(smp, contact_angle_deg=angle)
    d1 = next((r.pore_diameter_nm for r in r2 if r.cumulative_volume_mLg > 0), 0)
    print(f"{angle:>8.1f}°  {d1:>16.1f}  {r2[-1].cumulative_volume_mLg:>16.4f}")

# ── 4. 查看材料属性 ────────────────────────────────────────────────

m = smp.material
print(f"\n── 材料属性 ───")
print(f"  材料名称        : {m.name}")
print(f"  BET 比表面积    : {m.bet_surface_area_m2g} m²/g")
print(f"  阈值压力        : {m.threshold_pressure_psia} psia")
print(f"  线性压缩系数    : {m.linear_compressibility:.2e} 1/psia")
print(f"  二次压缩系数    : {m.quadratic_compressibility:.2e} 1/psia²")
print(f"  真密度（修正后）: {smp.recovered_true_density_gmL:.4f} g/mL")

# ── 5. 查看压力程序 ────────────────────────────────────────────────

print(f"\n── 压力程序（前 5 段）───")
print(f"{'#':>4}  {'结束压力(psia)':>16}  {'平衡时间(s)':>12}")
for i, step in enumerate(smp.pressure_program[:5]):
    print(f"{i:>4}  {step.end_pressure_psia:>16.2f}  {step.equilibration_time:>12.1f}")

# ── 6. LP 缓冲数据（LP 端口原始采集）─────────────────────────────

print(f"\n── LP 端口缓冲数据（共 {len(smp.lp_buffer_points)} 个点）───")
if smp.lp_buffer_points:
    last = smp.lp_buffer_points[-1]
    print(f"  压力范围: {smp.lp_buffer_points[0].pressure_psia:.2f} ~ {last.pressure_psia:.2f} psia")
