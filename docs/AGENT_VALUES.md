# 智能体定义值与角色重构

0.20 为现有 Agent 增加显式定义导入、检查和源码导出。智能体仍是一元可调用值，满足 `Function` 契约；没有另建一个调度器或智能体库。函数工厂早已可以动态声明 Agent，本版本让其公开配置也能安全地作为普通数据传递和重构。

## 显式定义与函数值

`agent_from(definition)` 接受严格的 `{kind: "agent", name: String, config: Record}`，返回属于当前解释器的新 Agent。`agent_info(agent)` 返回定义副本；普通 JSON 不会自动恢复为可执行值。导入和检查本身不调用模型或工具。

```chaos example=agent-definition-value
let worker=agent_from({kind:"agent",name:"worker",config:{provider:"model",prompt:"检查输入",tools:[]}});
[type_of(worker),agent_info(worker).name,agent_info(worker).config.prompt]
```

结果为 `["Function","worker","检查输入"]`。函数、异步任务、生成器、宏生成的调用、Plan 的显式绑定均使用原有统一调用入口。装饰器可以把 Agent 包装为普通函数；若需检查原定义，应保留原 Agent，不能从任意包装函数反推其内部智能体。

## 用普通函数组合角色

使用已有 `merge` 构造新定义即可；旧值不受影响。返回的定义及其嵌套数据与原 Agent 隔离，不包含闭包、解释器 owner 或权限。

```chaos example=agent-definition-derive
agent original {provider:"model",prompt:"初次检查",tools:[]}
let previous=agent_info(original);
let reviewer=agent_from(merge(previous,{name:"reviewer",config:merge(previous.config,{prompt:"复核证据"})}));
[agent_info(original).config.prompt,agent_info(reviewer).config.prompt]
```

下面的工厂仍是普通语言函数。配置表达式按词法作用域求值，无需提示词关键词识别。

```chaos example=agent-definition-factory
fn make_role(prompt) { agent role {provider:"model",prompt:prompt,tools:[]}; role }
let first=make_role("规划");
let second=make_role("执行");
[agent_info(first).config.prompt,agent_info(second).config.prompt]
```

## 公开配置与执行权限

所有配置字段都可省略，定义往返不注入默认值：

| 字段 | 类型与约束 |
| --- | --- |
| `provider`, `mode`, `role`, `route` | 非空 String，至多 128 字符 |
| `prompt`, `policy` | String，至多 100000 字符 |
| `tools` | 至多 128 个非空工具名称，每个至多 128 字符 |
| `maxSteps` | Int，1 至 12；Bool 不接受 |
| `level` | `worker`、`coordinator` 或 `platform` |

字段长度上限之外，定义还受总体 JSON 节点／文本预算限制；源码导出受 Parser 的 100000 字符整份源码限制（包含转义和模块包装），过大的定义会明确报资源错误，不截断内容。

名称采用语言已有 Unicode 标识符规则，至多 128 字符，拒绝保留字。未知字段、密钥、连接地址、私有 harness 控制和 `backend` 不属于公开定义。旧声明及受信任 Python Agent 回调接口保留原行为；含非公开配置的旧值仍可按原规则调用，但不能通过这些新接口检查或导出为公开定义。

`level` 是角色／路由提示，不授予平台图修改能力。每次调用仍验证当前 provider 注册、项目能力、工具授权、预算和取消状态。模型连接由本系统 Settings 解析；省略 `maxSteps` 的模型 Agent 继承 Settings，普通注册 provider 使用既有默认值 4。定义哈希不固定模型、密钥、Settings 版本或外部服务行为；实际路由另有 `route.selected` 事件。

## 模块、计划和目标检查点

`agent_source(value, name="saved_agent")` 导出由真实 Parser 验证的普通模块：`let saved_agent=agent_from(...); export {saved_agent};`。绑定名独立验证，拒绝注入与 `agent_from` 自遮蔽。导入模块会显式重建新 Agent，不携带旧解释器执行权限。

