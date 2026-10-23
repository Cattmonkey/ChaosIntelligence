"""Engine-owned browser drivers; durable leases never transfer an in-flight effect."""
import threading
import time
import uuid
from pathlib import Path
from .language import LanguageError
from .execution import BrowserBroker


class ComputerPool:
    MAX_DRIVERS = 16

    def __init__(self,engine):
        self.engine=engine;self.store=engine.store.computers
        self.lock=threading.RLock();self.entries={};self.closed=False

    def _checkpoint(self,broker):
        value=broker.checkpoint_state()
        # Storage is deliberately not redacted: modifying a cookie would corrupt
        # its restore semantics. Only the public URL goes through the redactor.
        value['url']=self.engine.settings.redact(value['url'])
        self.store._checkpoint(value)
        return value

    @staticmethod
    def _close_driver(entry,deadline=None):
        entry['closeStarted']=True
        if deadline is None:entry['broker'].close()
        else:entry['broker'].close(timeout=max(0,deadline-time.monotonic()))

    def _retire_idle(self,key,entry,deadline=None):
        # Release already committed the last restorable storage checkpoint.
        # Shutdown must not start another potentially blocking storage read.
        self._close_driver(entry,deadline)
        self.store.detach_idle(key[0],key[1],entry['generation'])
        self.entries.pop(key,None)

    def acquire(self,ctx,key,resolved):
        pair=(ctx.project['id'],key)
        with self.lock:
            if self.closed:raise LanguageError('Computer pool is closing',code='computer_error')
            entry=self.entries.get(pair)
            if entry is not None and (entry['claim'] is not None or entry.get('retiring')):
                raise LanguageError('Computer session is already leased',code='computer_busy')
            if entry is None and len(self.entries)>=self.MAX_DRIVERS:
                raise LanguageError('Computer driver capacity is full; close an existing Computer before connecting a new session',code='computer_busy')
            continued=entry is not None
            claim=self.store.claim_session(pair[0],pair[1],ctx.run_id)
            try:
                if entry is None:
                    broker=BrowserBroker(Path(resolved['workspace'])/pair[0],timeout=min(15,resolved['timeout']),storage_state=claim['storage'])
                    entry={'broker':broker,'claim':claim,'generation':claim['generation']}
                    self.entries[pair]=entry
                else:entry.update(claim=claim,generation=claim['generation'])
                return {'id':uuid.uuid4().hex,'persistent':True,'backend':'browser','status':'open','broker':entry['broker'],
                        'lock':threading.Lock(),'latest':None,'claim':claim,'sessionKey':key,
                        'stableSessionId':claim['id'],'generation':claim['generation'],
                        'requiresReconcile':claim['requiresReconcile'],'continued':continued}
            except BaseException:
                self.store.release_session(claim)
                raise

    def validate(self,resource):
        claim=resource['claim'];pair=(claim['projectId'],claim['key'])
        with self.lock:
            entry=self.entries.get(pair)
            if entry is None or entry['claim'] is not claim:
                raise LanguageError('Computer lease is stale',code='computer_error')
            row=self.store.validate_lease(claim)
            resource['requiresReconcile']=resource['requiresReconcile'] or row['requiresReconcile']
            return row

    def uncertain(self,resource,action_id,error):
        resource['requiresReconcile']=True
        self.store.uncertain_session(resource['claim'],action_id,error)

    def reconcile(self,resource,observation_id,evidence):
        self.validate(resource)
        self.store.reconcile_session(resource['claim'],observation_id,evidence)
        resource['requiresReconcile']=False

    def release(self,resource,*,close=False):
        claim=resource['claim'];pair=(claim['projectId'],claim['key'])
        with self.lock:
            self.validate(resource)
            entry=self.entries[pair]
        # Host holds this resource's operation lock; the durable claim remains
        # held throughout cleanup. Unrelated Computers never wait on this I/O.
        error=None;checkpoint=None
        try:checkpoint=self._checkpoint(entry['broker'])
        except BaseException as caught:error=caught;close=True
        if close:entry['broker'].close()
        with self.lock:
            self.validate(resource)
            result=self.store.release_session(claim,checkpoint,
                uncertain=resource['requiresReconcile'] or error is not None,attached=not close)
            entry['claim']=None
            resource['status']='closed';resource['latest']=None
            resource['requiresReconcile']=result['requiresReconcile']
            if close:self.entries.pop(pair,None)
            if error is not None:raise error

    def close_idle(self,resource):
        claim=resource['claim'];pair=(claim['projectId'],claim['key'])
        with self.lock:
            entry=self.entries.get(pair)
            if entry is None or entry['claim'] is not None or entry['generation']!=claim['generation']:
                return
            if entry.get('retiring'):return
            entry['retiring']=True
        # Claim attempts see retiring and remain busy until the actual driver is
        # closed. Never close a later generation acquired by another context.
        self._retire_idle(pair,entry)

    def close(self,deadline=None):
        with self.lock:
            self.closed=True
            for key,entry in list(self.entries.items()):
                if deadline is not None and time.monotonic()>=deadline:
                    raise TimeoutError('Computer drivers are still closing; storage remains open')
                if entry['claim'] is None:self._retire_idle(key,entry,deadline)
                else:
                    # Only reached after all Engine runs have drained. A claim
                    # retained after cleanup failure must remain uncertain.
                    self._close_driver(entry,deadline)
                    self.store.release_session(entry['claim'],None,uncertain=True)
                    self.entries.pop(key,None)
            if deadline is not None and time.monotonic()>deadline:
                raise TimeoutError('Computer cleanup exceeded close deadline; call close again')
