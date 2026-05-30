import struct

with open("008-804.smp", "rb") as f:
    data = f.read()

lines = []

lines.append("=== 空白区域完整数据，从29262开始 ===")
lines.append(f"{'#':>4}  {'体积原始值':>14}  {'压力(psia)':>14}  {'换算体积(mL/g)':>16}")

count = 0
for i in range(200):
    off = 29262 + i * 24
    if off + 24 > len(data):
        break
    vol_raw = struct.unpack_from('<d', data, off)[0]
    timestamp = struct.unpack_from('<d', data, off+8)[0]
    pressure  = struct.unpack_from('<d', data, off+16)[0]

    # 有效记录：体积是负数，压力在合理范围内
    if not (-20 < vol_raw < 0 and 0.1 < pressure < 60000):
        lines.append(f"  第{i}条记录不符合，停止: vol={vol_raw:.4f} p={pressure:.4f}")
        break

    vol_mLg = abs(vol_raw) * 0.25128
    lines.append(f"{count:>4}  {vol_raw:>14.6f}  {pressure:>14.4f}  {vol_mLg:>16.6f}")
    count += 1

lines.append(f"\n总记录数: {count}")

with open("smp_complete2.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print("已写入 smp_complete2.txt")