```chaos
agent architect {provider:"model",prompt:"只返回严格 JSON 的 kind/name/config 智能体定义",tools:[]}
let old=agent_from(input.definition);
let candidate=agent_from(parse_json(architect({current:agent_info(old),objective:input.objective})));
let pipeline=plan([{id:"answer",call:"worker"}]);
await pipeline(input.value,{worker:candidate})
```

此例需要真实项目运行环境及已配置模型。native 模型的最终回复是 String，因此显式 `parse_json` 后再验证定义；JSON 解析或定义验证错误按语言异常传播，不从自然语言猜测结构。注册 provider 若返回 `Final(record)`，可直接传给 `agent_from`。Plan 步骤仍显式绑定 callable，传入 Agent 的是 Plan 步骤上下文记录。候选执行和验收应由普通语言代码明确表达，执行过的外部副作用不会因验收失败撤销。

Goal 的 JSON 状态可保存 `agent_info(candidate)`；下一轮或恢复后由 planner 使用 `agent_from(state.definition)` 重建角色。这持久化的是定义数据，不是闭包、正在等待的任务或执行栈。新 Agent 可继续交给 `record_skill` 中的函数调用，记录的是实际工具回执；定义本身不是录制技能。任意模块源码的检查、修改、纯测试与版本采用已经由 Program 提供，见[自编程](SELF_PROGRAMMING.md)；当前运行栈不热替换。

## 可视化与无界面一致性

结构编辑器在已有 `agent` 声明节点上提供配置表单，字段元数据来自同一公开 schema。可选字段须显式添加；缺省不写入源码。原有未知字段保留在 AST，计算表达式保留为表达式，不会因打开表单而求值或变成固定文本。`agent_from(...)` 是可遮蔽的普通函数调用，编辑器不凭名称把它改造成特殊语法。

静态公开定义可通过同一纯验证接口预览与导出，计算配置则在语言中显式使用 `agent_source`。导入源码只创建 `agents/` 下的新本地模块草稿；保存使用现有项目 CAS。后台 SDK 更新、SSE 事件与未提交输入之间沿用草稿保留和冲突提示，不悄悄覆盖本地修改。

## SDK 与 HTTP

```python
from chaos import Framework
with Framework() as app:
    checked=app.validate_agent({"kind":"agent","name":"reviewer",
        "config":{"provider":"model","prompt":"检查证据","tools":[]}},
        name="saved_agent")
    assert Framework.evaluate('import "role.chaos" as R;agent_info(R.saved_agent)',
        files={"role.chaos":checked["source"]}) == checked["definition"]
```

`Client.validate_agent` 与 `Framework.validate_agent` 返回 `{definition, definitionHash, source}`；`agent_schema()` 返回配置对象的 JSON Schema，字段位于 `.properties`。HTTP 使用 `POST /api/v1/agents/validate`（请求 `{definition,name?}`）及 `GET /api/v1/agents/schema`，沿用宿主 100000 字符定义传输上限。这些操作不保存项目、不修改模式库、不执行 provider。

有效公开定义的实际 Agent 调用在 Context 发出的 `agent.observing`／`agent.completed` 事件中携带 `agentDefinitionHash`；事件不会因此新增配置或密钥。旧内部配置不适合公开验证时省略此字段，继续原执行路径。完整验证见 `tests/test_agent_values.py`、`test_agent_values_integration.py` 与 `test_agent_values_http.py`。

## 绑定语言工具

`agent_bind(value,tools)` 返回独立的 BoundAgent 可调用值，原 Agent 定义保持不变。工具包含活闭包，不能用 agent_info／agent_source 或 Session 配置序列化为可恢复定义；`agent_binding_info` 仅提供检查用合同，下一轮须用正常源码重建。详见[函数工具](FUNCTION_TOOLS.md)。
