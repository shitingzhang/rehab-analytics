"""Screening batch validation and latest-attempt reads; no screening rule changes."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .model_publication import validate_batch

REQUIRED = ['priority_review_list.csv', 'sensitivity_grid.csv', 'fairness_flag_rates.csv',
            'screen_all_facilities.csv', 'screening_summary.txt', 'screening_scatter.png']


def current_screen_dir(root):
    root = Path(root)
    status = json.loads((root / 'run_status.json').read_text(encoding='utf-8'))
    if status.get('status') != 'success':
        raise RuntimeError('Latest screening attempt is not successful; do not use old snapshots as current.')
    reference = status.get("current_batch")
    if not isinstance(reference, dict):
        raise RuntimeError("No successful screening batch")
    return validate_batch(root, reference)


def validate_screen_files(folder):
    folder = Path(folder)
    if {p.name for p in folder.iterdir()} != set(REQUIRED):
        raise ValueError('Incomplete screening batch')
    screen = pd.read_csv(folder / 'screen_all_facilities.csv', dtype={'ccn': str})
    priority = pd.read_csv(folder / 'priority_review_list.csv', dtype={'ccn': str})
    if screen.empty or screen.ccn.isna().any() or not screen.ccn.is_unique:
        raise ValueError('Invalid screening membership')
    if priority.ccn.isna().any() or not priority.ccn.is_unique:
        raise ValueError('Invalid priority membership')
    for name in ['flag', 'robust_flag']:
        if not screen[name].isin([True, False]).all():
            raise ValueError('Invalid screening flag')
    if set(priority.ccn) != set(screen.loc[screen.flag.eq(True), 'ccn']):
        raise ValueError('Priority list does not match the primary flags')
    values = screen[['oe_alos', 'dtc_residual', 'flag_frequency']].to_numpy(float)
    if not np.isfinite(values).all() or not screen.flag_frequency.between(0, 1).all():
        raise ValueError('Invalid screening numeric output')
    for name in ['sensitivity_grid.csv', 'fairness_flag_rates.csv']:
        if pd.read_csv(folder / name).empty:
            raise ValueError('Empty screening summary table')
    if not (folder / 'screening_summary.txt').read_text(encoding='utf-8').strip():
        raise ValueError('Empty screening report')
    if not (folder / 'screening_scatter.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Invalid screening figure')
