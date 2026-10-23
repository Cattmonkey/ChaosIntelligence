"""Real CLI entry with finite registered callbacks and filesystem rendezvous."""
import json,os,socket,sqlite3,sys,threading,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chaos import Final,Framework,Registry,ToolSpec
import chaos.cli as cli

def main():
    work=Path(sys.argv[1]);sys.argv=['chaos']+sys.argv[2:]
    calls=[];providers=[];apps=[];started=[];joined=[]
    counters={'constructed':0,'saved':0,'started':0};registry=Registry()
    def gate(context,kind):
        (work/'entered.json').write_text(json.dumps({'kind':kind}))
        deadline=time.monotonic()+20
        try:
            while not(work/'release').exists():
                context.checkpoint()
                if time.monotonic()>=deadline:raise AssertionError('Fixture gate was never released')
                time.sleep(.01)
            context.checkpoint()
        finally:(work/'gate-exited').write_text(kind)
    def block(value,context):gate(context,'tool');return value
    def effect(value,context):context.checkpoint();calls.append(value);return value
    registry.register_tool(ToolSpec('fixture.block',block))
    registry.register_tool(ToolSpec('fixture.effect',effect))
    @registry.provider('fixture.planner')
    def planner(request,context):
        providers.append(request.input);gate(context,'provider');return Final('old proposal')
    class LocalFramework(Framework):
        def __init__(self,*args,**kwargs):
            counters['constructed']+=1;assert kwargs.get('registry')is None
            kwargs['registry']=registry;super().__init__(*args,**kwargs);apps.append(self)
        def save_project(self,*args,**kwargs):counters['saved']+=1;return super().save_project(*args,**kwargs)
        def start(self,*args,**kwargs):counters['started']+=1;return super().start(*args,**kwargs)
    def denied(*args,**kwargs):raise AssertionError('Offline terminal fixture forbids network')
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=denied
    original_start=threading.Thread.start;original_join=threading.Thread.join
    def start(thread,*args,**kwargs):
        module=getattr(getattr(thread,'_target',None),'__module__','')
        result=original_start(thread,*args,**kwargs)
        if module=='chaos.process_console':started.append(thread)
        return result
    def join(thread,*args,**kwargs):
        result=original_join(thread,*args,**kwargs);joined.append(thread.ident);return result
    cli.Framework=LocalFramework;threading.Thread.start=start;threading.Thread.join=join
    try:return cli.main()
    finally:
        threading.Thread.start=original_start;threading.Thread.join=original_join
        report={'counters':counters,'effects':calls,'providerCalls':providers,
                'consoleWorkers':[{'ident':t.ident,'joined':t.ident in joined,'alive':t.is_alive()}for t in started],
                'appsClosed':all(a._closed for a in apps),'activeRuns':sum(len(a._engine.active)for a in apps),
                'activeOperations':sum(len(a._engine.operations)for a in apps),'gateExited':(work/'gate-exited').exists(),
                'runs':[],'events':[],'networkAllowed':False}
        database=work/'runtime.sqlite'
        if database.exists():
            db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            try:
                report['runs']=[{**dict(row),'output':json.loads(row['output'])if row['output']is not None else None}for row in db.execute('SELECT id,status,output FROM runs ORDER BY created')]
                report['events']=[{**dict(row),'payload':json.loads(row['payload'])}for row in db.execute('SELECT * FROM events WHERE run_id IS NOT NULL ORDER BY id')]
            finally:db.close()
        (work/'audit.json').write_text(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':raise SystemExit(main())
