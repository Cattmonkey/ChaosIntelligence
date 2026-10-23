"""Safe, lossless code presentation using the real language analysis service.

This module does not parse another approximation of Chaos. Token ranges have
exactly the same UTF-16 meaning as the IDE API; rendering never evaluates code.
"""
from functools import lru_cache
from html import escape
import io
import keyword
import tokenize as python_tokens

from .ide_language import SourceMap, analyze
from .language import LanguageError

TOKEN_TYPES = frozenset(('keyword', 'string', 'number', 'comment', 'operator',
                         'punctuation', 'variable', 'function', 'parameter', 'type', 'property'))


def code_language(info):
    """Fence metadata is an explicit language label, never inferred from words."""
    parts = str(info or '').strip().split()
    name = parts[0].lower() if parts else 'text'
    return 'python' if name == 'py' else name


@lru_cache(maxsize=256)
def _analysis(source, language):
    if len(source) > 100000:
        return ()
    if language == 'chaos':
        result = analyze(source)
        details = {(item['from'], item['to']): item['detail']
                   for item in result['symbols'] + result.get('referenceHints', [])}
        return tuple((item['from'], item['to'], item['type'], details.get((item['from'], item['to']), ''))
                     for item in result['tokens'])
    if language == 'python':
        source_map = SourceMap(source)
        result = []
        # Python's standard tokenizer also yields valid prefixes of unfinished
        # snippets. No Python source is imported, compiled, or executed here.
        try:
            for item in python_tokens.generate_tokens(io.StringIO(source).readline):
                kind = {python_tokens.STRING: 'string', python_tokens.NUMBER: 'number',
                        python_tokens.COMMENT: 'comment', python_tokens.OP: 'operator'}.get(item.type)
                if item.type == python_tokens.NAME:
                    kind = 'keyword' if keyword.iskeyword(item.string) else 'variable'
                if kind:
                    span = source_map.public(source_map.offset(item.start[0], item.start[1] + 1), source_map.offset(item.end[0], item.end[1] + 1))
                    result.append((span['from'], span['to'], kind, ''))
        except (python_tokens.TokenError, IndentationError, SyntaxError):
            pass
        return tuple(result)
    return ()


def presentation_tokens(source, language='chaos'):
    """Immutable cached analysis is copied so callers cannot poison later pages."""
    if len(source) > 100000:
        return []
    try:
        values = _analysis(source, code_language(language))
    except (LanguageError, RecursionError, ValueError):
        values = ()  # Readable original source is always preferable to a broken page.
    return [dict(zip(('from', 'to', 'type', 'detail'), item)) for item in values]


def _render_lines(source, language):
    """Render inert token spans without a second grammar or runtime evaluation."""
    source_map = SourceMap(source)
    offsets = {unit: index for index, unit in enumerate(source_map.units)}
    spans, previous = [], 0
    for item in presentation_tokens(source, language):
        start, end = offsets.get(item['from']), offsets.get(item['to'])
        if start is None or end is None or start < previous or end <= start or item['type'] not in TOKEN_TYPES:
            continue
        spans.append((start, end, item['type'], item['detail']))
        previous = end
    result, offset, token_index = [], 0, 0
    lines = source.split('\n')
    for index, line in enumerate(lines):
        end = offset + len(line)
        position, body, current = offset, [], token_index
        while current < len(spans) and spans[current][0] < end:
            start, finish, kind, detail = spans[current]
            if finish <= offset:
                current += 1
                continue
            left, right = max(start, offset), min(finish, end)
            body.append(escape(source[position:left]))
            title = ' title="' + escape(detail, quote=True) + '"' if detail else ''
            body.append('<span class="chaos-token-' + kind + '"' + title + '>' + escape(source[left:right]) + '</span>')
            position = right
            if finish > end:
                break
            current += 1
        token_index = current
        body.append(escape(source[position:end]))
        if index < len(lines) - 1:
            body.append('\n')
        result.append('<span class="chaos-code-line" data-line="' + str(index + 1) + '">' + ''.join(body) + '</span>')
        offset = end + 1
    return ''.join(result)


@lru_cache(maxsize=256)
def _readable_template(source):
    """Cache immutable display markup by source, bounded like token analysis.

    Documentation pages share snippets and are served repeatedly. Formatting
    is independent of project state, so edits naturally select a new cache key.
    """
    from .source_format import format_source
    try:
        formatted = format_source(source)
        if formatted != source:
            return '<template data-code-formatted>' + _render_lines(formatted, 'chaos') + '</template>'
    except (LanguageError, RecursionError, ValueError):
        pass
    return ''


def render_code(source, info='', *, readable=False):
    """Render original source, optionally bundling an offline readable view.

    The original remains the displayed/copyable code. A template stores only
    inert spans for an explicit reading toggle, never executable HTML or code.
    Invalid snippets remain valid documentation with no formatted alternative.
    """
    language = code_language(info)
    original = _render_lines(source, language)
    mode = ' data-format-mode="embedded"' if readable and language == 'chaos' else ''
    alternate = _readable_template(source) if mode and len(source) <= 100000 else ''
    label = escape(language, quote=True)
    return ('<pre class="chaos-code" data-language="' + label + '" data-highlight="' + ('ready' if 'class="chaos-token-' in original else 'plain')
            + '"' + mode + ' tabindex="0" aria-label="' + label + ' 代码"><code>' + original + '</code>' + alternate + '</pre>\n')
