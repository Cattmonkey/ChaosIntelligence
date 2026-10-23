# 群体非线性函数协议

Chaos 0.67 的 `std/swarm` 是用本语言写成的标准模块。它使用原来的函数、AST、冷 Task、TaskGroup 和异步生成器；没有第二套解释器或宿主 Swarm 句柄。群体是便携状态与可调用节点的组合，节点可以是函数、Agent、绑定智能体或真正的 Team 角色函数。算法、反馈与衡量函数都由用户编程，不通过名称、提示关键词或固定文本判断。

## 接口与快照

| 导出 | 合同 |
| --- | --- |
| `create(states:Record, edges:List=[]) -> Record` | 校验并复制初始状态，tick 为 0 |
| `async step(snapshot:Record, workers:Record, *, prepare=identity, rewire=null) -> Record` | 成功屏障后返回下一轮快照 |
| `async fn* frames(initial:Record, workers:Record, rounds:Int, *, prepare=identity, rewire=null, measure=no_measure, stop=never_stop)` | 每轮返回 `{snapshot,metrics}`，不是初始状态 |
| `async run(initial:Record, workers:Record, rounds:Int, *, prepare=identity, rewire=null, measure=no_measure, stop=never_stop) -> Record` | 消费 frames，仅保留最后一帧 |

快照严格包含 `{states,edges,tick}`。states 有 1..64 个节点，ID 是 1..128 字符的非空 String；tick 是非负 Int。每条边严格包含 `{source,target,weight}`，端点必须存在，weight 是有限 Number，最多 4096 条且不能重复 source-target 对。自环、循环、零和负权重都可表达。节点与边的输入顺序保留；结果按节点顺序提交，不依赖完成先后。

states、每个 worker 的最终返回值、prepare 返回值、measure 返回值都必须递归由 Null、Bool、有限 Int／Number、String、List、Record 组成。活函数、对象、任务、生成器、Tuple、Set、UIArtifact 等不能藏在其中；检查发生在 JSON 复制之前，不把活句柄转换为描述标签。普通数据字段 `kind` 或 `$handle` 没有特殊含义。仍受原来的字符、深度、节点与总计算预算约束。

workers 的键必须恰好匹配节点。每个值通过现有可调用／所有权协议校验。prepare 收到 `{id,state,incoming,tick}`；incoming 的每项是 `{edge,state}`，其中 state 是该边来源节点本轮开始前的状态。prepare 可以适配任何纯数据输入给 worker，改变输入中的 id 不会改变被选择的节点或权限。

## 一轮执行与生命周期

step 先完整校验快照、workers 和回调，再完成所有 prepare，之后才启动节点。每个节点由 TaskGroup 内的普通异步包装函数调用实际 worker。同步结果直接检查；若 worker 返回 Task，第一次接纳必须是当前作用域合法的冷 Task，返回它表示把生命周期交给这一轮的 TaskGroup。同一组内再次返回相同 Task 可共享结果，实际执行一次。外部已启动／完成的 Task、其他组的 Task 不可移交，不会因拒绝而取消无关任务。

worker Task 只 await 一层。其结果仍是 Task、Generator，或含有活值时会失败，不自动启动下一层。普通异常使整轮失败；所有已经接纳的任务按原任务组协议取消并等待实际效果结束。失败 worker 自身的普通 finally、资源退出和控制边界遵循[异步任务](EFFECTS.md)与[任务组](TASKS.md)：TaskCancelled、运行级取消及预算控制不承诺执行普通 finally，不绕过原控制语义。外部工具、文件、网络等副作用不能回滚；提交快照的原子性不等于副作用事务。

所有 worker 成功后，tick 增加 1，才调用同步 `rewire(oldSnapshot,nextSnapshot)`。rewire 必须返回合法 edges；节点集合、workers 和 Team 权限树不会改变。新边只影响后续轮次。失败的反馈不会发布这一轮的快照。

prepare、rewire、measure、stop 都是实际可调用值，可能产生效果。所谓同步是返回合同：不隐式等待它们返回的冷任务或生成器；这不是纯函数证明。推荐把它们写成无副作用计算，以便复现实验。快照、输入、回调实参和 yield 帧均进行深数据复制，受信任 Native 扩展修改某次传入的数据不能污染历史或未来轮次；外部捕获状态和宿主全局副作用仍由调用者负责。

frames 每轮用独立 TaskGroup 明确拥有 step Task，关闭之后才调用 measure、stop 并 yield。run 为每个 anext 操作建立并关闭任务组，使用语言内 async closing 处理正常或普通异常结束；TaskCancelled 通过被接纳的 anext 操作中止生成器和其轮次组，保持原控制边界，不宣称此时执行普通 __aexit__。measure 默认 null；stop 必须返回严格 Bool，true 时仍产出本轮帧，随后结束。rounds 范围 0..10000；0 仍校验快照、workers 和所有回调，但不调用回调，run 返回 `{snapshot:initialCopy,metrics:null}`。关闭停在 yield 处的流不启动下一轮。取消正在执行的 step／frames／run 会清理其实际工作任务，即使调用者取消后继续运行，不依赖整个程序最终退出才清理。

## 非线性计算与敏感性实验

