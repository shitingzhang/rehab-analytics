"""Complete, immutable result batches for the post-audit models only.

Consumers pin a validated batch once. Failed attempts retain their files for
diagnosis without becoming current. This is not a transaction across analyses.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_status(root, value):
    with tempfile.TemporaryDirectory(dir=root, prefix='.status-') as tmp:
        path = Path(tmp) / 'run_status.json'
        with path.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, root / 'run_status.json')


@contextmanager
def writer_lock(root):
    path = root / '.writer.lock'
    try:
        handle = path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise RuntimeError('Model publication locked; inspect any running/interrupted writer.') from exc
    try:
        with handle:
            json.dump({'pid': os.getpid(), 'started_utc': now()}, handle)
        yield
    finally:
        path.unlink()


def validate_batch(root, reference):
    root = Path(root).resolve()
    relative = reference.get('directory', '')
    if not isinstance(relative, str) or not re.fullmatch(r'runs/[A-Za-z0-9_-]+', relative):
        raise ValueError('Invalid model batch directory')
    batch = (root / relative).resolve()
    if not batch.is_relative_to(root):
        raise ValueError('Model batch escapes output root')
    manifest_path = batch / 'manifest.json'
    if manifest_path.resolve().parent != batch or digest(manifest_path) != reference.get('manifest_sha256'):
        raise ValueError('Model manifest checksum/path mismatch')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format_version') != 1 or manifest.get('run_id') != batch.name:
        raise ValueError('Invalid model manifest')
    files = manifest.get('files', [])
    names = [item['filename'] for item in files]
    if not names or len(names) != len(set(names)):
        raise ValueError('Empty or duplicate model outputs')
    if set(p.name for p in batch.iterdir()) != set(names) | {'manifest.json'}:
        raise ValueError('Missing or unexpected model output')
    for item in files:
        name = item['filename']
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) or name == 'manifest.json':
            raise ValueError('Invalid output name')
        path = batch / name
        if path.resolve().parent != batch or digest(path) != item['sha256']:
            raise ValueError(f'Model output checksum/path mismatch: {name}')
    return batch


def _reference_from_status(root, status):
    ref = status.get('current_batch')
    if not isinstance(ref, dict):
        raise RuntimeError('No successful model batch; run the model first. Root snapshots are not current.')
    validate_batch(root, ref)
    return ref


def current_reference(root):
    """Select a complete batch only if the latest recorded attempt succeeded."""
    root = Path(root)
    status = json.loads((root / 'run_status.json').read_text(encoding='utf-8'))
    if status.get('status') != 'success':
        raise RuntimeError('Latest model attempt is not successful; old results are historical, not current.')
    return _reference_from_status(root, status)


def current_result_dir(root):
    """Pin current results once; reject failed/incomplete latest attempts."""
    return validate_batch(root, current_reference(root))


def last_successful_reference(root):
    """Explicit historical access; does not establish success of the latest attempt."""
    root = Path(root)
    status = json.loads((root / 'run_status.json').read_text(encoding='utf-8'))
    return _reference_from_status(root, status)


def last_successful_result_dir(root):
    """Read the retained successful batch for audit/recovery, never as a rerun status."""
    return validate_batch(root, last_successful_reference(root))


def manifest_for(batch):
    return json.loads((Path(batch) / 'manifest.json').read_text(encoding='utf-8'))


def model_inputs(script):
    from input_provenance import require_database
    provenance = require_database()
    return list(dict.fromkeys(provenance + [Path(p) for p in [
        'data/processed/irf_analytics.db', 'data/processed/ccn_crosswalk.csv',
        'data/raw/irf_conditions.csv', 'data/raw/pac_irf_by_provider.csv',
        'src/irf_common.py', 'src/mode_mix_post_audit.py', 'src/strict_regression.py',
        'src/model_publication.py', script]]))


def fingerprint_inputs(paths):
    result = {}
    for path in paths:
        path = Path(path)
        if path.suffix == '.db' and any(Path(str(path) + s).exists() for s in ('-wal', '-shm', '-journal')):
            raise RuntimeError('Database journal/WAL present; finish writes before fitting models.')
        result[path.as_posix()] = digest(path)
    return result


def require_matching_inputs(primary, inputs):
    """A sensitivity cannot silently attach to results built from other inputs."""
    saved = manifest_for(primary)['inputs']
    for key, value in inputs.items():
        if key in saved and saved[key] != value:
            raise RuntimeError(f'Primary batch input changed: {key}; rerun step 12 first.')


@contextmanager
def publish_batch(root, expected_files, inputs, dependencies=None):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    expected = set(expected_files)
    with writer_lock(root):
        status_file = root / 'run_status.json'
        old = json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
        previous = old.get('current_batch')
        if previous is not None:
            validate_batch(root, previous)
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ_') + uuid.uuid4().hex[:8]
        attempt = {'format_version': 1, 'attempt_id': run_id, 'started_utc': now(), 'current_batch': previous}
        write_status(root, {**attempt, 'status': 'incomplete'})
        stage = None
        try:
            before = fingerprint_inputs(inputs)
            # Keep the directory on failure, including any partial diagnostics.
            stage = Path(tempfile.mkdtemp(dir=root, prefix='.staging-'))
            yield stage
            if fingerprint_inputs(inputs) != before:
                raise RuntimeError('Model inputs changed during analysis')
            if {p.name for p in stage.iterdir()} != expected:
                raise ValueError('Model output set differs from the required complete batch')
            files = [{'filename': p.name, 'sha256': digest(p), 'bytes': p.stat().st_size}
                     for p in sorted(stage.iterdir())]
            manifest = {'format_version': 1, 'run_id': run_id, 'completed_utc': now(),
                        'inputs': before, 'dependencies': dependencies or {}, 'files': files,
                        'environment': {'python': sys.version, **{name: importlib.metadata.version(name)
                            for name in ('numpy', 'pandas', 'scipy', 'statsmodels')}}}
            (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            (root / 'runs').mkdir(exist_ok=True)
            batch = root / 'runs' / run_id
            os.replace(stage, batch)
            stage = batch
            ref = {'directory': f'runs/{run_id}', 'manifest_sha256': digest(batch / 'manifest.json')}
            validate_batch(root, ref)
            write_status(root, {**attempt, 'status': 'success', 'completed_utc': now(), 'current_batch': ref})
        except BaseException as exc:
            failure = {**attempt, 'status': 'failed', 'completed_utc': now(),
                       'error': f'{type(exc).__name__}: {exc}'}
            if stage is not None and stage.exists():
                failure['unpublished_directory'] = stage.relative_to(root).as_posix()
            try:
                write_status(root, failure)
            except Exception as status_error:
                print(f'Failed to write attempt status: {status_error}; inspect output directory.', file=sys.stderr)
            raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python src/model_publication.py OUTPUT_ROOT')
    print(current_result_dir(sys.argv[1]))
