"""Single-source documentation, safe rendering, example checks and static export."""
import argparse
import json
from pathlib import Path
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import unicodedata
import urllib.parse
import uuid
from .version import __version__
from .resources import resource_directory

EXAMPLE_STEP_LIMIT = 30000
EXAMPLE_TIMEOUT_SECONDS = 10


class DocumentationError(ValueError):
    pass


def markdown_parser():
    from markdown_it import MarkdownIt
    # CommonMark's default permits arbitrary HTML; documentation never needs it.
    from .code_presentation import render_code
    parser = MarkdownIt('commonmark', {'html': False, 'linkify': False}).enable('table')
    def fence(renderer, tokens, index, options, env):
        token = tokens[index]
        return render_code(token.content, token.info, readable=True)
    def code_block(renderer, tokens, index, options, env):
        return render_code(tokens[index].content, 'text')
    parser.add_render_rule('fence', fence)
    parser.add_render_rule('code_block', code_block)
    return parser


def heading_slug(text):
    text = unicodedata.normalize('NFKC', text).lower()
    return 'h-' + (re.sub(r'[^\w\-\s]', '', text).strip().replace(' ', '-') or 'section')


def _text(tokens):
    return ' '.join(token.content for token in tokens if token.type in ('text', 'code_inline', 'fence', 'code_block'))


def _all_tokens(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _all_tokens(token.children)


def _contained_file(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise DocumentationError('Invalid documentation path')
    if posixpath.normpath(relative) != relative:
        raise DocumentationError('Documentation path must be canonical')
    parts = Path(relative).parts
    if Path(relative).is_absolute() or any(part in ('.', '..') for part in parts):
        raise DocumentationError('Documentation path escapes its root')
    path = root / relative
    if any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)):
        raise DocumentationError('Documentation files cannot be symbolic links')
    if not path.is_file():
        raise DocumentationError('Missing documentation file: ' + relative)
    return path


def api_reference():
    from .openapi import specification
    spec = specification()
    lines = ['\n## 当前接口目录\n', '下表直接由当前版本的 OpenAPI 定义生成，所有路径以 `/api/v1` 为前缀。\n', '| 方法 | 路径 | 操作 |', '| --- | --- | --- |']
    for path, methods in spec['paths'].items():
        for method, item in methods.items():
            if method not in ('get', 'post', 'put', 'patch', 'delete', 'options', 'head'): continue
            lines.append('| `' + method.upper() + '` | `' + path + '` | `' + item.get('operationId', '') + '` |')
    return '\n'.join(lines) + '\n'


