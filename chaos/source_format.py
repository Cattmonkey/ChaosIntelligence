"""Source-preserving layout shared by the IDE, API, SDK and command line.

The execution parser supplies statement/decorator and legacy-access boundaries;
layout changes whitespace only (plus explicit dots for version-1 access). Every
result is reparsed and compared before it can replace the caller's source.
"""
import bisect
from dataclasses import dataclass

from .language import LanguageError, Parser, ResourceLimit, tokenize
from .program import plain


@dataclass
class _Group:
    end: int = 0
    width: int = 0
    hard: bool = False


def format_source(source, file='main.chaos', *, language_version=2,
                  line_width=88, indent_size=2):
    """Return readable source without executing code or rewriting literal text.

    ``line_width`` is a target: indivisible strings, comments and identifiers
    may exceed it. Invalid or incomplete drafts never produce a replacement.
    Version-1 attribute-style ``x[key]`` is made explicit as ``x.[key]`` so the
    result also has the same meaning under the current grammar.
    """
    if not isinstance(source, str):
        raise LanguageError('source 必须是字符串')
    if not isinstance(file, str):
        raise LanguageError('file 必须是字符串')
    if type(language_version) is not int or language_version not in (1, 2):
        raise LanguageError('languageVersion 必须是 1 或 2')
    if type(line_width) is not int or not 40 <= line_width <= 240:
        raise LanguageError('lineWidth 必须是 40 到 240 的整数')
    if type(indent_size) is not int or not 1 <= indent_size <= 8:
        raise LanguageError('indentSize 必须是 1 到 8 的整数')
    try:
        return _format(source, file, language_version, line_width, indent_size)
    except RecursionError:
        raise ResourceLimit('源码语法嵌套过深') from None


def _format_generated_source(source, file='main.chaos', *, max_bytes=None):
    """Lay out generated text without shrinking the accepted source capacity.

    The original still passes the ordinary parser and all resource limits.
    Only whitespace expansion beyond the source-character limit keeps its
    original layout; invalid input or other resource failures still propagate.
    """
    try:
        return _format(source, file, 2, 88, 2, preserve_capacity=True, max_bytes=max_bytes)
    except RecursionError:
        raise ResourceLimit('源码语法嵌套过深') from None


