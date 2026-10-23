"""Closed public addresses for live Bot scopes and persistent Process roles.

An address selects a host-owned mailbox. It conveys no decision credential,
authority or executable text. Only declared Process role addresses are durable.
"""
import re


def validate_target(value):
    if type(value) is not dict or type(value.get('kind')) is not str:
        raise ValueError('target requires a closed Bot scope or Process role address')
    field, prefix = {'botScope': ('scopeId', 'bot-scope-'),
                     'processRole': ('roleId', 'bot-role-')}.get(value['kind'], (None, None))
    if (field is None or set(value) != {'kind', field} or type(value[field]) is not str
            or re.fullmatch(prefix + r'[0-9a-f]{32}', value[field]) is None):
        raise ValueError('target requires an actual Bot scope or persistent Process role identity')
    return {'kind': value['kind'], field: value[field]}


def totals(box):
    """Keep original run-wide bounds across active and retained mailboxes."""
    mailboxes = [box]
    mailboxes.extend(scope['mailbox'] for scope in box.get('botScopes', {}).values()
                     if scope['mailboxKind'] == 'scoped')
    return (sum(len(mailbox['messages']) for mailbox in mailboxes),
            sum(len(mailbox['queue']) for mailbox in mailboxes) +
            sum(scope.get('pending') is not None for scope in box.get('botScopes', {}).values()))


def private_mailbox(box, target):
    from .store import Conflict
    if target['kind'] != 'botScope':
        raise Conflict('A persistent role address requires its owning Process')
    scope = box.get('botScopes', {}).get(target['scopeId'])
    if scope is None or not scope['active']:
        raise Conflict('Bot scope is unknown, closed, or belongs to another run')
    if scope['mailboxKind'] != 'scoped':
        raise Conflict('Use the original run guidance entry for the legacy root Bot')
    return scope['mailbox'], scope
