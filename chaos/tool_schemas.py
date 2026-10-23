"""Provider schemas shared without loading Settings, tools or runtimes.

These are declaration metadata. Existing tool dispatchers retain their own
validation; this module neither validates payloads nor grants capabilities.
"""
CODE_SCHEMA={'type':'object','properties':{'language':{'type':'string','enum':['python','shell']},'code':{'type':'string'},'timeout':{'type':'number'}},'required':['code'],'additionalProperties':False}
IMAGE_SCHEMA={'type':'object','properties':{'prompt':{'type':'string'},'provider':{'type':'string'},'size':{'type':'string'}},'required':['prompt'],'additionalProperties':False}
DELEGATE_SCHEMA={'type':'object','properties':{'prompt':{'type':'string'},'agentId':{'type':'string'},'role':{'type':'string'}},'required':['prompt','role'],'additionalProperties':False}

def browser_schema():
    def row(action,extra=None,required=()):
        properties={'action':{'const':action}};properties.update(extra or {})
        return {'type':'object','properties':properties,'required':['action']+list(required),'additionalProperties':False}
    locator={'oneOf':[{'type':'object','properties':{'kind':{'const':kind},'value':{'type':'string','minLength':1,'maxLength':1000}},'required':['kind','value'],'additionalProperties':False}for kind in ('testId','label','css')]}
    locator['oneOf'].append({'type':'object','properties':{'kind':{'const':'role'},'role':{'type':'string','maxLength':1000},'name':{'type':'string','maxLength':1000}},'required':['kind','role','name'],'additionalProperties':False})
    targets=[{'selector':{'type':'string','minLength':1,'maxLength':1000}},{'locator':locator},
        {'sessionId':{'type':'string','minLength':1,'maxLength':128},'controlEpoch':{'type':'integer','minimum':1},'snapshotId':{'type':'integer','minimum':1},'ref':{'type':'string','minLength':1,'maxLength':128}}]
    variants=[row('navigate',{'url':{'type':'string','minLength':1,'maxLength':2048}},('url',)),row('snapshot'),row('screenshot'),row('observe',{'image':{'type':'boolean'}}),
       row('scroll',{'deltaX':{'type':'integer','minimum':-10000,'maximum':10000},'deltaY':{'type':'integer','minimum':-10000,'maximum':10000}},('deltaX','deltaY'))]
    for action,extra in [('click',{}),('type',{'text':{'type':'string','maxLength':100000}}),('press',{'key':{'type':'string','minLength':1,'maxLength':128}})]:
        for target in targets:
            fields={**target,**extra};variants.append(row(action,fields,tuple(fields)))
    return {'type':'object','oneOf':variants}

BROWSER_SCHEMA=browser_schema()
