"""Reuse the real terminal fixture and Registry; change only its CLI entry."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import _run_terminal_fixture230 as fixture
import chaos.executable_cli as executable_cli
from chaos import Framework
from test_executable231 import no_frontend

original_init=Framework.__init__
guards=[]
def initialize(self,*args,**kwargs):
    original_init(self,*args,**kwargs)
    guard=no_frontend();guard.__enter__();guards.append(guard)
Framework.__init__=initialize
# The existing fixture injects its real registered callbacks into cli.Framework.
def local_framework(*args,**kwargs):return fixture.cli.Framework(*args,**kwargs)
executable_cli.Framework=local_framework
try:
    raise SystemExit(fixture.main())
finally:
    Framework.__init__=original_init
    for guard in reversed(guards):guard.__exit__(None,None,None)
