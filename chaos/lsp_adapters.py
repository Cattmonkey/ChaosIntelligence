"""Translate the shared, nonexecuting editor analyzer into LSP data types."""
from .lsp_workspace import TextMap, confined


TOKEN_TYPES = ['namespace', 'type', 'class', 'parameter', 'variable', 'property',
               'function', 'method', 'macro', 'keyword', 'comment', 'string',
               'number', 'operator', 'decorator']
TOKEN_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
COMPLETION_KINDS = {'function': 3, 'method': 2, 'variable': 6, 'class': 7,
                    'module': 9, 'property': 10, 'keyword': 14}
SYMBOL_KINDS = {'module': 2, 'class': 5, 'method': 6, 'property': 7,
                'function': 12, 'variable': 13, 'parameter': 13}


def capabilities():
    return {
        'positionEncoding': 'utf-16',
        'textDocumentSync': {'openClose': True, 'change': 2},
        'completionProvider': {'triggerCharacters': ['.', '"', "'", '/']},
        'hoverProvider': True,
        'signatureHelpProvider': {'triggerCharacters': ['(', ','], 'retriggerCharacters': ['=']},
        'semanticTokensProvider': {'legend': {'tokenTypes': TOKEN_TYPES, 'tokenModifiers': []}, 'full': True},
        'documentSymbolProvider': True,
        'definitionProvider': True,
        'referencesProvider': True,
        'renameProvider': {'prepareProvider':True},
        'documentFormattingProvider': True,
    }


def diagnostics(result, mapping):
    return [{'range': mapping.span(item['from'], item['to']),
             'severity': {'error': 1, 'warning': 2, 'information': 3, 'hint': 4}.get(item.get('severity'), 1),
             'code': item.get('code', 'analysis_error'), 'source': 'chaos',
             'message': item['message']} for item in result['diagnostics']]


def convert(method, result, snapshot, files, *, position=None, hierarchical_symbols=False):
    mapping = TextMap(files[snapshot.name])
    if method in ('textDocument/references','textDocument/prepareRename','textDocument/rename'):
        def uri(name):
            target=snapshot.root/name;confined(snapshot.root,target)
            return snapshot.document_uris.get(name,target.as_uri())
        if method=='textDocument/references':
            if not result['complete']:raise ValueError('Incomplete references cannot be expressed as an LSP Location array')
            return [{'uri':uri(item['file']),'range':TextMap(files[item['file']]).span(item['from'],item['to'])}for item in result['references']]
        if method=='textDocument/prepareRename':
            if not result['target']:return None
            items=[item for item in result['references']if item['file']==snapshot.name and item['from']<=position<=item['to']]
            if not items:return None
            item=min(items,key=lambda item:item['to']-item['from'])
            return {'range':mapping.span(item['from'],item['to']),'placeholder':result['target']['name']}
        return {'documentChanges':[{'textDocument':{'uri':uri(name),'version':snapshot.document_versions.get(name)},'edits':[{'range':TextMap(files[name]).span(edit['from'],edit['to']),'newText':edit['text']}for edit in edits]}for name,edits in sorted(result['edits'].items())]}
    if method == 'textDocument/completion':
        return {'isIncomplete': False, 'items': [
            {'label': item['label'], 'kind': COMPLETION_KINDS.get(item['type'], 6),
             'detail': item['detail'], 'insertTextFormat': 1,
             'textEdit': {'range': mapping.span(item['from'], item['to']), 'newText': item['apply']}}
            for item in result['completions']]}
    if method == 'textDocument/hover':
        item = result['hover']
        return None if item is None else {'contents': {'kind': 'plaintext', 'value': item['contents']},
                                         'range': mapping.span(item['from'], item['to'])}
    if method == 'textDocument/signatureHelp':
        item = result['signatureHelp']
        if item is None:
            return None
        signature = {'label': item['label'], 'parameters': [
            # Names are exact substrings even when type/default display differs.
            {'label': p['name'], 'documentation': p['label']} for p in item['parameters']]}
        response = {'signatures': [signature], 'activeSignature': 0}
        if item.get('activeParameter') is not None:
            response['activeParameter'] = item['activeParameter']
            signature['activeParameter'] = item['activeParameter']
        else:
            # LSP defaults an omitted activeParameter to zero. Omitting the
            # parameter list keeps an unknown dynamic mapping from highlighting
            # an invented first argument while retaining the full signature.
            signature.pop('parameters')
        return response
    if method == 'textDocument/documentSymbol':
        if hierarchical_symbols is True:
            kinds = {'agent': 12, 'macro': 12, 'data': 5, 'constructor': 9,
                     'asyncFunction': 12, 'generator': 12, 'asyncGenerator': 12,
                     'asyncMethod': 6, 'generatorMethod': 6, 'asyncGeneratorMethod': 6}
            def document(item):
                return {'name': item['name'], 'detail': item['detail'],
                        'kind': kinds.get(item['kind'], SYMBOL_KINDS.get(item['kind'], 13)),
                        'range': mapping.span(item['range']['from'], item['range']['to']),
                        'selectionRange': mapping.span(item['selectionRange']['from'], item['selectionRange']['to']),
                        'children': [document(child) for child in item['children']]}
            return [document(item) for item in result['outline']['items']]
        # Flat symbols work with clients that do not negotiate hierarchical ones.
        return [{'name': item['name'], 'kind': SYMBOL_KINDS.get(item['kind'], 13),
                 'location': {'uri': snapshot.uri, 'range': mapping.span(item['from'], item['to'])}}
                for item in result['symbols']]
    if method == 'textDocument/definition':
        item = result.get('definition')
        if item is None or item['file'] not in files:
            return None
        target = snapshot.root / item['file']
        confined(snapshot.root, target)
        target_map = TextMap(files[item['file']])
        return {'uri': snapshot.document_uris.get(item['file'], target.as_uri()),
                'range': target_map.span(item['from'], item['to'])}
    if method == 'textDocument/semanticTokens/full':
        tokens = []
        for item in result['tokens']:
            kind = {'module': 'namespace', 'punctuation': 'operator'}.get(item['type'], item['type'])
            index = TOKEN_INDEX.get(kind, TOKEN_INDEX['variable'])
            tokens.extend((line, start, length, index) for line, start, length in mapping.pieces(item['from'], item['to']))
        data, old_line, old_start = [], 0, 0
        for line, start, length, index in sorted(tokens):
            data.extend((line - old_line, start - old_start if line == old_line else start, length, index, 0))
            old_line, old_start = line, start
        return {'data': data}
    raise ValueError('Unsupported language method')
