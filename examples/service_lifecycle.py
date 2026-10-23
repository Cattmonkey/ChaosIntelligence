"""Explicit OS-owned service configuration; defaults to a read-only plan.

Run with this package installed. This example never enrolls an updater,
downloads a release, or stores model credentials in an OS command line.
"""
import argparse
import json
from pathlib import Path
import sys

from chaos import ServiceManager, ServiceSpec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan', choices=('plan', 'install', 'status', 'start', 'stop', 'uninstall'))
    parser.add_argument('--name', default='workspace')
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    workspace, data = args.workspace.resolve(), args.data.resolve()
    spec = ServiceSpec(name=args.name, profile='source', python=str(Path(args.python).resolve()),
                       cwd=str(workspace), db=str(data / 'workspace.sqlite3'),
                       state_dir=str(data / 'control'), config=str(data / 'settings.json'),
                       host='127.0.0.1', port=args.port)
    manager = ServiceManager()
    result = getattr(manager, args.action)(spec if args.action in ('plan', 'install') else spec.name)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