class Documentation:
    def __init__(self, root=None, examples=None, web=None):
        self.root = Path(root) if root is not None else resource_directory('docs')
        self.examples = Path(examples) if examples is not None else resource_directory('examples')
        self.web = Path(web) if web is not None else resource_directory('web')
        self._lock = threading.RLock()

    def entries(self):
        path = _contained_file(self.root, 'catalog.json')
        try: data = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError) as exc: raise DocumentationError('Invalid documentation catalog') from exc
        if not isinstance(data, dict) or data.get('schemaVersion') != 1 or not isinstance(data.get('pages'), list):
            raise DocumentationError('Unsupported documentation catalog')
        seen, paths, result = set(), set(), []
        for row in data['pages']:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in ('id', 'path', 'title', 'section', 'description')):
                raise DocumentationError('Invalid documentation catalog entry')
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', row['id']) or row['id'] in seen or row['path'] in paths:
                raise DocumentationError('Duplicate or invalid documentation identifier')
            if not row['path'].endswith('.md'): raise DocumentationError('Documentation source must be Markdown')
            _contained_file(self.root, row['path'])
            seen.add(row['id']); paths.add(row['path']); result.append(dict(row))
        if not result: raise DocumentationError('Documentation catalog is empty')
        return result

    def _source(self, entry):
        body = _contained_file(self.root, entry['path']).read_text(encoding='utf-8')
        if entry['id'] == 'http-api': body += api_reference()
        return body

    def source(self, identifier):
        """Read registered Markdown without an HTML renderer or model request."""
        with self._lock:
            for entry in self.entries():
                if entry['id'] == identifier:
                    return {**entry, 'version': __version__, 'markdown': self._source(entry)}
        raise DocumentationError('Unknown documentation topic: ' + str(identifier))

    def _load(self, entry):
        body = self._source(entry)
        parser = markdown_parser(); tokens = parser.parse(body)
        headings, counts = [], {}
        for index, token in enumerate(tokens):
            if token.type == 'heading_open':
                title = _text(tokens[index + 1].children or [])
                slug = heading_slug(title); counts[slug] = counts.get(slug, 0) + 1
                if counts[slug] > 1: slug += '-' + str(counts[slug])
                token.attrSet('id', slug)
                headings.append({'id': slug, 'title': title, 'level': int(token.tag[1:])})
        return body, parser, tokens, headings

    def _target(self, entry, url, entries, headings):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme.lower() not in ('https', 'http', 'mailto') or parsed.username or parsed.password:
                raise DocumentationError('Unsafe documentation link')
            return ('external', url, None)
        if parsed.query: raise DocumentationError('Local documentation links cannot have query strings')
        fragment = urllib.parse.unquote(parsed.fragment)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith('/'):
            if path in ('/', '/control', '/docs', '/api/v1/openapi.json'): return ('application', path, None)
            raise DocumentationError('Unknown application documentation link: ' + path)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(entry['path']), path)) if path else entry['path']
        lookup = {item['path']: item for item in entries}
        if target in lookup:
            dest = lookup[target]
            anchors = {h['id'] for h in headings[dest['id']]}
            anchor = fragment if fragment in anchors else 'h-' + fragment
            if fragment and anchor not in anchors:
                raise DocumentationError('Missing heading in ' + target + ': ' + fragment)
            return ('page', dest['id'], anchor if fragment else None)
        if target.startswith('../examples/'):
            name = target[len('../examples/'):]
            if Path(name).suffix not in ('.py', '.chaos'): raise DocumentationError('Unsupported example attachment')
            _contained_file(self.examples, name)
            if fragment: raise DocumentationError('Example file links cannot target a heading')
            return ('example', name, None)
        raise DocumentationError('Unregistered documentation link: ' + target)

    def snapshot(self, *, static=False):
        return self._snapshot(static=static)

    def _snapshot(self, *, static=False, render_ids=None):
        # Always read and validate every source; HTML is only built for consumers
        # that need it. Public snapshots and static exports still render all pages.
        with self._lock:
            entries = self.entries()
            loaded = {entry['id']: self._load(entry) for entry in entries}
            headings = {key: value[3] for key, value in loaded.items()}
            pages = []
            for entry in entries:
                body, parser, tokens, toc = loaded[entry['id']]
                for token in _all_tokens(tokens):
                    if token.type == 'link_open':
                        kind, dest, anchor = self._target(entry, token.attrGet('href') or '', entries, headings)
                        if kind == 'page': token.attrSet('href', '#/' + dest + ('/' + urllib.parse.quote(anchor) if anchor else ''))
                        elif kind == 'example': token.attrSet('href', ('examples/' if static else '/docs/files/examples/') + urllib.parse.quote(dest, safe='/'))
                        elif kind == 'external': token.attrSet('rel', 'noreferrer noopener')
                        elif kind == 'application' and static:
                            # Offline exports cannot reach a local application's relative API.
                            token.attrSet('href', 'http://127.0.0.1:8765' + dest)
                    elif token.type == 'image':
                        # Docs are text-first; prevent arbitrary remote asset loads or data URLs.
                        token.type = 'text'; token.content = token.content or 'Image'; token.tag = ''; token.attrs = {}; token.children = None
                page = {**entry, 'markdown': body, 'html': parser.renderer.render(tokens, parser.options, {}) if render_ids is None or entry['id'] in render_ids else '', 'headings': toc,
                        'searchText': _text(_all_tokens(tokens)), 'version': __version__, 'languageVersion': '2'}
                pages.append(page)
            return {'version': __version__, 'languageVersion': '2', 'mode': 'static' if static else 'live', 'pages': pages}

    def catalog(self):
        snapshot = self._snapshot(render_ids=())
        snapshot['pages'] = [{k: v for k, v in row.items() if k not in ('markdown', 'html')} for row in snapshot['pages']]
        return snapshot

    def page(self, identifier):
        for page in self._snapshot(render_ids=(identifier,))['pages']:
            if page['id'] == identifier: return page
        raise KeyError(identifier)

    def attachment(self, name):
        if Path(name).suffix not in ('.py', '.chaos'): raise DocumentationError('Unsupported example attachment')
        return _contained_file(self.examples, name).read_bytes()

    def check(self):
        snapshot = self.snapshot()
        included = {row['path'] for row in snapshot['pages']}
        missing = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*.md') if str(path.relative_to(self.root)) not in included)
        if missing: raise DocumentationError('Markdown pages missing from catalog: ' + ', '.join(missing))
        cases = json.loads(_contained_file(self.root, 'examples.json').read_text(encoding='utf-8'))
        if not isinstance(cases, list): raise DocumentationError('Example registry must be a list')
        registry = {}
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get('id'), str) or 'expect' not in case or case['id'] in registry:
                raise DocumentationError('Invalid or duplicate example registry entry')
            registry[case['id']] = case
        snippets = {}
        for page in snapshot['pages']:
            for token in markdown_parser().parse(page['markdown']):
                if token.type != 'fence': continue
                parts = token.info.strip().split()
                if not parts or parts[0] != 'chaos': continue
                for part in parts[1:]:
                    if not part.startswith('example='): continue
                    name = part.split('=', 1)[1]
                    if not re.fullmatch(r'[a-z][a-z0-9-]*', name) or name in snippets:
                        raise DocumentationError('Invalid or duplicate executable example: ' + name)
                    snippets[name] = (token.content, page['path'])
        if set(registry) != set(snippets):
            raise DocumentationError('Executable examples and registry differ: ' + ', '.join(sorted(set(registry) ^ set(snippets))))
        from .framework import Framework
        for name, case in registry.items():
            source, page = snippets[name]
            try:
                # This is a correctness check, not a two-second CPU benchmark.
                # Keep the instruction bound and allow supported slower hosts
                # enough wall time for asynchronous standard-library examples.
                result = Framework.evaluate(source, {'input': case.get('input')}, files=case.get('files'), entry=case.get('entry', 'main.chaos'), step_limit=EXAMPLE_STEP_LIMIT, timeout=EXAMPLE_TIMEOUT_SECONDS)
                if json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False) != json.dumps(case['expect'], sort_keys=True, ensure_ascii=False, allow_nan=False):
                    raise ValueError('result differs from the documented expectation')
            except Exception as exc:
                raise DocumentationError('Example ' + name + ' in ' + page + ' failed: ' + str(exc)) from exc
        return {'version': __version__, 'pages': len(snapshot['pages']), 'examples': len(snippets), 'links': 'validated', 'status': 'passed'}

    def build(self, output):
        report = self.check()
        output = Path(output).expanduser().absolute()
        if output.is_symlink() or output == output.parent: raise DocumentationError('Invalid static site destination')
        protected = [self.root.resolve(), self.web.resolve(), self.examples.resolve()]
        resolved = output.resolve()
        for path in protected:
            if resolved == path or resolved in path.parents or path in resolved.parents:
                raise DocumentationError('Static export must be outside source resource directories')
        marker = output / '.chaos-docs-site.json'
        if output.exists() and (not output.is_dir() or (any(output.iterdir()) and not marker.is_file())):
            raise DocumentationError('Output must be empty or an existing generated documentation site')
        if marker.exists() or marker.is_symlink():
            try:
                if marker.is_symlink(): raise ValueError('Symlink marker')
                ownership = json.loads(marker.read_text(encoding='utf-8'))
                if (not isinstance(ownership, dict) or ownership.get('schemaVersion') != 1
                        or ownership.get('status') != 'passed' or not isinstance(ownership.get('version'), str)
                        or type(ownership.get('pages')) is not int or ownership['pages'] < 1):
                    raise ValueError('Invalid generated-site marker')
            except (OSError, ValueError, UnicodeError) as exc:
                raise DocumentationError('Output ownership marker is invalid; existing files preserved') from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.chaos-docs-', dir=output.parent))
        backup = output.with_name('.' + output.name + '-previous-' + uuid.uuid4().hex)
        try:
            snapshot = self.snapshot(static=True)
            payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            template = _contained_file(self.web, 'docs.html').read_text(encoding='utf-8')
            if '<!-- DOCS_BOOTSTRAP -->' not in template: raise DocumentationError('Documentation shell is missing bootstrap marker')
            template = template.replace('<!-- DOCS_BOOTSTRAP -->', '<script id="docs-bootstrap" type="application/json">' + payload + '</script>')
            (stage / 'index.html').write_text(template, encoding='utf-8')
            for name in ('theme.css', 'docs.css', 'docs.js', 'code.css', 'code.js'):
                # Custom documentation shells only need the assets they use.
                if name == 'theme.css' and 'theme.css' not in template and not ('code.css' in template and 'theme.css' in _contained_file(self.web, 'code.css').read_text(encoding='utf-8')): continue
                if name.startswith('code.') and name not in template: continue
                shutil.copyfile(_contained_file(self.web, name), stage / name)
            (stage / 'markdown').mkdir()
            for page in snapshot['pages']:
                target = stage / 'markdown' / page['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(page['markdown'], encoding='utf-8')
            for name in ('catalog.json', 'examples.json'):
                shutil.copyfile(_contained_file(self.root, name), stage / 'markdown' / name)
            for file in self.examples.rglob('*'):
                if file.is_file() and file.suffix in ('.py', '.chaos'):
                    relative = str(file.relative_to(self.examples)); source = _contained_file(self.examples, relative)
                    dest = stage / 'examples' / relative; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, dest)
            (stage / '.chaos-docs-site.json').write_text(json.dumps({'schemaVersion': 1, **report}), encoding='utf-8')
            if output.exists(): output.rename(backup)
            try: stage.rename(output)
            except Exception:
                if backup.exists(): backup.rename(output)
                raise
            if backup.exists(): shutil.rmtree(backup)
        finally:
            if stage.exists(): shutil.rmtree(stage)
        return {**report, 'output': str(output), 'published': False}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='chaos docs', description='Read, validate and export the documentation shipped with this installation')
    parser.add_argument('action', nargs='?', default='path', choices=['path', 'list', 'show', 'check', 'build'])
    parser.add_argument('topic', nargs='?', help='Exact topic ID from docs list (required for show)')
    parser.add_argument('--output', help='Generated static site directory (required for build)')
    args = parser.parse_args(argv)
    if args.action == 'build' and not args.output: parser.error('build requires --output')
    if (args.action == 'show') != (args.topic is not None): parser.error('show requires a topic; other actions do not accept a topic')
    if args.output and args.action != 'build': parser.error('--output is only valid for build')
    try:
        docs = Documentation()
        if args.action == 'list':
            print(json.dumps({'version': __version__, 'pages': docs.entries()}, ensure_ascii=False, indent=2)); return 0
        if args.action == 'show':
            body = docs.source(args.topic)['markdown']
            print(body, end='' if body.endswith('\n') else '\n'); return 0
        result = {'path': str(docs.root), 'version': __version__} if args.action == 'path' else docs.check() if args.action == 'check' else docs.build(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, KeyError, ImportError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr); return 1
