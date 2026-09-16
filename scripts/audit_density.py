"""Read-only comparison of density calculations with local AutoPore reports."""
from pathlib import Path
import re
import struct
import sys

import numpy as np
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mercury_app.core import load_smp, summary_metrics
from smp_parser import SMPParser, _longest_monotonic_density_run


def paired_density_table(path):
    data = path.read_bytes()
    offset, size = SMPParser()._parse_directory(data)[0x273]
    payload = data[offset:offset + size]
    best = []
    for start in range(len(payload) - 16):
        run = []
        for pos in range(start, len(payload) - 16, 17):
            flag = payload[pos]
            temp, density = struct.unpack_from('<dd', payload, pos + 1)
            if not (flag == 1 and 0 < temp < 100 and 13 < density < 14):
                break
            run.append((temp, density))
        run = _longest_monotonic_density_run(run)
        if len(run) > len(best):
            best = run
    return best


def audit():
    rows = []
    for path in sorted(ROOT.parent.glob('*.SMP')):
        report = path.with_suffix('.pdf')
        if not report.exists():
            continue
        try:
            text = PdfReader(report).pages[0].extract_text()
            bulk = re.search(r'Bulk density at ([\d,.]+) psia:\s*([\d,.]+)', text)
            skeletal = re.search(r'Apparent \(skeletal\) density at [\d,.]+ psia:\s*([\d,.]+)', text)
            porosity = re.search(r'Porosity:\s*([\d,.]+)', text)
            if not all((bulk, skeletal, porosity)):
                continue
            result = load_smp(path)
            summary = summary_metrics(result)
            table = paired_density_table(path)
            mercury = float(np.interp(result.metadata['mercury_temperature_C'], *np.array(table).T))
            mass = result.metadata['sample_mass_g']
            volume = result.metadata['penetrometer_bulb_volume_mL'] - result.metadata['mercury_mass_g'] / mercury
            new_bulk = mass / volume
            new_skeletal = mass / (volume - summary.total_intrusion_volume * mass)
            new_porosity = 100 * summary.total_intrusion_volume * new_bulk
            official = [float(v.replace(',', '')) for v in (bulk[2], skeletal[1], porosity[1])]
            candidate = [new_bulk, new_skeletal, new_porosity]
            actual = [summary.bulk_density, summary.apparent_density, summary.porosity]
            matched = all(abs(a-b) <= 0.000051 for a,b in zip(official,actual))
            assert np.allclose(actual, candidate, atol=1e-9, rtol=0), path.name
            rows.append((path.name, matched, official, actual))
            print(path.name, 'OK' if matched else 'DIFF', 'report', official, 'app', [round(v,7) for v in actual], 'P', bulk[1], 'T', result.metadata['mercury_temperature_C'], flush=True)
        except Exception as exc:
            print(path.name, type(exc).__name__, str(exc), flush=True)
    print('TOTAL', len(rows), 'MATCHED', sum(row[1] for row in rows))
    if rows:
        print('MAX_ABSOLUTE_ERRORS', np.max([np.abs(np.array(row[2])-row[3]) for row in rows], axis=0))
        print('EXACT_4DP_ROWS', sum(all(f'{a:.4f}' == f'{b:.4f}' for a,b in zip(row[2],row[3])) for row in rows))
    return 0 if rows and all(row[1] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(audit())
