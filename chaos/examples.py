DEFAULT_SOURCE = '''// Chaos / 函数是一等值，智能体是显式效果函数
fn factorial(n: Int) -> Int {
  if n <= 1 { 1 } else { n * factorial(n - 1) }
}
fn twice(f: Function) -> Function { fn(x) { f(f(x)) } }
let inc = fn(x: Int) -> Int { x + 1 };
let values = map(factorial, range(1, 7));
let clean = input |> trim;
agent analyst {
  prompt: "分析输入文本，提取结构与可执行建议。",
  mode: "analyze",
  provider: "local",
  tools: ["text.stats"],
  maxSteps: 4
};
{
  factorials: values,
  composition: twice(inc)(40),
  total: fold(fn(a, b) { a + b }, 0, values),
  analysis: analyst(clean)
}
'''


def initial_graph():
    def node(id, kind, label, x, y, config=None, parent='platform'):
        return {'id': id, 'kind': kind, 'label': label, 'x': x, 'y': y, 'parentId': parent, 'config': config or {}}
    def edge(src, dst, port='input'):
        return {'id': f'{src}_{dst}', 'source': src, 'target': dst, 'sourcePort': 'value', 'targetPort': port}
    return {
        'nodes': [
            node('platform', 'agent', '架构调度智能体', 490, 30, {'level': 'platform', 'provider': 'local', 'policy': 'ensure-review', 'prompt': '在输出前补充质量复核节点，保证结果经过复核。', 'tools': ['graph.inspect', 'graph.patch']}, None),
            node('input', 'input', '任务输入', 50, 270),
            node('normalize', 'tool', '文本归一化', 305, 270, {'tool': 'text.normalize'}),
            node('analyst', 'agent', '研究分析智能体', 575, 210, {'level': 'coordinator', 'provider': 'local', 'mode': 'analyze', 'prompt': '分析任务，识别关键约束，形成行动建议。', 'tools': ['text.stats'], 'maxSteps': 4}),
            node('metrics', 'tool', '结构统计', 575, 420, {'tool': 'text.stats'}),
            node('synthesis', 'agent', '结果综合智能体', 850, 270, {'level': 'worker', 'provider': 'local', 'mode': 'summarize', 'prompt': '综合研究与统计，形成清晰的交付结果。', 'tools': [], 'maxSteps': 3}, 'analyst'),
            node('output', 'output', '结果输出', 1130, 270),
        ],
        'edges': [edge('input', 'normalize'), edge('normalize', 'analyst'), edge('normalize', 'metrics'), edge('analyst', 'synthesis', 'analysis'), edge('metrics', 'synthesis', 'metrics'), edge('synthesis', 'output')],
    }
