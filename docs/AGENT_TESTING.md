# 智能体协议测试

从 0.44 起，Program 验收与 IDE 的 `draft.evaluate` 可以接收显式 `fixtures`。它们提供模型协议响应和宿主工具回执，语言函数、闭包、控制流与异步任务仍由同一个解释器实际执行。无需配置或调用真实模型。

这用于验证智能体驱动的程序行为，例如模型要求调用 `twice` 后，该函数是否确实返回 42、是否更新闭包状态、是否正确释放资源。它不评估模型本身的推理质量，也不证明任意程序正确或任意优化有效。

## 同一程序与明确响应

候选程序可以写成：

```chaos
var seen=[];
fn twice(x:Int)->Int {
  seen=append(seen,x);
  x*2
}
agent worker {provider:"model",tools:[]}
let bound=agent_bind(worker,[
  agent_tool("twice",twice,input_type="Int",output_type="Int")
]);
[await bound(input),seen]
```

给它传入以下测试。`definition` 必须是实际 Agent 的完整公开定义；`input` 必须匹配实际调用输入。系统不从提示词、自然语言回答或代码块中猜测响应。

```json
[
  {
    "input": 21,
    "expect": ["42", [21]],
    "fixtures": {
      "agents": [
        {
          "definition": {
            "kind": "agent",
            "name": "worker",
            "config": {"provider": "model", "tools": []}
          },
          "input": 21,
          "actions": [
            {"tool": "twice", "input": 21, "expect": 42},
            {"final": "42"}
          ]
        }
      ]
    }
  }
]
```

第一项 action 要求真实 `twice` 函数执行并返回 42；第二项才提供模型最终结果。原生文本模型通常返回 String，所以此例最终值为 `"42"`，工具自身仍返回 Int 42；fixtures 不会替生产提供方自动转换返回类型。把函数改成 `x*3` 后，工具输出检查失败，即使最终响应仍写着 42，也不能通过验收。`seen` 同样来自实际词法环境。

## 用例、边界与失败

用例为 `{input,expect,entry?,fixtures?}`，一次 Program 验收包含 1–16 项。省略 fixtures 保持既有纯求值行为；提供它时，所有外部响应都必须在测试数据中明确声明。`entry` 可以指向候选包中的测试模块。

`fixtures.agents` 和 `fixtures.tools` 均为可选数组，每个最多 64 项。每项可指定 `times`，默认为 1，范围 1–64；两类数组分别最多展开为 256 次调用。一个智能体脚本包含 1–12 个 action，只能有一个 `final`，且必须放在最后；动作数同时受 Agent 的 `maxSteps` 限制。未使用的调用、额外调用、实际工具结果不匹配都使测试失败；语言中的 `catch` 不能把协议违规变成通过。

同一完整 Agent 定义与输入只能声明一个脚本；`times` 表示重复同一个脚本。同一工具名称与输入也只能声明一个回执。匹配使用严格 JSON 值身份，Bool、整数和浮点数不互相冒充。并发调用使用各自的调用状态；需要不同响应时使用不同的明确输入。

宿主工具回执写为：

```json
{"tools":[{"name":"text.normalize","input":" A ","output":"a","times":1}]}
```

这里返回指定 JSON，不执行真实宿主工具；已注册工具的输入和输出类型合同仍会校验。项目权限、Agent 的 tools 和语言工具的 capabilities 仍共同限制调用；测试声明不授予生产权限。已知但未调用的 Agent 工具无需配置回执。持久 Program、Session、Computer 等外部生命周期操作不会因为存在 fixtures 就自动变成可用的真实效果。

每个用例隔离解释器状态，但仍继承验收的总预算和取消。测试数据有 JSON 大小、结构及计数上限。未声明的效果和权限拒绝会保留失败证据；清理和任务收束结束后才决定是否通过。

## 自编程与无界面调用

`program_probe(candidate,cases)` 与 `program_adopt(base,candidate,cases)` 接受相同用例。候选源码、输入、期望值及 fixtures 都参与对应哈希；采用时重新执行用例，并检查原项目版本和定义哈希。过去的通过报告不能替代本次执行，当前调用栈也不会被热替换。

下例只构造冷测试任务，不发起模型或宿主调用：

```chaos example=agent-test-cold
let candidate=program_from({
  kind:"program",entry:"main.chaos",
  files:{"main.chaos":"input+1"},
  languageVersions:{"main.chaos":2}
});
type_of(program_probe(candidate,[{input:41,expect:42,fixtures:{}}]))
```

Python `Framework.adopt_program`、远程 `Client.adopt_program` 和 HTTP `program-adopt` 的 `cases` 原样支持 fixtures。IDE 候选窗口使用同一格式，显示测试 JSON 和源码差异；系统编程助手可调用 `draft.evaluate(input=...,fixtures=...)` 检查草稿。该助手自己的规划请求仍使用配置的模型；候选测试不访问生产模型客户端。

源码高亮、参数提示和格式化继续来自实际语言解析器及内置签名。JSON 测试数据使用 JSON 高亮和格式化，不会将字符串中的关键字误当成 Chaos 语法。

相关指南：[语言函数工具](FUNCTION_TOOLS.md)、[自编程](SELF_PROGRAMMING.md)、[IDE](IDE.md)、[开发者文档](DEVELOPER.md)。
