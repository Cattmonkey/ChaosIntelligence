"""Small CLI adapters over ordinary language programs and the installed docs."""
import argparse
import json
import sys

ASK_SOURCE = 'import "std/intelligence" as AI; AI.ask(input.request, role=input.role)'


def print_help():
    print("""Chaos — functional agent programming language

  chaos run FILE.chaos       Run a language program (JSON result)
  chaos FILE.chaos           Shorthand for run
  chaos -c '40 + 2'          Evaluate inline code
  chaos run -               Read UTF-8 source from stdin
  chaos repl                Interactive language session (.ask for model help)
  chaos check FILE          Check syntax and module dependencies
  chaos fmt FILE            Print formatted source without overwriting
  chaos ask 'question'      Call the built-in language Agent
  chaos ask 'how?' --topic getting-started
  chaos assist --help       Use the same programming assistant as the IDE
  chaos project --help      Project IDE, settings and directory operations
  chaos serve --help        Start the web service (also legacy bare chaos)
  chaos docs list           List installed documentation topic IDs
  chaos docs show TOPIC     Read one topic offline in the terminal
  chaos --version           Print the installed version

run options: --input-json JSON --config FILE --stream --interactive
Advanced commands: compile, executable, artifact, ir, symbols, lsp, debug,
workspace, process, session, run-guide, computer, ui, task, service, package,
update, release. Use COMMAND --help for its options.

Model calls require configured credentials and may incur provider charges.
ask and Agent programs use the existing Settings / Harness / permissions.
REPL evaluates pure code; .ask explicitly uses a hosted model run for help.
Model/robot workflows run as hosted programs.
Directory packages: python chaos.py -c '40 + 2', run, ask or repl;
no arguments still opens the project IDE.
""")
    return 0


def ask_parser(prog='chaos ask'):
    parser = argparse.ArgumentParser(prog=prog, description='Call std/intelligence through the ordinary language runtime')
    parser.add_argument('question', help='Question text, or - to read text from stdin')
    parser.add_argument('--role', default='executor', choices=('planner','executor','reviewer','assistant'), help='Built-in role Agent (default executor)')
    parser.add_argument('--topic', help='Include an installed documentation topic (IDs: chaos docs list) as explicit context')
    parser.add_argument('--config', help='Private Settings file')
    parser.add_argument('--db', default=':memory:')
    parser.add_argument('--stream', action='store_true')
    return parser


def ask_main(argv):
    return ask(ask_parser().parse_args(argv))


def ask(args):
    """Execute parsed options through the same hosted language program."""
    try:
        question = sys.stdin.read(20001) if args.question == '-' else args.question
        if not question.strip() or len(question) > 20000:
            raise ValueError('Question requires 1–20000 characters')
        if not 1 <= len(args.role) <= 128:
            raise ValueError('Role requires 1–128 characters')
        request = question
        if args.topic:
            from .documentation import Documentation
            page = Documentation().source(args.topic)
            request = {'question': question, 'reference': {'topic': args.topic,
                'version': page['version'], 'markdown': page['markdown']}}
        command = ['run', '-c', ASK_SOURCE, '--input-json', json.dumps({'request':request,'role':args.role},ensure_ascii=False), '--db', args.db]
        if args.config: command += ['--config', args.config]
        if args.stream: command.append('--stream')
        from .cli import main
        return main(command)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'error':str(error)},ensure_ascii=False),file=sys.stderr)
        return 1
