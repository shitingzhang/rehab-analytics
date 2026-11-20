"""Fail-closed receipts for the supported 01 -> 02 -> 12/13 CLI chain.

Receipts bind content, not scientific validity. Lower-level cleaning/build functions
remain usable in synthetic tests; only the CLI wrappers issue production receipts.
No automatic signing of existing files, cross-file rollback or crash durability claim.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

RAW = tuple('data/raw/' + name for name in (
    'pac_irf_by_provider.csv', 'irf_general_information.csv',
    'irf_provider_data.csv', 'irf_conditions.csv'))
CLEAN = tuple('data/processed/' + name for name in (
    'clean_facility_cohort.csv', 'irf_qrp_long_clean.csv', 'ccn_crosswalk.csv',
    'pac_national_state_reference.csv', 'irf_measure_periods.csv', 'audit_log.csv'))
HELPER = 'src/_infra/input_provenance.py'
CLEAN_INPUTS = RAW + ('src/01_data_quality_cms.py', HELPER)
CLEAN_RECEIPT = 'data/processed/clean_provenance.json'
DB_RECEIPT = 'data/processed/database_provenance.json'
DB = 'data/processed/irf_analytics.db'
DB_INPUTS = CLEAN_INPUTS + CLEAN + (CLEAN_RECEIPT, 'src/02_build_database.py')
LOCK = 'data/processed/.source-chain.lock'


def snapshot(root, names):
    values = {}
    for name in names:
        path = root / name
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        values[name] = h.hexdigest()
    return values


def write_receipt(root, name, state):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.provenance-') as temporary:
        pending = Path(temporary) / 'receipt.json'
        pending.write_text(json.dumps(state, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        os.replace(pending, target)


def validate_receipt(root, name, stage, inputs, outputs):
    try:
        value = json.loads((root / name).read_text(encoding='utf-8'))
        if (value.get('format_version') != 1 or value.get('stage') != stage
                or value.get('status') != 'success'):
            raise ValueError('receipt is not a completed success')
        for label, names in (('inputs', inputs), ('outputs', outputs)):
            saved = value.get(label)
            if not isinstance(saved, dict) or set(saved) != set(names):
                raise ValueError(f'{label} file set differs')
            current = snapshot(root, names)
            changed = [key for key in names if current[key] != saved[key]]
            if changed:
                raise ValueError(f'{label} changed: {", ".join(changed)}')
        return value
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f'Input provenance rejected ({name}): {exc}. '
                           'Rebuild from 01, then 02, before running 12/13.') from exc


def validate_clean(root):
    return validate_receipt(root, CLEAN_RECEIPT, 'clean', CLEAN_INPUTS, CLEAN)


def require_database(root=Path('.')):
    root = Path(root).resolve()
    if (root / LOCK).exists():
        raise RuntimeError('Source chain is being rebuilt; wait for 01/02 to finish.')
    if any(Path(str(root / DB) + ending).exists() for ending in ('-wal', '-shm', '-journal')):
        raise RuntimeError('Database has an active journal; finish writes before analysis.')
    validate_clean(root)
    validate_receipt(root, DB_RECEIPT, 'database', DB_INPUTS, (DB,))
    # Include receipts and upstream content in model pre/post fingerprints as well.
    return [Path(name) for name in (*DB_INPUTS, DB_RECEIPT, DB)]


@contextmanager
def recorded_stage(stage, root=Path('.')):
    root = Path(root).resolve()
    if stage not in ('clean', 'database'):
        raise ValueError('Unknown provenance stage')
    receipt, inputs, outputs = ((CLEAN_RECEIPT, CLEAN_INPUTS, CLEAN) if stage == 'clean'
                               else (DB_RECEIPT, DB_INPUTS, (DB,)))
    lock = root / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError('Source chain writer lock exists; inspect the running process first.') from exc
    os.close(fd)
    base = {'format_version': 1, 'stage': stage}
    try:
        write_receipt(root, receipt, {**base, 'status': 'incomplete'})
        if stage == 'database':
            validate_clean(root)
        before = snapshot(root, inputs)

        def unchanged():
            if snapshot(root, inputs) != before:
                raise RuntimeError('Source-chain inputs changed during this stage; rebuild from 01.')
            if stage == 'database':
                validate_clean(root)

        yield unchanged
        unchanged()
        result = snapshot(root, outputs)
        unchanged()
        write_receipt(root, receipt, {**base, 'status': 'success', 'inputs': before, 'outputs': result})
    except BaseException as exc:
        try:
            write_receipt(root, receipt, {**base, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
        except OSError:
            # A retained incomplete/mismatched receipt also fails closed.
            pass
        raise
    finally:
        lock.unlink()
