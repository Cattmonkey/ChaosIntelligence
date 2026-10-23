"""Bounded public conversation messages, independent of native SDK objects."""
import hashlib
import json
from .extensions import json_copy, valid_name


def dump(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def data(value, maximum=40000):
    copied=json_copy(value,max_chars=maximum)
    if len(dump(copied))>maximum:raise ValueError('Session JSON exceeds '+str(maximum)+' characters')
    return copied


def context_limit(value):
    if type(value) is not int or not 1000<=value<=200000:raise ValueError('Session context_limit must be 1000–200000')
    return value


def tool_alias(name):
    valid_name(name)
    return 'chaos_'+hashlib.sha256(name.encode('utf-8')).hexdigest()[:48]


def _text(value,nullable=False):
    if type(value) is not str and not(nullable and value is None):raise ValueError('Invalid Session message content')


def validate_message(message):
    m=data(message)
    if type(m) is not dict:raise ValueError('Session message must be a record')
    role=m.get('role')
    if role=='user':
        if set(m)!={'role','content'}:raise ValueError('Invalid user message fields')
        content=m['content']
        if type(content) is list:
            if not content or len(content)>33:raise ValueError('Invalid Session media parts')
            image=False
            for part in content:
                if type(part) is not dict:raise ValueError('Invalid Session media part')
                if part.get('type')=='text' and set(part)=={'type','text'}:_text(part['text'])
                elif part.get('type')=='image_url' and set(part)=={'type','image_url'}:
                    item=part['image_url']
                    if type(item) is not dict or set(item)!={'url'} or type(item['url']) is not str or not item['url'].startswith('data:image/'):
                        raise ValueError('Session images require a bounded data:image URL')
                    image=True
                else:raise ValueError('Unsupported Session media part')
            if not image:raise ValueError('Session media must include an image')
        else:_text(content)
    elif role=='assistant':
        if set(m)!={'role','content','tool_calls'} or type(m['tool_calls']) is not list or len(m['tool_calls'])>32:
            raise ValueError('Invalid assistant message fields')
        _text(m['content'],nullable=bool(m['tool_calls']))
        seen=set()
        for call in m['tool_calls']:
            if type(call) is not dict or set(call)!={'id','name','arguments'}:raise ValueError('Invalid assistant tool call')
            if type(call['id']) is not str or not 1<=len(call['id'])<=200 or call['id'] in seen:raise ValueError('Invalid or duplicate tool call ID')
            seen.add(call['id']);valid_name(call['name'])
            if type(call['arguments']) is not dict:raise ValueError('Tool arguments must be an object')
    elif role=='tool':
        if set(m)!={'role','tool_call_id','name','content'}:raise ValueError('Invalid tool message fields')
        if type(m['tool_call_id']) is not str or not 1<=len(m['tool_call_id'])<=200:raise ValueError('Invalid tool result ID')
        valid_name(m['name'])
    else:raise ValueError('Unsupported Session message role')
    return m


def context_size(messages):return len(dump(messages))


def validate_messages(messages,*,complete=True,context_limit=100000):
    if type(messages) is not list or len(messages)>10000:raise ValueError('Session history must be a bounded list')
    if type(context_limit) is not int or not 1000<=context_limit<=200000:raise ValueError('Invalid Session context limit')
    result=[];state='user';pending=[];media_allowed=False
    for raw in messages:
        m=validate_message(raw);role=m['role']
        if role=='user' and type(m['content']) is list:
            if state=='user':state='assistant'
            elif state!='assistant' or not media_allowed:raise ValueError('Media must start a turn or follow a completed tool batch')
            media_allowed=False
        elif role=='user':
            if state!='user':raise ValueError('User turn overlaps incomplete assistant/tool messages')
            state='assistant';media_allowed=False
        elif role=='assistant':
            if state!='assistant':raise ValueError('Unexpected assistant message')
            pending=[(c['id'],c['name'])for c in m['tool_calls']]
            state='tool' if pending else 'user';media_allowed=False
        else:
            if state!='tool' or not pending or pending[0]!=(m['tool_call_id'],m['name']):raise ValueError('Tool result does not match pending call order')
            pending.pop(0)
            if not pending:state='assistant';media_allowed=True
        result.append(m)
    if complete and state!='user':raise ValueError('Session history ends with an incomplete turn')
    if context_size(result)>context_limit:raise ValueError('Session active context exceeds its character budget; compact explicitly')
    return result


def input_message(value):
    value=data(value)
    return validate_message({'role':'user','content':value if type(value) is str else dump(value)})


def encode_messages(messages):
    """Canonical real names -> stable wire aliases, including revoked past tools."""
    result=[]
    for raw in messages:
        m=validate_message(raw)
        if m['role']=='assistant':
            message={'role':'assistant','content':m['content']}
            if m['tool_calls']:
                message['tool_calls']=[{'id':c['id'],'type':'function','function':{'name':tool_alias(c['name']),'arguments':dump(c['arguments'])}}for c in m['tool_calls']]
            result.append(message)
        elif m['role']=='tool':result.append({'role':'tool','tool_call_id':m['tool_call_id'],'content':dump(m['content'])})
        else:result.append(m)
    return result
