"""Real Framework authority and source/IR with only the browser driver stubbed."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chaos import Framework,Settings
from chaos.computer_host import ComputerHost


class Driver:
    def __init__(self,*args,**kwargs):pass
    def close(self):pass


class ComputerAuthorityIntegration(unittest.TestCase):
    def execute(self,source,mode='source'):
        calls=[]
        def tool(host,ctx,resource,command,allowed):
            calls.append(command['action'])
            return {'sessionId':'fixture','controlEpoch':1,'snapshotId':len(calls),'url':'about:blank','title':'fixture',
                    'viewport':{'width':20,'height':20},'cursor':{'x':0,'y':0},'truncated':{'text':False,'targets':False},
                    'snapshot':'button','targets':[{'ref':'e1','text':'Button','tag':'button'}]}
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(':memory:');settings.update({'runtime':{'workspace':directory}},1)
            with patch('chaos.computer_host.BrowserBroker',Driver),patch.object(ComputerHost,'_tool',tool),Framework(settings=settings) as framework:
                project=framework.create_project('computer',source=source,capabilities=['browser.control'])
                run=framework.start('computer',project['version'],None,mode=mode)
                result=run.result(10)
                events=framework.events('computer',run_id=run.id)['items']
                return result,calls,events

    PREFIX='''let c=await computer_open();let o=await computer_observe(c,false);let a=computer_action(o,{action:"click",ref:"e1"});
let roles=team_from({kind:"team",name:"roles",entry:"main",roles:[{id:"main",parent:null,capabilities:[],agent:{kind:"agent",name:"main",config:{provider:"local",tools:[]}}}]});'''

    def test_role_resource_return_denied_before_effect_and_original_stays_usable(self):
        source=self.PREFIX+'''let role=team_bind(roles,{main:fn(value){[{computer:c,action:a}]}});let narrow=(await role(null))[0];
let error=try{await narrow.computer(narrow.action)}catch e{e.code};let unused=action_info(a).used;
let successful=await c(a);[error,unused,action_info(narrow.action).used,observation_info(successful).receipt.status]'''
        result,calls,events=self.execute(source)
        self.assertEqual(result,['capability_error',False,True,'completed'])
        self.assertEqual(calls,['observe','click'])
        self.assertTrue(any(row['type']=='team.role.completed' for row in events))

    def test_source_and_graph_enforce_identical_role_resource_authority(self):
        source=self.PREFIX+'''let role=team_bind(roles,{main:fn(value){c}});let narrow=await role(null);
[try{await computer_close(narrow)}catch e{e.code},computer_info(c).status]'''
        direct,calls,_=self.execute(source,'source');graph,graph_calls,_=self.execute(source,'graph')
        self.assertEqual(direct,['capability_error','open']);self.assertEqual(direct,graph)
        self.assertEqual(calls,graph_calls);self.assertEqual(calls,['observe'])

    def test_narrow_observation_cannot_authorize_act_with_original_wide_handle(self):
        source=self.PREFIX+'''let role=team_bind(roles,{main:fn(value){o}});let observed=await role(null);
let narrowed=computer_action(observed,{action:"click",ref:"e1"});
[try{await c(narrowed)}catch e{e.code},action_info(narrowed).used,observation_info(observed).text]'''
        result,calls,_=self.execute(source)
        self.assertEqual(result,['capability_error',False,'button']);self.assertEqual(calls,['observe'])


if __name__=='__main__':unittest.main()