def _format(source, file, language_version, line_width, indent_size, *,
            preserve_capacity=False, max_bytes=None):
    original = Parser(source, file, ranges=True,
                      legacy_items=language_version == 1).program()
    tokens = [t for t in tokenize(source, trivia=True)
              if t.kind not in ('space', 'eof')]
    code = [t for t in tokens if t.kind != 'comment']
    starts = [t.start for t in code]
    line_starts = [0] + [i + 1 for i, c in enumerate(source) if c == '\n']
    decorator_ends, legacy_brackets = set(), set()
    records, statements, prefixes, clauses, spaced_groups = set(), set(), set(), set(), set()
    parameter_separators, tuple_groups = set(), set()
    class_colons, class_braces = set(), set()

    def span(node):
        info = getattr(node, 'span', {})
        def offset(line, column):
            line = max(1, min(line or 1, len(line_starts)))
            return min(len(source), line_starts[line - 1] + max(0, (column or 1) - 1))
        return (offset(info.get('line'), info.get('column')),
                offset(info.get('endLine', info.get('line')),
                       info.get('endColumn', info.get('column'))))

    def boundaries(node):
        if not isinstance(node, (list, tuple)):
            return
        # Only located syntax owns layout boundaries. Record field pairs and
        # signature rows are data tuples, even when a label spells `for`.
        if node and isinstance(node[0], str) and getattr(node, 'span', None):
            kind = node[0]
            if kind in ('assign_pattern', 'with_pattern', 'async_with_pattern', 'let_pattern', 'var_pattern', 'for_pattern', 'async_for_pattern', 'comp_for_pattern', 'comp_async_for_pattern'):
                spaced_groups.add(span(node[1])[0])
            if kind in ('fn', 'gen_fn', 'async_fn', 'async_gen_fn'):
                spaced_groups.update(span(parameter[4])[0] for parameter in node[1] if len(parameter) == 5)
                if any(len(parameter)>=4 and parameter[2]=='positional_only' for parameter in node[1]):
                    start,finish=span(node)[0],span(node[3])[0]
                    header=code[bisect.bisect_left(starts,start):bisect.bisect_left(starts,finish)]
                    depth=0
                    for index,token in enumerate(header):
                        if token.value in ('(','[','{'):depth+=1
                        elif token.value in (')',']','}'):depth-=1
                        elif depth==1 and token.value=='/' and index and header[index-1].value==',':
                            parameter_separators.add(token.start)
            if kind == 'group_case':spaced_groups.add(span(node[1])[0])
            if kind in ('for', 'async_for', 'for_pattern', 'async_for_pattern'):
                spaced_groups.add(span(node[2])[0])
            if kind in ('tuple','p_tuple') and source[span(node)[0]:span(node)[0]+1]=='(':
                tuple_groups.add(span(node)[0])
            if kind == 'block':
                statements.update(span(child)[0] for child in node[1])
            elif kind == 'p_class':
                start, finish = span(node)
                header = code[bisect.bisect_left(starts,start):bisect.bisect_left(starts,finish)]
                for index, token in enumerate(header[:-2]):
                    if token.value == ':' and header[index+1].value == ':' and header[index+2].value == '{':
                        class_colons.update((token.start,header[index+1].start))
                        class_braces.add(header[index+2].start)
                        records.add(header[index+2].start)
                        break
            elif kind in ('record', 'record_comp', 'p_record', 'set', 'set_comp'):
                records.add(span(node)[0])
            elif kind in ('comp_for', 'comp_async_for', 'comp_for_pattern', 'comp_async_for_pattern', 'comp_if'):
                clauses.add(span(node)[0])
                if kind != 'comp_if': spaced_groups.add(span(node[2])[0])
            elif kind == 'async_gen_comp':
                start, finish = span(node)
                opening = next((token for token in code[bisect.bisect_left(starts, start):bisect.bisect_left(starts, finish)] if token.value == '('), None)
                if opening is not None: spaced_groups.add(opening.start)
            elif kind in ('unary', 'spread', 'kwspread'):
                prefixes.add(span(node)[0])
            elif kind == 'decorated':
                decorator_ends.update(span(expr)[1] for expr in node[1])
            if language_version == 1 and kind in ('index', 'set_member'):
                end = span(node[1])[1]
                at = bisect.bisect_left(starts, end)
                while at < len(code) and code[at].value == ')':
                    at += 1
                if at < len(code) and code[at].value == '[':
                    legacy_brackets.add(code[at].start)
        for child in node:
            boundaries(child)
    boundaries(original)

    # Width summaries are computed once, bottom-up. No nested token strings
    # are copied, keeping deeply nested inputs within the parser's own budget.
    groups, pending = {}, []
    for index, token in enumerate(tokens):
        value = token.value
        if token.kind == 'op' and value in ('(', '[', '{'):
            group = _Group(width=1 + (1 if token.start in legacy_brackets else 0),
                           hard=value == '{' and token.start not in records)
            groups[index] = group
            pending.append((index, group))
        elif token.kind == 'op' and value in (')', ']', '}'):
            _, group = pending.pop()
            group.end = index
            group.width += 1
            if index and tokens[index - 1].value in ('(', '[', '{'):
                group.hard = False
            if pending:
                parent = pending[-1][1]
                parent.width += group.width + 1
                parent.hard = parent.hard or group.hard
        elif pending:
            group = pending[-1][1]
            # Conservative whitespace allowance prevents an inline group from
            # overflowing due to operator padding. It does not split tokens.
            group.width += len(value) + (2 if token.kind == 'op' else 1)
            group.hard = group.hard or token.kind == 'comment' or '\n' in value or value == ';'

    lines, stack = [], []
    line, indent = '', 0
    previous = None

    def emit(text, space=False):
        nonlocal line
        if not line:
            line = ' ' * (indent * indent_size)
        elif space and not line.endswith((' ', '\n')):
            line += ' '
        line += text

    def newline(preserve=False):
        nonlocal line
        if line.strip():
            lines.append(line if preserve else line.rstrip())
        line = ''

    def attached_comment(index):
        return (index + 1 < len(tokens) and tokens[index + 1].kind == 'comment'
                and '\n' not in source[tokens[index].end:tokens[index + 1].start])

    for index, token in enumerate(tokens):
        value = token.value
        following = tokens[index + 1].value if index + 1 < len(tokens) else ''
        if previous and token.kind != 'comment' and (token.start in statements or any(
                previous.end <= end <= token.start for end in decorator_ends)):
            newline()
        if token.start in clauses and stack and stack[-1][1]:
            newline()
        if value == '@' and token.kind == 'op':
            newline()
        if token.kind == 'comment':
            if previous and '\n' in source[previous.end:token.start]:
                newline()
            emit(value, space=True)
            newline(preserve=True)
            previous = token
            continue
        if token.kind == 'op' and value in ('(', '[', '{'):
            if value == '{':
                space = token.start not in class_braces and bool(previous and previous.value not in ('(', '[', '.', '*', '**'))
            else:
                # await(x) is a callable identifier; await (x) is suspension.
                space = bool(value == '(' and previous and
                             previous.value in ('await', 'yield', 'from') and
                             previous.end < token.start)
            if token.start in spaced_groups:
                space = True
            if token.start in tuple_groups:
                space = bool(previous and previous.kind=='id')
            if token.start in legacy_brackets:
                emit('.')
            group = groups[index]
            column = len(line.rsplit('\n', 1)[-1]) if line else indent * indent_size
            multiline = group.hard or column + int(space) + group.width > line_width
            if group.end == index + 1:
                multiline = False
            emit(value, space=space)
            stack.append((value, multiline))
            if multiline:
                indent += 1
                if not attached_comment(index):
                    newline()
        elif token.kind == 'op' and value in (')', ']', '}'):
            _, multiline = stack.pop()
            if multiline:
                indent -= 1
                newline()
            emit(value)
            # A statement boundary from the AST handles the following
            # declaration without breaking expression/member continuations.
        elif value == ';' and token.kind == 'op':
            emit(';')
            if not attached_comment(index):
                newline()
        elif value == ',' and token.kind == 'op':
            emit(',')
            if stack and stack[-1][1]:
                if not attached_comment(index):
                    newline()
            elif following not in (')',']','}'):
                emit(' ')
        elif value == '.' and token.kind == 'op':
            emit('.')
        elif value == ':' and token.kind == 'op':
            emit(':' if token.start in class_colons else ': ')
        elif token.kind == 'op':
            prefix = (token.start in prefixes or value in ('*', '**', '..')
                      and previous and previous.value in ('(', '[', '{', ','))
            if value == '@' or token.start in parameter_separators:
                emit(value)
            elif prefix:
                emit(value, space=bool(previous and previous.value not in ('(', '[', '{', ',', '@')))
            elif value == '!' or value == '*' and previous and previous.value in ('fn', 'catch'):
                emit(value)
            else:
                emit(value, space=True)
                emit(' ')
        else:
            space = bool(previous and previous.value not in ('(', '[', '{', '.', '!', '@')
                         and previous.start not in prefixes
                         and not (previous.value in ('*', '**', '..') and
                                  (index < 2 or tokens[index - 2].value in ('(', '[', '{', ','))))
            emit(value, space=space)
        previous = token
    newline()
    result = '\n'.join(lines) + '\n'
    if preserve_capacity and (len(result) > 100000 or
            max_bytes is not None and len(result.encode('utf-8')) > max_bytes):
        result = source
    if plain(Parser(result, file).program()) != plain(original):
        raise LanguageError('格式化未通过语义一致性检查')
    # Checking the entire token sequence covers original string spellings,
    # comments (including trailing whitespace), numeric text and punctuation.
    expected = []
    for token in tokens:
        if token.start in legacy_brackets:
            expected.append(('op', '.'))
        expected.append((token.kind, token.value))
    actual = [(t.kind, t.value) for t in tokenize(result, trivia=True)
              if t.kind not in ('space', 'eof')]
    if actual != expected:
        raise LanguageError('格式化未保留源码标记')
    return result
