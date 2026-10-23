# 语言团队、角色与能力范围

0.21 将团队身份、成员函数和父子能力范围实现为语言语义。Team 是当前解释器拥有的受控值，既满足 `Team` 契约，也满足 `Function`。角色句柄满足 `Function`；调用 Team 或角色只创建冷 Task，等待时才调用已有 Agent 效果。普通函数、模块、宏、对象、Plan、Goal 和 `gather` 可以直接组合它们。

## 定义、成员与冷调用

```chaos example=team-definition
let t=team_from({kind:"team",name:"research",entry:"worker",roles:[
  {id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{provider:"model",tools:[]}}}
]});
[type_of(t),type_of(team_role(t,"worker")),team_info(t).entry]
```

结果 `["Team","Function","worker"]`。`team_from` 严格验证纯数据，不调用模型。Team 自身的一元调用 `t(input)` 等价于入口角色调用；`team_role(t,id)(input)` 调用指定成员，均须显式 `await` 或调度。成员收到调用者原样提供的 JSON 输入；通过 Plan 调用时，这个输入是 Plan 的 `{input,results}` 步骤上下文。

```chaos example=team-cold-call
let t=team_from({kind:"team",name:"cold",entry:"worker",roles:[
  {id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{provider:"model"}}}
]});
let members=team_bindings(t);
let pending=members.worker(42);
[type_of(members.worker),type_of(pending),type_of(partial(t))]
```

结果 `["Function","Task","Function"]`。未调度的冷任务不会在程序退出时偷偷运行。普通 Agent 继续保留原有直接调用协议；Team 角色明确采用冷任务协议，组合器可等待它返回的 Task。纯环境可创建与检查定义及冷任务，实际调用角色需要项目智能体宿主。

## 关系如何约束执行

定义严格采用 `{kind:"team",name,entry,roles}`，每个角色严格采用 `{id,parent,capabilities,agent}`：

- 团队名 1–200 字符；1–64 个角色；role id 使用既有模式标识符规则，1–128 字符。
- id 唯一，入口必须存在；parent 为现有 id 或 null；拒绝自指、缺失引用和循环，允许多个根。
- capabilities 为至多 128 个不重复的工具名称，每个 1–128 字符。子角色范围必须是父角色范围的子集；Agent 的 tools 必须是自己角色范围的子集。
- agent 是 [0.20 公开智能体定义](AGENT_VALUES.md)，拒绝私有 backend、连接凭据和未知字段。

这些都是构造时验证的真实约束。调用时 Context 还会将角色范围与本次运行的项目快照授权求交，再检查实际 Agent 请求、工具注册、预算、取消和模型设置。项目版本变更不会原地重写正在运行的快照；模型与插件仍按既有 Settings 热生效规则处理。定义中的范围不会授予项目没有的工具；`platform` 标签也不授予图修改权限。工具名称在调用时解析，因此定义可离线编写。

父角色的 capabilities 是允许子树使用的上限，可大于父 Agent 自己请求的 tools。父子关系不自动调用管理者，不根据角色名称猜测行为，也不自动形成任务数据依赖。Team 管成员与能力范围，Plan 管步骤依赖；两者可独立重构。当前范围粒度是工具名，例如 `browser.control`，不把“观察者”名称解释为浏览器只读权限。

## 不可变定义与角色重构

`team_info(value)` 返回独立的完整定义副本；`team_bindings(value)` 返回新的 id→角色函数 Record。普通数据和事件中的描述符不会自动复活为可执行值。新的 Team 不修改旧 Team 或先前取得的角色函数。

```chaos example=team-derive
let original=team_from({kind:"team",name:"original",entry:"worker",roles:[
  {id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{provider:"model",tools:[]}}}
]});
let revised=team_from(merge(team_info(original),{name:"revised"}));
[team_info(original).name,team_info(revised).name]
```

`team_source(value,name="saved_team")` 生成普通模块：`let saved_team=team_from(...);export {saved_team};`。源码经真实 Parser 验证，导出绑定名禁止注入和 `team_from` 自遮蔽。整体 JSON 与源码预算分别生效；过大定义报错，不截断。

保存使用已有项目源码模块和 CAS；没有另一个 Team 数据库。Goal 的 JSON 状态也可显式保存 `team_info(t)`，恢复后用 `team_from` 重建。不会保存 live closure、Task、浏览器会话或执行栈。模型若返回 JSON 文本，程序先 `parse_json`，再 `team_from`；不提取自然语言中的隐含团队定义。

## 计划、目标与电脑操作

下面使用已有 Plan 接口，角色仍是普通函数值：

```chaos
let members=team_bindings(team_from(input.team));
let flow=plan([
  {id:"before",call:"observe"},
  {id:"decision",call:"decide",needs:["before"]},
  {id:"action",call:"act",needs:["before","decision"]},
  {id:"after",call:"observe",needs:["action"]}
]);
let results=await flow(input.request,members);
results
```

