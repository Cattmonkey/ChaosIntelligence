"""Hot, versioned deployment configuration. Credentials never cross public views."""
import copy
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlsplit
from .store import Conflict


def defaults():
    provider = lambda model: {'baseURL': 'https://api.openai.com/v1', 'apiKey': '', 'model': model, 'models': [], 'discoveredAt': None}
    return {'version': 1, 'providers': {'text': provider('gpt-6.1-sol'), 'image1k': provider('gpt-image-2-1K'), 'image4k': provider('image2-4k'), 'grok': provider('grok-4')},
            'roles': {'planner': {'provider': 'text', 'model': 'gpt-6-astra', 'families': ['gpt-astra', 'gpt-sol'], 'autoUpgrade': True},
                      'executor': {'provider': 'text', 'model': 'gpt-6-sol', 'families': ['gpt-sol'], 'autoUpgrade': True},
                      'reviewer': {'provider': 'text', 'model': 'gpt-6.1-sol', 'families': ['gpt-sol'], 'autoUpgrade': True},
                      'image': {'provider': 'image1k', 'model': 'gpt-image-2-1K', 'families': [], 'autoUpgrade': False},
                      'grok': {'provider': 'grok', 'model': 'grok-4', 'families': ['grok'], 'autoUpgrade': True}},
            'agents': {}, 'runtime': {'maxConcurrent': 4, 'maxSteps': 12, 'timeout': 120, 'codeEnabled': True, 'browserEnabled': True,
              'workspace': str(Path.cwd() / '.chaos' / 'tasks'), 'codexPath': 'codex', 'continuousEnabled': True,
              'intervalSeconds': 15, 'discoveryInterval': 3600, 'backend': 'native'}, 'plugins': []}


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2000: raise ValueError('baseURL must be a URL')
    u = urlsplit(value)
    if u.username or u.password or u.query or u.fragment or not u.hostname:
        raise ValueError('baseURL cannot contain credentials, query or fragment')
    if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('baseURL requires HTTPS or local HTTP')
    return value.rstrip('/')


def model_identity(model):
    """Parse provider model identifiers, never task prose or semantic intent."""
    match = re.fullmatch(r'gpt-(\d+(?:\.\d+)*)(?:-(astra|sol))', model)
    if match: return 'gpt-' + match.group(2), tuple(int(n) for n in match.group(1).split('.'))
    match = re.fullmatch(r'grok-(\d+(?:\.\d+)*)(?:-(\d+))?', model)
    if match: return 'grok', tuple(int(n) for n in match.group(1).split('.')) + ((int(match.group(2)),) if match.group(2) else ())
    return model, ()


def _merge(old, patch):
    result = copy.deepcopy(old)
    for key, value in patch.items():
        if value is None: result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict): result[key] = _merge(result[key], value)
        else: result[key] = copy.deepcopy(value)
    return result


