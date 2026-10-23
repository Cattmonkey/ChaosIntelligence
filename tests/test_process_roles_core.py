"""Explicit durable role identity, namespace and native nested Bot composition."""
import copy
import hashlib
import json
import time
import unittest
from unittest.mock import patch

from chaos import Framework, Settings
from chaos.bot_host import BotStale
from chaos.language import LanguageError
from chaos.process_values import validate_definition, definition_hash
from chaos.store import Conflict

DEFINITION={'kind':'process','name':'roles','module':'roles.chaos','export':'step','intervalSeconds':None,'maxTurns':None}
VERIFY='fn(s){{done:true,context:null,output:s.evidence[0]}}'


def fixture(test,path=':memory:',*,roles=('left','right'),source=None):
    app=Framework(path,settings=Settings(':memory:'));test.addCleanup(app.close);app._engine.pause_dispatch(True)
    app.create_project('p',source='null',files={'roles.chaos':source or 'fn step(c){process_step(c.state,null,false)};export {step}'},capabilities=['process.manage'])
    definition={**DEFINITION,'roles':list(roles)}
    app.create_process('p',1,'worker',definition,state={'kept':True})
    return app


def guide(app,role,value,identity,run_id=None):
    target=app.get_process('p','worker')['botRoles'][role]['target']
    return app._store.processes.guide('p','worker',value,identity,{'process.manage'},lambda v:v,run_id=run_id,target=target)


def claim(app,run='turn-one'):
    with app._engine.idle:return app._store.processes.claim('p','worker',run)


def open_pair(app,current,role='left'):
    store=app._store.processes;box=store.role_box(current,role);root='root-scope';child='role-scope'
    root_payload={'scopeId':root,'revision':0,'mailboxKind':'run','ancestry':[]}
    store.bot_operation(current,'open',root_payload,lambda v:v)
    child_payload={'scopeId':child,'revision':box['revision'],'mailboxKind':'scoped','roleKey':role,'roleId':box['roleId'],
                   'parentScopeId':root,'rootScopeId':root,'ancestry':[{'scopeId':root,'decisionId':'root-plan','revision':0}]}
    store.bot_operation(current,'open',child_payload,lambda v:v)
    return root_payload,child_payload