```chaos example=swarm-logistic
import "std/swarm" as Swarm;
fn logistic(context) { 3.9 * context.state * (1 - context.state) }
fn distance(snapshot) { abs(snapshot.states.base - snapshot.states.perturbed) }
let initial = Swarm.create({base:0.2, perturbed:0.200001});
let last = await Swarm.run(initial, {base:logistic, perturbed:logistic}, 6, measure=distance);
[last.snapshot.tick, floor(last.metrics * 1000000), initial.tick]
```

这是固定参数下的 logistic map 数值实验；测试与独立数值计算比较。非线性和微扰度量不保证出现智能、收敛或普遍混沌。

```chaos example=swarm-feedback
import "std/swarm" as Swarm;
fn prepare(context) {
  context.state + sum(map(fn(link) { link.edge.weight * link.state }, context.incoming))
}
fn nonlinear(value) { value * value }
fn feedback(old, next) { map(fn(edge) { set(edge, "weight", -0.25) }, old.edges) }
let initial = Swarm.create({a:0.5, b:0.25}, [
  {source:"a", target:"b", weight:-0.5}, {source:"b", target:"a", weight:-0.5}
]);
let last = await Swarm.run(initial, {a:nonlinear, b:nonlinear}, 2,
  prepare=prepare, rewire=feedback, measure=fn(snapshot) { snapshot.states });
[last.snapshot.tick, last.snapshot.states, last.snapshot.edges[0].weight, initial.tick]
```

循环通信／抑制拓扑与 Team 的权限父树是两种关系。通信图可以循环，权限树仍遵循原来的无环约束。weights 是传给用户函数的数据，标准模块不会暗中选择某种耦合公式。

## 智能体与 Team 角色

```chaos example=swarm-team
import "std/swarm" as Swarm;
let policy = team_from({kind:"team", name:"research", entry:"planner", roles:[
  {id:"planner", parent:null, capabilities:[], agent:{kind:"agent", name:"planner", config:{provider:"local", tools:[]}}},
  {id:"reviewer", parent:"planner", capabilities:[], agent:{kind:"agent", name:"reviewer", config:{provider:"local", tools:[]}}}
]});
let crew = team_bind(policy, {
  planner:fn(context) { context.state + 1 },
  reviewer:fn(context) { context.state + context.incoming[0].state }
});
let initial = Swarm.create({planner:1, reviewer:2}, [
  {source:"planner", target:"reviewer", weight:1}, {source:"reviewer", target:"planner", weight:-1}
]);
let result = await Swarm.step(initial, team_bindings(crew));
[result.states, result.tick, team_binding_info(crew).team.roles[1].parent]
```

实际 Agent／BoundAgent 可替换绑定函数，继续使用系统 Settings 的 provider、baseURL、key 与模型路由。Team 角色仍取调用者、角色与实现能力的交集；群体边不会授予工具权限。返回宽权限借用 Task 的受限角色仍被原运行时拒绝。状态和 incoming 会有意交给其他节点，frames 包含整个 states，它们不是私密存储；私有数据保留在角色闭包或 Session，并显式决定什么进入快照。

## 报告与自动演示

```chaos example=swarm-report
import "std/swarm" as Swarm;
var rows = [];
async for frame in Swarm.frames(Swarm.create({a:0}), {a:fn(c) { c.state+1 }}, 3,
  measure=fn(snapshot) { snapshot.states.a }) {
  rows = append(rows, [frame.snapshot.tick, frame.metrics]);
};
let table = ui_node("Table", [["轮次", "状态"], rows]);
let chart = ui_node("BarChart", ["已提交状态", ["1", "2", "3"], map(fn(row) { row[1] }, rows)]);
let report = ui_artifact("report", "群体迭代报告", ui_node("Stack", [[table, chart]]));
let slides = ui_artifact("slides", "群体演示", ui_node("Slides", [[
  ui_node("Slide", ["迭代证据", [table]]), ui_node("Slide", ["状态图", [chart]])
]]));
[ui_info(report).kind, ui_info(slides).kind, rows]
```

帧是普通数据，可直接构造已有 [OpenUI 报告／演示](GENERATIVE_UI.md)。显示图表、自动切页、导出与结构化需求验证继续使用原 UIArtifact 通道。当前没有新增流式群体后端或无限历史存储；保留哪些帧由程序决定。IDE 的导入提示、格式化、高亮、Program IR、图形结构编辑和无界面 API 使用标准模块的同一源码与 AST。

## 安装、修改与范围

`chaos/stdlib/swarm.chaos` 随 wheel／源码包发布，安装后无需仓库或网络下载；发布校验检查资源字节一致。官方 `std/swarm` 路径不能被项目文件覆盖。可复制其源码到自己的封闭项目模块中，通过普通 import／export 改造算法或协议。

1..64 节点和 4096 边是 schema 上限，不是默认预算内必能完成所有图的承诺。所有校验、回调、复制和迭代都计入原 AST 步数／时间／任务资源预算；复杂状态、巨大图或很多轮需在宿主明确配置更合适的预算。群体标准模块补充了语言内组合能力，不意味着已经完成全部 Python／Lisp 能力、GPU 通用编译或解释器自举。当前全局缺口见[能力对照](PYTHON_LISP_CAPABILITIES.md)。
