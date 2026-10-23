"""Persist example model settings from environment variables, without printing keys.

Copy this file beside chaos.py, then run: python3 configure_models.py --project .
It also works with an installed Chaos package. No network call unless --discover is used.
"""
import argparse
from contextlib import contextmanager, nullcontext
import runpy
import json
import os
from pathlib import Path
import sys


PROVIDERS = {
    'text': 'CHAOS_TEXT', 'image1k': 'CHAOS_IMAGE_1K',
    'image4k': 'CHAOS_IMAGE_4K', 'grok': 'CHAOS_GROK',
}
ROLES = {'planner': 'CHAOS_PLANNER_MODEL', 'executor': 'CHAOS_EXECUTOR_MODEL',
         'reviewer': 'CHAOS_REVIEWER_MODEL'}


def configure(settings, environment, *, fresh=False):
    """Use the existing Settings CAS/validation/0600 persistence contract."""
    if any(environment.get(k) for k in ('CHAOS_MODEL_URL', 'CHAOS_MODEL_KEY', 'CHAOS_MODEL_NAME')):
        raise ValueError('Unset CHAOS_MODEL_URL/KEY/NAME before using persistent settings')
    current = settings.public()
    patch = {'providers': {}, 'roles': {}}
    common_url = environment.get('CHAOS_BASE_URL')
    for provider, prefix in PROVIDERS.items():
        fields = {}
        for field, suffix in (('apiKey', '_KEY'), ('model', '_MODEL')):
            if environment.get(prefix + suffix):
                fields[field] = environment[prefix + suffix]
        url = environment.get(prefix + '_BASE_URL') or common_url
        if url:
            fields['baseURL'] = url
        if fields:
            patch['providers'][provider] = fields
    for role, variable in ROLES.items():
        if environment.get(variable):
            patch['roles'][role] = {'model': environment[variable], 'autoUpgrade': False}
    if fresh:
        patch['runtime'] = {'workspace': '.chaos/tasks'}
    public = settings.update(patch, version=current['version'])
    # Only report names/booleans. No key fragments, routes or raw SDK exceptions.
    return {'version': public['version'], 'providers': {
        name: {'configured': value['hasKey']} for name, value in public['providers'].items()
    }}


@contextmanager
def project_imports(root):
    """Use the same bootstrap selection and lock as chaos.py; never pin the base runtime."""
    previous = sys.path[:]
    excluded = {root, Path(__file__).resolve().parent}
    sys.path[:] = [p for p in sys.path if Path(p or '.').resolve() not in excluded]
    try:
        launcher = root / 'chaos.py'
        if launcher.is_symlink() or not launcher.is_file():
            raise ValueError('Expected the project bootstrap beside chaos.project.json')
        bootstrap = runpy.run_path(str(launcher), run_name='chaos_configuration_bootstrap')
        select = bootstrap.get('environment')
        lock = bootstrap.get('environment_lock')
        modern = callable(select) and callable(lock)
        if not modern and (root / '.chaos/environment.json').exists():
            raise ValueError('Use a bootstrap that supports the selected project environment')
        with (lock(root) if modern else nullcontext()):
            env = select(root) if modern else root / '.chaos'
            runtime, deps = env / 'runtime', env / 'deps'
            for path in (env, runtime, deps):
                if path.is_symlink():
                    raise ValueError('Project environment cannot be a symlink')
            bundled = (root / '.chaos/bundled-runtime').exists() or (root / '.chaos/environment.json').exists()
            if bundled and not (runtime / 'chaos/__init__.py').is_file():
                raise ValueError('Selected runtime is missing')
            legacy = env == root / '.chaos' and not (deps / '.chaos-dependencies.json').exists()
            ready = bootstrap['dependencies_ready'](env) if modern else False
            if deps.is_dir() and (ready or legacy):
                sys.path.insert(0, str(deps))
            if runtime.is_dir():
                sys.path.insert(0, str(runtime))
            yield runtime
    finally:
        sys.path[:] = previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.', help='Directory containing chaos.py and chaos.project.json')
    parser.add_argument('--discover', nargs='+', choices=tuple(PROVIDERS),
                        help='Explicitly contact these providers to refresh their model catalogs')
    args = parser.parse_args(argv)
    root = Path(args.project).expanduser().resolve()
    if not (root / 'chaos.project.json').is_file():
        parser.error('Use an initialized Chaos directory project')
    try:
        with project_imports(root) as runtime:
            import chaos
            if runtime.is_dir():
                Path(chaos.__file__).resolve().relative_to(runtime.resolve())
            from chaos.settings import Settings
            from chaos.project import Project
            project = Project(root)
            config = project.path('.chaos/settings.json')
            project.path('.chaos/settings.json.lock')
            result = configure(Settings(config), os.environ, fresh=not config.exists())
            if args.discover:
                settings = Settings(config)
                result['discovered'] = {
                    provider: [row['id'] for row in settings.discover(provider)['models']]
                    for provider in args.discover
                }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except Exception as error:
        # Error text can contain provider-controlled data. Keep secrets out of the terminal.
        print(json.dumps({'error': type(error).__name__,
                          'hint': 'Check project dependencies, environment variable names and provider settings; no credentials were printed.'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
