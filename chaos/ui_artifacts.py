"""Owned, immutable UI documents; OpenUI is a formal data format, not an executor.

The shared component catalog defines the positional grammar. Computation and
callbacks remain ordinary Chaos functions driven by its existing AST scheduler.
"""
import copy
import base64
import hashlib
import html
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_SOURCE = 100000
MAX_DEFINITION = 200000
MAX_NODES = 10000
MAX_DEPTH = 64
MAX_ACTIONS = 128
MAX_REQUIREMENTS = 128
MAX_TASKS = 64
SAFE_INT = 9007199254740991
KINDS = ('page', 'report', 'slides')
_CATALOG = json.loads(Path(__file__).with_name('ui_catalog.json').read_text(encoding='utf-8'))
_COMPONENTS = _CATALOG['$defs']
_RESERVED = {'Action', 'Query', 'Mutation'}
if _RESERVED.intersection(_COMPONENTS): raise RuntimeError('Reserved OpenUI component name')
LEGACY_LIBRARIES = {'53877d77c738ba646bfd355647f489a9671f2bfadf6194a77c3a90888c2a6d1b': frozenset(('Stack','Heading','Text','Card','Metric','Table','BarChart','Tabs','Tab','Disclosure','Code','ActionButton','Slides','Slide'))}
LIBRARY_HASH = hashlib.sha256(json.dumps(_CATALOG, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _error(message, code='ui_error'):
    from .language import LanguageError
    raise LanguageError(message, code=code)


def _limit(message):
    from .language import ResourceLimit
    raise ResourceLimit(message)


def _dump(value, *, sorted_keys=False):
    return json.dumps(value, ensure_ascii=False, sort_keys=sorted_keys, separators=(',', ':'), allow_nan=False)


def catalog(): return copy.deepcopy(_CATALOG)


def data(value, *, tick=None):
    """Closed JSON projection: no protocols, live handles, or host subclasses."""
    count, chars, active = [0], [0], set()
    def visit(item, depth=0):
        if tick: tick()
        count[0] += 1
        if count[0] > MAX_NODES or depth > MAX_DEPTH: _limit('UI data exceeds node/depth limit')
        kind = type(item)
        if kind is str:
            try: item.encode('utf-8')
            except UnicodeError: _error('UI requires valid Unicode')
            chars[0] += len(item)
            if chars[0] > MAX_DEFINITION: _limit('UI text exceeds definition limit')
            return item
        if kind is int:
            if abs(item) > SAFE_INT: _error('UI integers must be exactly representable in JavaScript')
            return item
        if kind is float:
            if not math.isfinite(item): _error('UI numbers must be finite')
            if item.is_integer():
                if abs(item) > SAFE_INT: _error('UI integer-valued numbers exceed exact range')
                return int(item)
            return item
        if item is None or kind is bool: return item
        if kind not in (list, dict): _error('UI requires exact pure JSON values')
        if id(item) in active: _error('UI data cannot contain cycles')
        if len(item) > MAX_NODES: _limit('UI container exceeds limit')
        active.add(id(item))
        try:
            if kind is list: return [visit(child, depth+1) for child in item]
            if any(type(key) is not str or key in ('__proto__', 'constructor', 'prototype') for key in item):
                _error('UI Record keys must be safe Strings')
            return {visit(key, depth+1): visit(child, depth+1) for key, child in item.items()}
        finally: active.remove(id(item))
    result = visit(value)
    if len(_dump(result)) > MAX_DEFINITION: _limit('UI definition exceeds character limit')
    return result


def _typed(value, schema, path):
    if schema.get('x-ui-node'):
        result = _node(value, path)
        if schema.get('x-ui-tag') and result['typeName'] != schema['x-ui-tag']:
            _error(path+' requires '+schema['x-ui-tag'])
        return result
    if 'anyOf' in schema:
        valid = {'string': type(value) is str, 'number': type(value) in (int, float),
                 'boolean': type(value) is bool, 'null': value is None}
        if not any(valid.get(part.get('type'), False) for part in schema['anyOf']): _error(path+' has wrong scalar type')
        return value
    typ = schema.get('type')
    valid = {'string': type(value) is str, 'number': type(value) in (int, float),
             'integer': type(value) is int, 'boolean': type(value) is bool,
             'array': type(value) is list, 'object': type(value) is dict, 'null': value is None}
    if typ and not valid.get(typ, False): _error(path+' requires '+typ)
    if 'enum' in schema and value not in schema['enum']: _error(path+' is outside enum')
    if typ in ('number', 'integer'):
        if 'minimum' in schema and value < schema['minimum'] or 'maximum' in schema and value > schema['maximum']:
            _error(path+' is outside numeric bounds')
    if typ == 'string':
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', MAX_SOURCE):
            _error(path+' is outside text bounds')
    if typ == 'array':
        if len(value) > schema.get('maxItems', MAX_NODES): _limit(path+' exceeds item limit')
        return [_typed(item, schema.get('items', {}), path+'/'+str(index)) for index, item in enumerate(value)]
    if typ == 'object' and 'properties' in schema:
        properties=schema['properties']
        if schema.get('additionalProperties')is False and set(value)-set(properties):_error(path+' has unknown fields')
        if set(schema.get('required',[]))-set(value):_error(path+' missing required fields')
        return {key:_typed(value[key] if key in value else copy.deepcopy(part['default']),part,path+'/'+key) for key,part in properties.items() if key in value or 'default'in part}
    return value


def _node(value, path='root'):
    if type(value) is not dict or set(value) != {'type', 'typeName', 'props', 'partial'} or value['type'] != 'element' or value['partial'] is not False:
        _error(path+' requires a complete canonical component')
    name, props = value['typeName'], value['props']
    if type(name) is not str or name not in _COMPONENTS or type(props) is not dict: _error(path+' has unknown component')
    spec = _COMPONENTS[name]
    if set(props) - set(spec['properties']): _error(path+' has unknown properties')
    out = {}
    for key, schema in spec['properties'].items():
        if key in props: item = props[key]
        elif 'default' in schema: item = copy.deepcopy(schema['default'])
        else: _error(path+' missing '+key)
        out[key] = _typed(item, schema, path+'/props/'+key)
    if name == 'NetworkGraph':
        from .ui_network import validate_graph
        validate_graph(out)
    if name == 'Table' and any(len(row) != len(out['columns']) for row in out['rows']): _error('Table row width must match columns')
    if name == 'BarChart' and len(out['labels']) != len(out['values']): _error('BarChart labels and values must match')
    return {'type': 'element', 'typeName': name, 'props': out, 'partial': False}


def node(tag, args=None, *, tick=None):
    args = [] if args is None else data(args, tick=tick)
    if type(tag) is not str or tag not in _COMPONENTS or type(args) is not list: _error('ui_node requires registered tag and List arguments')
    keys = list(_COMPONENTS[tag]['properties'])
    if len(args) > len(keys): _error('ui_node has excess arguments')
    return _node({'type': 'element', 'typeName': tag, 'props': dict(zip(keys, args)), 'partial': False})


def _source_value(value, schema=None):
    schema = {} if schema is None else schema
    if schema.get('x-ui-node'):
        return value['typeName']+'('+', '.join(_source_value(value['props'][key], prop) for key, prop in _COMPONENTS[value['typeName']]['properties'].items())+')'
    if type(value) is list: return '['+', '.join(_source_value(item, schema.get('items', {})) for item in value)+']'
    if type(value) is dict: return '{'+', '.join(_dump(key)+': '+_source_value(item) for key, item in value.items())+'}'
    return _dump(value)


def _walk(root):
    pending = [root]
    while pending:
        value = pending.pop()
        yield value
        for key, schema in reversed(list(_COMPONENTS[value['typeName']]['properties'].items())):
            if schema.get('x-ui-node'): pending.append(value['props'][key])
            elif schema.get('items', {}).get('x-ui-node'): pending.extend(reversed(value['props'][key]))


def _actions(root):
    result = {}
    for component in _walk(root):
        if component['typeName'] != 'ActionButton': continue
        props = component['props']
        if props['name'] in result: _error('UI action names must be unique')
        result[props['name']] = props['payload']
        if len(result) > MAX_ACTIONS: _limit('UI actions exceed 128')
    return result


def artifact_definition(kind, title, root, *, tick=None, library_hash=None):
    if type(kind) is not str or kind not in KINDS or type(title) is not str or len(title) > 1000: _error('UI kind/title invalid')
    data(title)
    root = _node(data(root, tick=tick))
    if kind == 'slides' and root['typeName'] != 'Slides': _error('Slides artifact requires Slides root')
    _actions(root)
    library_hash = LIBRARY_HASH if library_hash is None else library_hash
    if type(library_hash)is not str:_error('Unsupported UI component library')
    if library_hash != LIBRARY_HASH:
        if library_hash not in LEGACY_LIBRARIES or any(n['typeName']not in LEGACY_LIBRARIES[library_hash]for n in _walk(root)):_error('Unsupported UI component library')
    source = 'root = '+_source_value(root, {'x-ui-node': True})+'\n'
    if len(source) > MAX_SOURCE: _limit('OpenUI source exceeds 100000 characters')
    result = {'schema': 'chaos.ui/1', 'kind': kind, 'title': title, 'source': source, 'root': root, 'libraryHash': library_hash}
    if len(_dump(result)) > MAX_DEFINITION: _limit('UI definition exceeds 200000 characters')
    result['hash'] = hashlib.sha256(_dump(result, sorted_keys=True).encode()).hexdigest()
    if len(_dump(result)) > MAX_DEFINITION: _limit('UI definition exceeds 200000 characters')
    return result


class _Parser:
    """Strict static OpenUI declaration grammar, including forward references."""
    def __init__(self, source):
        if type(source) is not str: _error('OpenUI source requires String')
        if len(source) > MAX_SOURCE: _limit('OpenUI source exceeds 100000 characters')
        self.tokens, self.position, self.count = [], 0, 0
        i = 0
        while i < len(source):
            char = source[i]
            if char in ' \t\r': i += 1; continue
            if char == '\n': self.tokens.append(('sep', char)); i += 1; continue
            if source.startswith('//', i) or char == '#':
                end = source.find('\n', i); i = len(source) if end < 0 else end; continue
            if char in '()[]{},:=': self.tokens.append((char, char)); i += 1; continue
            if char == '"':
                try: value, end = json.JSONDecoder().raw_decode(source[i:])
                except ValueError: _error('Incomplete or invalid OpenUI String')
                self.tokens.append(('value', value)); i += end; continue
            number = re.match(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', source[i:])
            if number:
                raw = number.group()
                if len(raw) > 1000: _error('OpenUI numeric literal exceeds bounds')
                try: parsed = float(raw) if any(c in raw for c in '.eE') else int(raw)
                except ValueError: _error('Invalid OpenUI numeric literal')
                self.tokens.append(('value', parsed)); i += len(raw); continue
            name = re.match(r'[A-Za-z_][A-Za-z_0-9]*', source[i:])
            if name:
                raw = name.group(); self.tokens.append(('value', {'true': True, 'false': False, 'null': None}[raw]) if raw in ('true', 'false', 'null') else ('name', raw)); i += len(raw); continue
            _error('Unsupported OpenUI token at '+str(i))
        self.tokens.append(('eof', None))
        if len(self.tokens) > MAX_NODES * 8: _limit('OpenUI token limit exceeded')

    def peek(self): return self.tokens[self.position][0]
    def take(self, kind=None):
        token = self.tokens[self.position]
        if kind is not None and token[0] != kind: _error('Expected OpenUI '+kind)
        self.position += 1
        return token[1]
    def skip(self):
        while self.peek() == 'sep': self.take()
    def expression(self, depth=0):
        self.count += 1
        if depth > MAX_DEPTH or self.count > MAX_NODES: _limit('OpenUI expression limit exceeded')
        self.skip()
        kind = self.peek()
        if kind == 'value': return ('value', self.take())
        if kind == 'name':
            name = self.take()
            if self.peek() != '(': return ('ref', name)
            if name not in _COMPONENTS: _error('Unsupported OpenUI component '+name)
            self.take('('); args = self.items(')', depth)
            return ('call', name, args)
        if kind == '[':
            self.take(); return ('list', self.items(']', depth))
        if kind == '{':
            self.take(); pairs = []; self.skip()
            while self.peek() != '}':
                key = self.take('name') if self.peek() == 'name' else self.take('value')
                if type(key) is not str or key in [item[0] for item in pairs]: _error('Invalid or duplicate OpenUI object key')
                self.take(':'); pairs.append((key, self.expression(depth+1))); self.skip()
                if self.peek() == '}': break
                self.take(','); self.skip()
            self.take('}'); return ('record', pairs)
        _error('Expected complete OpenUI value')
    def items(self, close, depth):
        result = []; self.skip()
        while self.peek() != close:
            result.append(self.expression(depth+1)); self.skip()
            if self.peek() == close: break
            self.take(','); self.skip()
        self.take(close); return result
    def parse(self):
        declarations = {}; self.skip()
        while self.peek() != 'eof':
            name = self.take('name')
            if name in declarations: _error('Duplicate OpenUI declaration')
            self.take('='); declarations[name] = self.expression()
            if self.peek() not in ('sep', 'eof'): _error('OpenUI declarations require newline separation')
            self.skip()
        if 'root' not in declarations: _error('OpenUI requires root declaration')
        memo, active = {}, set()
        def resolve(value, depth=0):
            if depth > MAX_DEPTH: _limit('OpenUI reference depth exceeded')
            if value[0] == 'value': return data(value[1])
            if value[0] == 'ref':
                name = value[1]
                if name not in declarations or name in active: _error('Unresolved or cyclic OpenUI reference '+name)
                if name not in memo:
                    active.add(name)
                    try: memo[name] = resolve(declarations[name], depth+1)
                    finally: active.remove(name)
                return memo[name]
            if value[0] == 'call': return node(value[1], [resolve(item, depth+1) for item in value[2]])
            if value[0] == 'list': return [resolve(item, depth+1) for item in value[1]]
            return {key: resolve(item, depth+1) for key, item in value[1]}
        # Validate every declaration, including unreachable ones; never hide bad source.
        for name in declarations: resolve(('ref', name))
        return resolve(('ref', 'root'))


def compile_ui(source, kind='page', title=''):
    try: return artifact_definition(kind, title, _Parser(source).parse())
    except RecursionError: _limit('OpenUI nesting limit exceeded')


def _definition(value):
    value = data(value)
    if type(value) is not dict or set(value) != {'schema', 'kind', 'title', 'source', 'root', 'libraryHash', 'hash'} or value['schema'] != 'chaos.ui/1': _error('Invalid UI definition')
    expected = artifact_definition(value['kind'], value['title'], value['root'],library_hash=value['libraryHash'])
    if value != expected: _error('UI definition/source/hash/library mismatch')
    return expected


@dataclass(frozen=True, eq=False)
class UIArtifact:
    owner: object
    definition_json: str
    capabilities: frozenset = field(default_factory=frozenset, init=False)
    def __post_init__(self):
        if type(self.definition_json) is not str or len(self.definition_json) > MAX_DEFINITION: _error('Invalid native UI snapshot')
        try: value = json.loads(self.definition_json)
        except (ValueError, RecursionError): _error('Invalid native UI snapshot')
        object.__setattr__(self, 'definition_json', _dump(_definition(value)))
    @property
    def definition(self): return json.loads(self.definition_json)


def owned(value, evaluator):
    if type(value) is not UIArtifact: _error('Expected UIArtifact')
    if value.owner is not evaluator: _error('UIArtifact cannot cross evaluators', 'ownership_error')
    return value


def describe(value): return {'$ui': value.definition}


def get_member(value, name, evaluator):
    definition = owned(value, evaluator).definition
    if type(name) is not str or name not in definition: _error('UIArtifact has no such member', 'attribute_error')
    return definition[name]


def info(value):
    definition = value.definition
    return {key: definition[key] for key in ('kind', 'title', 'hash', 'libraryHash')} | {'nodeCount': sum(1 for _ in _walk(definition['root'])), 'actionNames': list(_actions(definition['root']))}


def program_ui(definition):
    definition = _definition(definition)
    return 'ui_from_openui('+_dump(definition['source'])+', kind='+_dump(definition['kind'])+', title='+_dump(definition['title'])+')\n'


def validate_ui(definition, requirements=None):
    definition = _definition(definition); requirements = data([] if requirements is None else requirements)
    if type(requirements) is not list or len(requirements) > MAX_REQUIREMENTS: _error('UI requirements require up to 128 typed Records')
    checks = []
    for index, requirement in enumerate(requirements):
        if type(requirement) is not dict: _error('UI requirement must be Record')
        if set(requirement) == {'component', 'minimum'}:
            tag, minimum = requirement['component'], requirement['minimum']
            if type(tag) is not str or tag not in _COMPONENTS or type(minimum) is not int or minimum < 0: _error('Invalid component requirement')
            actual = sum(item['typeName'] == tag for item in _walk(definition['root']))
            passed = actual >= minimum
        elif set(requirement) == {'path', 'equals'}:
            path = requirement['path']
            if type(path) is not str or not path.startswith('/') or len(path) > 2000: _error('Requirement path requires JSON pointer')
            actual, found = definition['root'], True
            for token in path[1:].split('/'):
                if re.search(r'~(?![01])', token): _error('Invalid JSON pointer escape')
                token = token.replace('~1', '/').replace('~0', '~')
                if type(actual) is dict and token in actual: actual = actual[token]
                elif type(actual) is list and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(actual): actual = actual[int(token)]
                else: found = False; actual = None; break
            passed = found and _dump(actual, sorted_keys=True) == _dump(requirement['equals'], sorted_keys=True)
        else: _error('Unsupported typed UI requirement')
        checks.append({'index': index, 'passed': passed, 'actual': actual})
    return {'passed': all(check['passed'] for check in checks), 'checks': checks, 'artifactHash': definition['hash']}


def prompt(kind='page'):
    if type(kind) is not str or kind not in KINDS: _error('Invalid UI kind')
    signatures = []
    for name, spec in _COMPONENTS.items():
        params = [key+('='+_dump(schema['default']) if 'default' in schema else '') for key, schema in spec['properties'].items()]
        signatures.append(name+'('+', '.join(params)+')')
    return ('Return only a complete static OpenUI document with a root = Component(...) declaration. '
            'Use newline-separated named declarations and registered components, JSON literals, lists, records and references. '
            'No markdown fences, scripts, Query, Mutation, Action, reactive state, builtins or operators. '
            'NetworkGraph is pure supplied graph data. RuntimeGraph binds only to the trusted host project/run and reads actual observations; no URLs or IDs are accepted in its props. Offline RuntimeGraph has no live connection. Provide a full replacement document. Kind: '+kind+'. '+('Root must be Slides. ' if kind == 'slides' else '')+
            'Components and positional argument order:\n'+'\n'.join(signatures)+'\nSchema:\n'+_dump(_CATALOG))


_EXPORT_STYLE = '''body{font:16px/1.55 system-ui;margin:0;padding:2rem;background:#12151c;color:#edf1f8}main{max-width:1100px;margin:auto}button,summary{cursor:pointer}button{font:inherit;background:#232b39;color:#edf1f8;border:1px solid #7f94b2;border-radius:.4rem;padding:.4rem .8rem}button:focus-visible,summary:focus-visible{outline:2px solid #99c3ff;outline-offset:3px}.ui-stack{display:flex;flex-direction:column;gap:1rem}.ui-row{flex-direction:row;flex-wrap:wrap}.ui-small{gap:.5rem}.ui-large{gap:1.5rem}.ui-card,.ui-slide,details{padding:1rem;background:#191e29;border:1px solid #7f94b2;border-radius:.6rem}.ui-metric strong{display:block;font-size:2rem;color:#ffcb7a}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;text-align:left;border-bottom:1px solid #7f94b2}th button{width:100%;text-align:left}.ui-table{overflow:auto}.ui-bar{height:1.3rem;background:#99c3ff;min-width:1px}.ui-chart-row{display:grid;grid-template-columns:minmax(7rem,1fr) 3fr 5rem;gap:.5rem;margin:.5rem 0}pre{white-space:pre-wrap;overflow:auto;background:#232b39;padding:1rem}nav{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}[hidden]{display:none!important}small{color:#bdc6d4}@media(max-width:600px){body{padding:1rem}.ui-chart-row{grid-template-columns:1fr 2fr 4rem}}@media print{body{color:#182235;background:white;padding:0}.ui-card,.ui-slide,details,pre{background:white;color:#182235;border-color:#667085}.ui-metric strong{color:#87501a}button,nav,.ui-actions{display:none!important}.ui-slide,[data-tab-panel]{display:block!important;break-after:page}[hidden][data-tab-panel],[hidden].ui-slide{display:block!important}small{color:#526078}}'''

_EXPORT_SCRIPT = '''(()=>{const e=document.querySelector('main');e.addEventListener('click',n=>{const b=n.target.closest('button');if(!b)return;if(b.hasAttribute('data-tab')){const t=b.closest('[data-ui-tabs]');t.querySelectorAll(':scope > [data-tab-panel]').forEach((p,i)=>p.hidden=i!==Number(b.dataset.tab));t.querySelectorAll(':scope > nav button').forEach(x=>x.setAttribute('aria-selected',String(x===b)))}if(b.hasAttribute('data-sort')){const t=b.closest('table'),c=Number(b.dataset.sort),d=b.dataset.dir==='up'?-1:1;b.dataset.dir=d===1?'up':'down';[...t.tBodies[0].rows].sort((a,z)=>{const x=a.cells[c].textContent,y=z.cells[c].textContent;return d*(x.localeCompare(y,undefined,{numeric:true}))}).forEach(r=>t.tBodies[0].append(r))}if(b.hasAttribute('data-action')){const p=JSON.parse(b.dataset.payload);e.dispatchEvent(new CustomEvent('ui.action',{bubbles:true,detail:{name:b.dataset.action,payload:p}}));e.querySelector('[data-action-status]').textContent='Portable action emitted: '+b.dataset.action}});document.querySelectorAll('[data-ui-slides]').forEach(s=>{let i=0,t=null;const p=[...s.querySelectorAll(':scope > .ui-slide')],status=s.querySelector('[data-slide-status]'),play=s.querySelector('[data-slide-play]');const draw=()=>{p.forEach((v,k)=>v.hidden=k!==i);status.textContent=p.length?(i+1)+' / '+p.length:'0 / 0'};const stop=()=>{clearInterval(t);t=null;play.textContent='Play';play.setAttribute('aria-pressed','false')};s.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-slide-step')){if(p.length)i=(i+Number(b.dataset.slideStep)+p.length)%p.length;draw()}if(b===play){if(t)stop();else if(p.length&&!matchMedia('(prefers-reduced-motion: reduce)').matches){t=setInterval(()=>{i=(i+1)%p.length;draw()},Number(s.dataset.interval));play.textContent='Pause';play.setAttribute('aria-pressed','true')}}});s.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){if(p.length)i=(i+(e.key==='ArrowRight'?1:-1)+p.length)%p.length;draw();e.preventDefault()}if(e.key==='Escape')stop()});document.addEventListener('visibilitychange',()=>{if(document.hidden)stop()});window.addEventListener('pagehide',stop);draw()})})();'''


def _html_node(value):
    tag, p = value['typeName'], value['props']
    esc = lambda item: html.escape(str(item), quote=True)
    children = lambda values: ''.join(_html_node(item) for item in values)
    if tag == 'Stack': return '<div class="ui-stack ui-'+p['direction']+' ui-'+p['gap']+'">'+children(p['children'])+'</div>'
    if tag == 'NetworkGraph':
        from .ui_network import network_html
        return network_html(p)
    if tag == 'RuntimeGraph': return '<section class="ui-card"><h2>'+esc(p['title'])+'</h2><p>离线导出未连接运行宿主；没有实时状态数据。请在原项目中打开此组件。</p></section>'
    if tag == 'Heading': return '<h'+str(p['level'])+'>'+esc(p['text'])+'</h'+str(p['level'])+'>'
    if tag == 'Text': return '<p>'+esc(p['text'])+'</p>'
    if tag in ('Card', 'Tab', 'Slide'): return '<section class="ui-'+tag.lower()+'"><h2>'+esc(p['title'])+'</h2>'+children(p['children'])+'</section>'
    if tag == 'Metric': return '<div class="ui-metric"><span>'+esc(p['label'])+'</span><strong>'+esc(p['value'])+'</strong><small>'+esc(p['detail'])+'</small></div>'
    if tag == 'Table':
        header = ''.join('<th scope="col"><button data-sort="'+str(i)+'">'+esc(label)+'</button></th>' for i, label in enumerate(p['columns']))
        rows = ''.join('<tr>'+''.join('<td>'+esc(_dump(cell) if cell is None or type(cell) is bool else cell)+'</td>' for cell in row)+'</tr>' for row in p['rows'])
        return '<div class="ui-table"><table><thead><tr>'+header+'</tr></thead><tbody>'+rows+'</tbody></table></div>'
    if tag == 'BarChart':
        scale = max([abs(number) for number in p['values']] or [1]) or 1
        rows = ''.join('<div class="ui-chart-row"><span>'+esc(label)+'</span><div class="ui-bar" style="width:'+str(abs(number)/scale*100)+'%"></div><span>'+esc(number)+'</span></div>' for label, number in zip(p['labels'], p['values']))
        return '<figure><figcaption>'+esc(p['title'])+'</figcaption>'+rows+'</figure>'
    if tag == 'Tabs':
        buttons = ''.join('<button role="tab" aria-selected="'+('true' if i == 0 else 'false')+'" data-tab="'+str(i)+'">'+esc(item['props']['title'])+'</button>' for i, item in enumerate(p['items']))
        panels = ''.join('<section data-tab-panel role="tabpanel"'+(' hidden' if i else '')+'>'+children(item['props']['children'])+'</section>' for i, item in enumerate(p['items']))
        return '<section data-ui-tabs><nav role="tablist">'+buttons+'</nav>'+panels+'</section>'
    if tag == 'Disclosure': return '<details><summary>'+esc(p['title'])+'</summary>'+children(p['children'])+'</details>'
    if tag == 'Code':
        from .code_presentation import render_code
        return render_code(p['source'], p['language'])
    if tag == 'ActionButton': return '<button data-action="'+esc(p['name'])+'" data-payload="'+esc(_dump(p['payload']))+'">'+esc(p['label'])+'</button>'
    if tag == 'Slides': return '<section data-ui-slides tabindex="0" data-interval="'+str(p['interval'])+'"><nav><button data-slide-step="-1">Previous</button><button data-slide-play aria-pressed="false">Play</button><button data-slide-step="1">Next</button><span data-slide-status></span></nav>'+children(p['children'])+'</section>'
    _error('Unknown HTML component')


def export_ui(definition, format='html'):
    definition = _definition(definition)
    if type(format) is not str or format not in ('html', 'json', 'openui', 'chaos'): _error('UI export format requires html/json/openui/chaos')
    if format == 'json': result = _dump(definition)
    elif format == 'openui': result = definition['source']
    elif format == 'chaos': result = program_ui(definition)
    else:
        script_hash = base64.b64encode(hashlib.sha256(_EXPORT_SCRIPT.encode()).digest()).decode()
        policy = "default-src 'none'; script-src 'sha256-"+script_hash+"'; style-src 'unsafe-inline'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
        result = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+html.escape(policy, quote=True)+'"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(definition['title'])+'</title><style>'+_EXPORT_STYLE+'</style></head><body><main>'+_html_node(definition['root'])+'<p data-action-status role="status"></p><small>Portable offline document. Actions emit ui.action events; server handlers require the original program.</small></main><script>'+_EXPORT_SCRIPT+'</script></body></html>'
    if len(result) > MAX_DEFINITION: _limit('UI export exceeds 200000 characters')
    return result


def _task(evaluator, iterator):
    scheduler = evaluator.suspension
    if not scheduler.active: _error('UI Task requires active evaluator')
    count = sum(bool(getattr(task, 'ui_operation', False)) for task in scheduler.tasks)
    if count >= MAX_TASKS: _limit('UI operations exceed 64 Tasks per epoch')
    task = scheduler.task(iterator)
    task.ui_operation = True
    task.context_structured = True
    return task


def generate(evaluator, agent, request, *, kind='page', title='', previous=None):
    from .language import Agent
    from .agent_tools import current_caps
    from .agent_values import validate_definition
    if type(agent) is not Agent or agent.owner not in (None, evaluator): _error('ui_generate requires owned exact Agent')
    definition = validate_definition({'kind': 'agent', 'name': agent.name, 'config': agent.config})
    capabilities = current_caps(evaluator)
    if capabilities is not None and not set(definition['config'].get('tools', [])).issubset(capabilities): _error('UI Agent exceeds capabilities', 'capability_error')
    if kind not in KINDS or type(kind) is not str or type(title) is not str or len(title) > 1000: _error('Invalid UI kind/title')
    data(title)
    payload = {'operation': 'generate', 'agent': {'name': definition['name'], 'config': definition['config']}, 'request': data(request, tick=evaluator.tick), 'kind': kind, 'title': title, 'previous': None if previous is None else owned(previous, evaluator).definition['source'], 'libraryHash': LIBRARY_HASH}
    def operation():
        from .suspension import Effect, CHECKPOINT
        yield CHECKPOINT
        current = evaluator.suspension.current.task
        if current.recording_scope is not None: _error('UI generation cannot run in Skill recording', 'mode_error')
        if evaluator.effect_handler is None or evaluator.agent_handler is None: _error('Current environment has no UI model driver', 'capability_error')
        result = yield Effect('ui', payload)
        if type(result) is not dict or set(result) != {'source'} or type(result['source']) is not str: _error('Invalid UI host response')
        return UIArtifact(evaluator, _dump(compile_ui(result['source'], kind, title)))
    return _task(evaluator, operation())


def dispatch(evaluator, value, event, handlers):
    from .agent_tools import AgentTool, owned as tool_owned, invoke_tool, current_caps
    definition = owned(value, evaluator).definition
    event = data(event, tick=evaluator.tick)
    if type(event) is not dict or set(event) != {'name', 'payload'} or type(event['name']) is not str: _error('UI event requires exact name/payload')
    actions = _actions(definition['root'])
    if event['name'] not in actions or _dump(event['payload'], sorted_keys=True) != _dump(actions[event['name']], sorted_keys=True): _error('UI event is not declared by artifact')
    if type(handlers) is not dict or len(handlers) > MAX_ACTIONS or any(type(key) is not str for key in handlers): _error('UI handlers require bounded Record')
    for name, handler in handlers.items():
        if name not in actions or type(handler) is not AgentTool: _error('UI handlers require declared owned AgentTool')
        tool_owned(handler, evaluator)
        caps = current_caps(evaluator)
        if caps is not None and not handler.capabilities.issubset(caps): _error('UI handler exceeds capabilities', 'capability_error')
    if event['name'] not in handlers: _error('UI event has no handler')
    tool = handlers[event['name']]
    def operation():
        return (yield from invoke_tool(evaluator, tool, event['payload']))
    return _task(evaluator, operation())


def builtins(evaluator):
    def artifact(kind, title, root): return UIArtifact(evaluator, _dump(artifact_definition(kind, title, root, tick=evaluator.tick)))
    def parse(source, kind='page', title=''): return UIArtifact(evaluator, _dump(compile_ui(source, kind, title)))
    return {'ui_node': lambda tag, args=[]: node(tag, args, tick=evaluator.tick), 'ui_artifact': artifact, 'ui_from_openui': parse,
            'ui_info': lambda value: info(owned(value, evaluator)), 'ui_definition': lambda value: owned(value, evaluator).definition,
            'ui_source': lambda value: owned(value, evaluator).definition['source'], 'ui_program': lambda value: program_ui(owned(value, evaluator).definition),
            'ui_export': lambda value, format='html': export_ui(owned(value, evaluator).definition, format),
            'ui_validate': lambda value, requirements=[]: validate_ui(owned(value, evaluator).definition, requirements), 'ui_prompt': prompt,
            'ui_generate': lambda agent, request, *, kind='page', title='', previous=None: generate(evaluator, agent, request, kind=kind, title=title, previous=previous),
            'ui_dispatch': lambda value, event, handlers: dispatch(evaluator, value, event, handlers)}
