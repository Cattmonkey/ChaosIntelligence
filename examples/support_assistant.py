"""A secondary application built exclusively on the public Chaos SDK.

Run from the checkout: python3 -m examples.support_assistant
Or, after installing chaos-intelligence, run this file from any directory.
"""
import json
from chaos import Framework, Registry, ToolCall, Final, Workflow


def build_registry():
    registry = Registry()
    orders = {'A100': {'id': 'A100', 'state': '已发货', 'tracking': 'DEMO-001'}}

    @registry.tool('orders.lookup', input_type='String', output_type='Record', effect='read', description='查询应用内订单目录')
    def lookup(order_id, context):
        context.checkpoint()
        return orders.get(order_id, {'id': order_id, 'state': '未找到'})

    @registry.provider('support.researcher', description='示例中的确定性查单策略，可替换为模型适配器')
    def researcher(request, context):
        if request.step == 1:
            return ToolCall('orders.lookup', request.input['orderId'])
        return Final(request.history[-1]['output'])

    @registry.provider('support.writer', description='示例中的确定性回复策略')
    def writer(request, context):
        context.checkpoint()
        order = request.input
        return Final({'message': f"订单 {order['id']}：{order['state']}。", 'order': order})

    return registry


def build_workflow():
    return (Workflow().input()
            .agent('research', '查询订单状态', provider='support.researcher', tools=['orders.lookup'], level='coordinator')
            .agent('reply', '根据已核实的订单状态生成答复', provider='support.writer', parent='research')
            .output().connect('input','research').connect('research','reply').connect('reply','output'))


def main():
    with Framework(registry=build_registry()) as application:
        project = application.create_project('support', graph=build_workflow(), capabilities=['orders.lookup'])
        handle = application.start(project['id'], project['version'], {'orderId': 'A100'})
        output = handle.result()
        assert output['order']['state'] == '已发货'
        assert output['message'] == '订单 A100：已发货。'
        print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