class Settings:
    def __init__(self, path=None, *, client_factory=None):
        self.path = None if path == ':memory:' else Path(path or os.environ.get('CHAOS_CONFIG', Path.home() / '.config' / 'chaos-intelligence' / 'settings.json')).expanduser().resolve()
        self.lock = threading.RLock(); self.client_factory = client_factory; self.health = {}; self._data = defaults()
        self._environment_keys = set()
        if self.path and self.path.exists():
            self._data = _merge(self._data, json.loads(self.path.read_text(encoding="utf-8")))
            self._validate(self._data)

    def _validate(self, data):
        if set(data) != {'version','providers','roles','agents','runtime','plugins'}: raise ValueError('Unknown configuration field')
        if type(data['version']) is not int or data['version'] < 1: raise ValueError('Invalid version')
        if not isinstance(data['providers'],dict) or not 1 <= len(data['providers']) <= 32: raise ValueError('Require 1–32 providers')
        for name, p in data['providers'].items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',name) or not isinstance(p,dict): raise ValueError('Invalid provider identifier')
            if set(p) - {'baseURL','apiKey','model','models','discoveredAt'}: raise ValueError('Unknown provider field')
            p['baseURL'] = validate_url(p.get('baseURL'))
            if not isinstance(p.get('apiKey',''),str) or len(p.get('apiKey','')) > 500: raise ValueError('Invalid credential')
            if not isinstance(p.get('model',''),str) or len(p.get('model','')) > 200: raise ValueError('Invalid model')
            if not isinstance(p.get('models',[]),list) or len(p.get('models',[])) > 5000: raise ValueError('Invalid model catalog')
            for m in p.get('models',[]):
                if not isinstance(m,dict) or set(m)-{'id','created','ownedBy'} or not isinstance(m.get('id'),str) or len(m['id'])>200: raise ValueError('Invalid model metadata')
        for collection in ('roles','agents'):
            if not isinstance(data[collection],dict) or len(data[collection])>500: raise ValueError('Invalid routing table')
            for name, route in data[collection].items():
                if not isinstance(name,str) or not 1<=len(name)<=128 or not isinstance(route,dict): raise ValueError('Invalid route')
                if set(route)-{'provider','model','families','autoUpgrade','backend','baseURL','apiKey','role'}: raise ValueError('Unknown route field')
                if 'provider' in route and route['provider'] not in data['providers']: raise ValueError('Unknown route provider')
                if 'role' in route and route['role'] not in data['roles']: raise ValueError('Unknown role')
                if 'baseURL' in route:route['baseURL']=validate_url(route['baseURL'])
                if 'apiKey' in route and (not isinstance(route['apiKey'],str) or len(route['apiKey'])>500):raise ValueError('Invalid agent credential')
                if 'model' in route and (not isinstance(route['model'],str) or len(route['model'])>200):raise ValueError('Invalid route model')
                if 'autoUpgrade' in route and type(route['autoUpgrade']) is not bool:raise ValueError('autoUpgrade must be boolean')
                if route.get('backend','native') not in ('native','codex'):raise ValueError('Unknown backend')
                if 'families' in route and (not isinstance(route['families'],list) or not all(isinstance(v,str) and len(v)<=200 for v in route['families'])):raise ValueError('Invalid model families')
        rt=data['runtime']; expected=defaults()['runtime']
        if not isinstance(rt,dict) or set(rt)!=set(expected):raise ValueError('Unknown or missing runtime option')
        for key,low,high in [('maxConcurrent',1,8),('maxSteps',1,100),('timeout',5,3600),('intervalSeconds',1,3600),('discoveryInterval',60,86400)]:
            if type(rt[key])not in (int,float) or not math.isfinite(rt[key]) or not low<=rt[key]<=high:raise ValueError('Invalid '+key)
        for key in ('maxConcurrent','maxSteps'):
            if type(rt[key])is not int:raise ValueError(key+' must be integer')
        for key in ('codeEnabled','browserEnabled','continuousEnabled'):
            if type(rt[key])is not bool:raise ValueError(key+' must be boolean')
        for key in ('workspace','codexPath'):
            if not isinstance(rt[key],str) or not rt[key] or len(rt[key])>2000 or '\x00'in rt[key]:raise ValueError('Invalid '+key)
        if rt['backend']not in ('native','codex'):raise ValueError('Invalid backend')
        if not isinstance(data['plugins'],list) or len(data['plugins'])>100:raise ValueError('Invalid plugins')
        # Plugins are registered host extensions, never arbitrary module paths from HTTP.
        if len({p.get('id')for p in data['plugins']if isinstance(p,dict)})!=len(data['plugins']):raise ValueError('Duplicate or invalid plugin setting')
        for p in data['plugins']:
            if not isinstance(p,dict) or p.get('id')not in ('code','browser','image','native','codex') or set(p)-{'id','enabled'} or not isinstance(p.get('id'),str) or type(p.get('enabled'))is not bool:raise ValueError('Plugin needs id and enabled')

    @contextmanager
    def _disk_guard(self):
        if not self.path:
            yield;return
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock_path=self.path.with_suffix(self.path.suffix+'.lock')
        with open(lock_path,'a+b')as handle:
            os.chmod(lock_path,0o600)
            if os.name=='posix':
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
            else:
                import msvcrt
                handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
            try:
                self._reload();yield
            finally:
                if os.name=='posix':fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
                else:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

    def _reload(self):
        if self.path and self.path.exists():
            data=json.loads(self.path.read_text(encoding="utf-8"));self._validate(data);self._data=data

    def _write(self, data):
        if not self.path:return
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,temporary=tempfile.mkstemp(prefix='.settings-',dir=str(self.path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8')as f:
                if hasattr(os,'fchmod'):os.fchmod(f.fileno(),0o600)
                else:os.chmod(temporary,0o600)
                json.dump(data,f,ensure_ascii=False,allow_nan=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(temporary,self.path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)

    def public(self):
        with self.lock:
            self._reload()
            data=copy.deepcopy(self._data)
            for collection in ('providers','agents','roles'):
                for p in data[collection].values():
                    key=p.pop('apiKey','');p['hasKey']=bool(key);p['keyHint']=('••••'+key[-4:] if len(key)>8 else '••••')if key else ''
            return data

    def update(self, patch, version=None):
        patch=copy.deepcopy(patch)
        version=patch.pop('version',version)
        with self.lock,self._disk_guard():
            if type(version)is not int or version!=self._data['version']:raise Conflict('Configuration changed; reload before saving')
            if set(patch)-{'providers','roles','agents','runtime','plugins'}:raise ValueError('Unknown configuration patch')
            for collection in ('providers','agents','roles'):
                if collection in patch and not isinstance(patch[collection],dict):raise ValueError('Routing collection must be an object')
                for p in (patch.get(collection)or{}).values():
                    if isinstance(p,dict):
                        p.pop('hasKey',None);p.pop('keyHint',None)
                        if p.pop('clearKey',False):p['apiKey']=''if collection=='providers'else None
                        elif p.get('apiKey')=='':p.pop('apiKey')
            candidate=_merge(self._data,patch);candidate['version']+=1
            self._validate(candidate);self._write(candidate);self._data=candidate
            return self.public()

    def resolve(self, agent_id='', role='executor'):
        with self.lock:
            self._reload()
            d=copy.deepcopy(self._data)
            # Compatibility is a call-time overlay on this host's configuration.
            # Never replace the host, persist environment secrets, or reuse the
            # saved endpoint's discovered model catalog for a temporary endpoint.
            url=os.environ.get('CHAOS_MODEL_URL')
            if url:
                key=os.environ.get('CHAOS_MODEL_KEY','')
                model=os.environ.get('CHAOS_MODEL_NAME','')
                d['providers']['text'].update(baseURL=url.removesuffix('/chat/completions'),
                    apiKey=key,model=model,models=[],discoveredAt=None)
                for name in ('planner','executor','reviewer'):
                    if d['roles'].get(name,{}).get('provider','text')=='text' and name in d['roles']:
                        d['roles'][name].update(model=model,autoUpgrade=False)
                self._validate(d)
                if key:self._environment_keys.add(key)
            agent=d['agents'].get(agent_id,{})
            role=agent.get('role',role)
            if role not in d['roles']:raise ValueError('Unknown model role')
            route={**d['roles'][role],**agent};pid=route.get('provider','text');p=d['providers'][pid]
            explicit=route.get('model')or p.get('model');catalog={m['id']for m in p.get('models',[])}
            families=route.get('families',[]);candidates=[]
            # Route specificity wins. Agent endpoints/credentials have their own catalog authority.
            auto=route.get('autoUpgrade',False) and not any(k in route for k in ('baseURL','apiKey'))
            if auto and catalog:
                for family in families:
                    matching=[m for m in catalog if model_identity(m)[0]==family]
                    candidates.extend(sorted(matching,key=lambda m:model_identity(m)[1],reverse=True))
            if explicit:candidates.insert(0,explicit) if not auto else candidates.append(explicit)
            candidates=list(dict.fromkeys(candidates))
            available=[m for m in candidates if self.health.get((pid,m),0)<=time.monotonic()]
            if not available:available=candidates
            if not available:raise ValueError('No model configured')
            return {**d['runtime'],'provider':pid,'role':role,'baseURL':route.get('baseURL',p['baseURL']),'apiKey':route.get('apiKey',p.get('apiKey','')),
                    'model':available[0],'candidates':available,'backend':route.get('backend',d['runtime']['backend']),'configVersion':d['version']}

    def report_failure(self, provider, model):
        with self.lock:self.health[(provider,model)]=time.monotonic()+60

    def report_success(self, provider, model):
        with self.lock:self.health.pop((provider,model),None)

    def client(self, provider=None, *, resolved=None):
        if resolved is None:
            with self.lock:
                self._reload();resolved=copy.deepcopy(self._data['providers'][provider or 'text'])
        if not resolved.get('apiKey'):raise ValueError('Provider key is not configured')
        if self.client_factory:return self.client_factory(resolved)
        from openai import OpenAI
        import httpx
        return OpenAI(base_url=resolved['baseURL'],api_key=resolved['apiKey'],timeout=20,max_retries=0,http_client=httpx.Client(follow_redirects=False,timeout=20,trust_env=False))

    def discover(self, provider):
        with self.lock:
            self._reload()
            if provider not in self._data['providers']:raise ValueError('Unknown provider')
            original=copy.deepcopy(self._data['providers'][provider])
        client=self.client(resolved=original)
        try:
            rows=client.models.list();models=[]
            for m in rows:
                mid=getattr(m,'id',None)
                if isinstance(mid,str) and len(mid)<=200:
                    models.append({'id':mid,'created':getattr(m,'created',None),'ownedBy':getattr(m,'owned_by',None)})
                if len(models)>=5000:break
        except Exception as e:
            code=getattr(e,'status_code',None)
            raise RuntimeError('Model discovery failed'+(' (HTTP '+str(code)+')'if code else ' (connection or protocol error)'))from None
        finally:
            if hasattr(client,'close'):client.close()
        with self.lock,self._disk_guard():
            current=self._data['providers'].get(provider,{})
            if (current.get('baseURL'),current.get('apiKey'))!=(original['baseURL'],original['apiKey']):raise Conflict('Provider changed during discovery; retry')
            data=copy.deepcopy(self._data);data['providers'][provider].update(models=models,discoveredAt=time.time());data['version']+=1
            self._validate(data);self._write(data);self._data=data
            return {'provider':provider,'models':models,'version':data['version']}

    def runtime(self):
        with self.lock:
            self._reload();return copy.deepcopy(self._data['runtime'])

    def redact(self, value):
        with self.lock:
            keys=[p.get('apiKey')for group in ('providers','agents','roles')for p in self._data[group].values() if p.get('apiKey')]
            keys.extend(self._environment_keys)
            if os.environ.get('CHAOS_MODEL_URL') and os.environ.get('CHAOS_MODEL_KEY'):
                keys.append(os.environ['CHAOS_MODEL_KEY'])
        def clean(v):
            if isinstance(v,str):
                for key in keys:v=v.replace(key,'[redacted]')
                return v
            if isinstance(v,list):return [clean(x)for x in v]
            if isinstance(v,dict):return {k:('[redacted]'if k.lower()in ('apikey','authorization','api_key')else clean(x))for k,x in v.items()}
            return v
        return clean(value)
