"""Pure shared schemas for explicit browser-backed language actions."""
import copy
import urllib.parse
from .language import LanguageError
from .session_protocol import data


def error(message):raise LanguageError(message,code='computer_error')


def validate_url(value):
    if type(value) is not str or not 1<=len(value)<=2048:error('Computer URL requires 1–2048 characters')
    try:
        parsed=urllib.parse.urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or any(ord(c)<32 for c in value):raise ValueError()
        parsed.port
    except ValueError:error('Computer navigation requires an HTTP(S) URL without credentials')
    return value


def action_schema(*, registered=False):
    def variant(kind,fields):return {'type':'object','additionalProperties':False,'required':['action']+list(fields),'properties':{'action':{'const':kind},**fields}}
    ref={'type':'string','minLength':1,'maxLength':128}
    variants=[
        variant('navigate',{'url':{'type':'string','minLength':1,'maxLength':2048}}),
        variant('click',{'ref':ref}),
        variant('type',{'ref':ref,'text':{'type':'string','maxLength':100000}}),
        variant('press',{'ref':ref,'key':{'type':'string','minLength':1,'maxLength':128}}),
        variant('scroll',{'deltaX':{'type':'integer','minimum':-10000,'maximum':10000},'deltaY':{'type':'integer','minimum':-10000,'maximum':10000}})]
    if registered:
        variants.extend([variant('input',{'ref':ref,'text':{'type':'string','maxLength':100000}}),
          variant('launch',{'packageName':{'type':'string','minLength':1,'maxLength':240}}),
          variant('button',{'button':{'type':'string','minLength':1,'maxLength':128}}),
          variant('swipe',{'direction':{'enum':['up','down','left','right']},'distance':{'type':'integer','minimum':1,'maximum':10000}})])
    return {'oneOf':variants}


def configuration_schema():
    return {'open':{'type':'object','additionalProperties':False,'properties':{'url':{'anyOf':[{'type':'null'},{'type':'string','minLength':1,'maxLength':2048}]}}},
            'connect':{'type':'object','additionalProperties':False,'required':['key'],'properties':{'key':{'type':'string','pattern':'^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'}}},
            'release':{'description':'Release an owned Computer; persistent sessions retain their live browser; ephemeral Computers close.'},
            'reconcile':{'type':'object','additionalProperties':False,'required':['evidence'],'properties':{'evidence':{'type':'string','minLength':1,'maxLength':1000,'pattern':'\\S'}},'description':'Requires an owned persistent Computer and its latest Observation; evidence is not proof of external correctness.'},
            'persistence':{'driversPerEngine':16,'sessionsPerProject':64,'storageBytes':1048576,'restartRestores':['cookies','localStorage'],'restartRestoresPage':False,'automaticReplay':False},
            'action':action_schema(),'observe':{'type':'object','additionalProperties':False,'properties':{'image':{'type':'boolean','default':True}}},
            'registered':{'attach':{'name':'trusted registered binding'},'capability':'computer.control','action':action_schema(registered=True),'persistent':False,'automaticReplay':False},
            'backend':'browser','limits':{'computersPerRun':4,'observationsPerRun':64,'targets':40,'text':12000,'modelImageBase64':16000,'previewImageBase64':180000}}


def validate_action(spec, *, registered=False):
    if type(registered)is not bool:error('registered must be Bool')
    spec=data(spec,110000)
    if type(spec) is not dict:error('ComputerAction needs an explicit record')
    shapes={'navigate':{'action','url'},'click':{'action','ref'},'type':{'action','ref','text'},'press':{'action','ref','key'},'scroll':{'action','deltaX','deltaY'}}
    if registered:shapes.update(input={'action','ref','text'},launch={'action','packageName'},button={'action','button'},swipe={'action','direction','distance'})
    action=spec.get('action')
    if type(action) is not str or action not in shapes or set(spec)!=shapes[action]:error('Unknown action or unexpected ComputerAction fields')
    if action=='navigate':validate_url(spec['url'])
    if 'ref' in spec and (type(spec['ref']) is not str or not 1<=len(spec['ref'])<=128):error('Action ref must be a bounded target reference')
    if action in ('type','input') and (type(spec['text']) is not str or len(spec['text'])>100000):error('Type text exceeds its bound')
    if action=='press' and (type(spec['key']) is not str or not 1<=len(spec['key'])<=128 or any(ord(c)<32 for c in spec['key'])):error('Press key must be a bounded keyboard key')
    if action=='scroll' and any(type(spec[k]) is not int or not -10000<=spec[k]<=10000 for k in ('deltaX','deltaY')):error('Scroll deltas must be Int in -10000..10000')
    if action in ('launch','button'):
        key='packageName' if action=='launch' else 'button';maximum=240 if action=='launch' else 128
        if type(spec[key])is not str or not 1<=len(spec[key])<=maximum or any(ord(c)<32 for c in spec[key]):error('Invalid '+key)
    if action=='swipe' and (type(spec['direction'])is not str or spec['direction']not in ('up','down','left','right') or type(spec['distance'])is not int or not 1<=spec['distance']<=10000):error('Invalid swipe')
    return copy.deepcopy(spec)