角色提供方必须按该程序的输入合同产生数据。观察、动作和验证的含义由已授权工具及普通语言函数实现；结果字符串不会被当作完成证据。循环任务可让 Goal 的 planner 返回这个 Plan，并让 verify 读取 `after` 的真实页面状态，严格返回 `{done:Bool,state,evidence}`。同一 `observe` 角色可在不同步骤接收不同的依赖记录，角色名不触发特殊分支。

实际回归通过本地页面、真实 headless 浏览器、独立角色 Provider、snapshot/ref 身份校验及普通语言验收，执行观察→决定→动作→新观察的过程。测试中的提供方是确定性 fixture，不是外部模型准确率评测。没有执行模型生成的 Python/pyautogui 文本。

Plan 默认按原有顺序串行；0.39 可显式设置 `max_concurrency` 启用有界依赖调度。普通函数内部也可使用 `gather([member_a(x),member_b(y)])`。这复用已有 Scheduler、Context 与总预算，没有新的团队后台线程。角色任务继承录制 scope，`record_skill` 记录实际工具回执；并发工具录制仍遵守原有拒绝重叠动作的规则。取消不回滚已发生动作，也不强制终止不合作的受信任 Python 回调。

实际效果事件增加 `team:{name,definitionHash,roleId,rolePath}` 来源信息；不在来源字段放配置、密钥、输入或授权清单。语言角色生命周期事件以真实调用为依据。Team 定义哈希不固定模型设置、外部服务或页面状态；模型路由继续记录实际 Settings 版本。

## 图形编写与无界面使用

团队编写入口提供成员、父子关系、能力范围和 Agent 配置的结构表单与关系投影，使用同一 Team schema／验证器。生成结果是 `teams/` 中的普通模块草稿，显式保存后才进入项目版本；已有源码草稿及 SDK/SSE 冲突使用原有保护机制。

该入口编写明确的定义，不按 `team_from` 这个可遮蔽函数名猜测任意源代码的含义。动态表达式继续使用源码或通用语法树编辑；导入模块也不会偷偷重写原来的 Team 表达式。验证期间的新编辑会使旧预览失效，避免把过时结果加入项目。

```python
from chaos import Framework
with Framework() as app:
    definition={"kind":"team","name":"example","entry":"worker","roles":[
        {"id":"worker","parent":None,"capabilities":[],"agent":
            {"kind":"agent","name":"worker","config":{"provider":"model","tools":[]}}}]}
    checked=app.validate_team(definition)
    assert Framework.evaluate('import "team.chaos" as T;team_info(T.saved_team)',
        files={"team.chaos":checked["source"]}) == definition
```

Framework／Client 提供 `validate_team(definition,*,name="saved_team")` 和 `team_schema()`；验证返回 `{definition,definitionHash,source}`，schema 描述整个 Team 定义。HTTP 对应 `POST /api/v1/teams/validate`（`{definition,name?}`）及 `GET /api/v1/teams/schema`。宿主定义输入总文本上限 100000 字符，验证无运行效果、无持久写入。

## 与 OpenDots、Agent-S 的融合边界

语言语义独立实现，其上游设计参考固定于 [OpenDots 625452e](https://github.com/CopilotKit/OpenDots/tree/625452e06cde74cb25b0ce319e2c1be0488f5a5f) 与 [Agent-S 3aa272d](https://github.com/simular-ai/Agent-S/tree/3aa272d23d2994c7bbde1acbbe0ef8e8d06b8693)；这些固定版本记录设计依据，不代表当前上游功能评测。

OpenDots 的角色与工具控制启发这里的显式角色身份和能力范围；Agent-S 的新观察反馈启发可执行语言里的观察—行动—再观察—验收过程。[OpenDots 电脑服务](https://github.com/CopilotKit/OpenDots/blob/625452e06cde74cb25b0ce319e2c1be0488f5a5f/src/server/computer-service.ts)与 [Agent-S Worker](https://github.com/simular-ai/Agent-S/blob/3aa272d23d2994c7bbde1acbbe0ef8e8d06b8693/gui_agents/s3/agents/worker.py)是设计参考，没有将上游运行时或提示模板复制进解释器。

目标是使这些机制可组合、检查、导出、调试和无界面运行。“超越”应由具体性质和同等预算的任务评测证明，当前不宣称整体功能或成功率超过上游。[Computer／Observation／ComputerAction 及显式视觉输入](COMPUTERS.md)和[持久 AgentSession 对话](SESSIONS.md)已实现。Computer 当前后端是独立无头 Chromium；具名 Computer 已支持跨运行独占租约与已保存 cookies/localStorage 恢复；原生 OS 视觉定位、人工接管和自动技能检索仍未完成。普通 screenshot Record 不会自动成为 ObservedInput。Team 也不代表 Python／Lisp 全部生态兼容。

## 0.47：当前程序的角色实现

`team_bind` 显式为已有角色绑定真实 callable，原默认 Agent 定义不变。BoundTeam 角色转发实际参数与命名参数，保持对象结果；能力与任务来源贯穿挂起和清理。`team_role`、`team_bindings` 支持两类值，导出普通定义不丢弃活绑定。详见[函数角色团队](TEAM_FUNCTIONS.md)。