class ProcessRoleCore(unittest.TestCase):
    def test_legacy_definition_hash_stays_exact_and_roles_are_explicit(self):
        expected=hashlib.sha256(json.dumps(DEFINITION,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(definition_hash(DEFINITION),expected);self.assertNotIn('roles',validate_definition(DEFINITION))
        for roles in (['left','left'],['with space'],[True],['a']*17,'left'):
            with self.subTest(roles=roles),self.assertRaises(LanguageError):validate_definition({**DEFINITION,'roles':roles})
        self.assertEqual(validate_definition({**DEFINITION,'roles':['left','Reviewer-2']})['roles'],['left','Reviewer-2'])

    def test_role_namespaces_keep_original_receipt_and_default_purpose(self):
        app=fixture(self);a=guide(app,'left',{'n':1},'same','original-run');b=guide(app,'right',{'n':2},'same')
        self.assertNotEqual(a['target'],b['target']);self.assertEqual(a['revision'],1);self.assertEqual(b['revision'],1)
        self.assertEqual(guide(app,'left',{'n':1},'same','later-run'),a)
        with self.assertRaises(Conflict):guide(app,'left',{'n':9},'same')
        default=app._store.processes.guide('p','worker',1,'same',{'process.manage'},lambda v:v)
        self.assertEqual(set(default),{'key','runId','messageId','revision','status'})
        with self.assertRaises(Conflict):app.send_process('p','worker',1,message_id='same')
        self.assertEqual(app.get_process('p','worker')['guidanceRevision'],1)

    def test_default_and_all_roles_share_64_uncommitted_limit(self):
        app=fixture(self)
        for i in range(63):guide(app,'left'if i%2 else'right',i,'g-'+str(i))
        app._store.processes.guide('p','worker',1,'default',{'process.manage'},lambda v:v)
        with self.assertRaises(ValueError):guide(app,'left',99,'overflow')
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_role_messages').fetchone()[0],63)
        self.assertEqual(guide(app,'right',0,'g-0')['revision'],1)

    def test_role_identity_retains_on_edit_retires_on_remove_and_readd(self):
        app=fixture(self);old=app.get_process('p','worker');first=old['botRoles']['left']['roleId']
        changed={**old['definition'],'name':'new program description'}
        current=app._store.processes.configure('p','worker',old['revision'],changed,{'process.manage'})
        self.assertEqual(current['botRoles']['left']['roleId'],first)
        current=app._store.processes.configure('p','worker',current['revision'],{**changed,'roles':['right']},{'process.manage'})
        self.assertIn(first,current['retiredBotRoleIds'])
        current=app._store.processes.configure('p','worker',current['revision'],changed,{'process.manage'})
        self.assertNotEqual(current['botRoles']['left']['roleId'],first)
        guide(app,'left',1,'pending');before=app.get_process('p','worker')
        with self.assertRaises(Conflict):app._store.processes.configure('p','worker',before['revision'],{**changed,'roles':['right']},{'process.manage'})
        self.assertEqual(app.get_process('p','worker'),before)

    def test_one_active_scope_per_role_and_private_claim_cannot_be_forged(self):
        app=fixture(self);current=claim(app);root,child=open_pair(app,current)
        duplicate={**child,'scopeId':'another-child'}
        with self.assertRaises(LanguageError):app._store.processes.bot_operation(current,'open',duplicate,lambda v:v)
        self.assertEqual(len(app.get_process('p','worker')['pending']['botWork']['children']),1)
        forged=copy.deepcopy(current);forged['pending']['lease']='not-the-private-lease'
        with self.assertRaises(Conflict):app._store.processes.role_box(forged,'left')

    def test_persisted_role_revision_stales_only_its_branch_and_root_guide_stales_all(self):
        app=fixture(self);current=claim(app);root,left=open_pair(app,current)
        right_box=app._store.processes.role_box(current,'right');right={**left,'scopeId':'right-scope','roleKey':'right','roleId':right_box['roleId']}
        app._store.processes.bot_operation(current,'open',right,lambda v:v)
        guide(app,'left',1,'left-change')
        def admit(payload,identity):
            return app._store.processes.bot_operation(current,'admit',{**payload,'decisionId':'decision','phase':'execution','action':{'id':identity,'kind':'fixture','status':'admitted'}},lambda v:v)
        with self.assertRaises(BotStale)as stale:admit(left,'left-action')
        self.assertEqual(stale.exception.scope_id,left['scopeId'])
        admit(right,'right-action')
        app._store.processes.guide('p','worker',1,'root-change',{'process.manage'},lambda v:v)
        with self.assertRaises(BotStale)as stale:admit(right,'later-action')
        self.assertEqual(stale.exception.scope_id,root['scopeId'])
        actions=app.get_process('p','worker')['pending']['botWork']['actions']
        self.assertEqual([a['id']for a in actions],['right-action'])

    def test_role_journal_failure_rolls_back_identity_revision_and_message(self):
        app=fixture(self);before=app.get_process('p','worker')
        with patch.object(app._store,'_event',side_effect=OSError('journal unavailable')):
            with self.assertRaises(OSError):guide(app,'left',42,'failed')
        self.assertEqual(app.get_process('p','worker'),before)
        self.assertEqual(app._store.db.execute('SELECT count(*) FROM process_role_messages').fetchone()[0],0)

    def test_native_nested_role_adopts_real_queue_and_keeps_root_checkpoint(self):
        body=('async fn step(c){let child=bot("role",fn(s){s.guidance[0].input},fn(p,s){p+2},'+VERIFY+',role="left");'
              'let root=bot("root",fn(s){null},async fn(p,s){(await child()).output},'+VERIFY+');'
              'let r=await root();process_step(r.state,r.output,r.done)};export {step}')
        app=fixture(self,source=body);receipt=guide(app,'left',40,'answer');app._engine.pause_dispatch(False)
        end=time.monotonic()+5
        while time.monotonic()<end:
            row=app.get_process('p','worker')
            if row['status']in ('completed','needs_reconciliation'):break
            time.sleep(.005)
        self.assertEqual(row['status'],'completed');self.assertEqual(row['output'],42);self.assertEqual(row['state']['goal'],'root')
        self.assertEqual(guide(app,'left',40,'answer')['status'],'applied')
        item=app._store.db.execute('SELECT status,input FROM process_role_messages').fetchone()
        self.assertEqual(item['status'],'guide_consumed');self.assertIsNone(json.loads(item['input'])['input'])
        opened=[e['payload']for e in app.events('p',limit=300)['items']if e['type']=='bot.scope.opened']
        bound=[p for p in opened if p.get('roleId')==receipt['target']['roleId']]
        self.assertEqual(len(bound),1);self.assertEqual(bound[0]['roleKey'],'left');self.assertEqual(bound[0]['processKey'],'worker')
        durable=[e['payload']['receipt']for e in app.events('p',limit=300)['items']
                 if e['type']in ('process.guidance.queued','process.bot.receive','process.bot.adopt')and 'receipt'in e['payload']]
        self.assertEqual([r['status']for r in durable],['queued','received','applied'])
        self.assertTrue(all(r['runId']is None and r['key']=='worker'and r['target']==receipt['target']for r in durable))
