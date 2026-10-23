# 原生生成式界面、报告与演示

Chaos 0.65 将 [thesysdev/openui](https://github.com/thesysdev/openui) 的组件语言、流式解析与 React Renderer 融入语言运行时。固定上游提交 `9c5c43e78b95b69a2220c9f0d4bc4919d29067c9`，MIT 许可；浏览器包 0.1.7、lang-core 0.4.1。构建包含本地依赖与来源记录，不要求 OpenUI Cloud、CDN、Codex App 或外部渲染服务。

## 一个产物，多种入口

`UIArtifact` 是当前求值器拥有的不可变值。原生函数、SDK、HTTP、CLI 与浏览器共享组件目录、规范化 OpenUI 源码、组件树和 SHA256；序列化表示为 `{"$ui": definition}`。定义固定字段为 `schema/kind/title/source/root/libraryHash/hash`，schema 为 `chaos.ui/1`。计算、权限、异步与工具调用仍使用 Chaos 的函数和 Task。

```chaos example=ui-native-page
let view = ui_artifact("page", "你好", ui_node("Stack", [
  [ui_node("Heading", ["Hello", 1]), ui_node("Text", ["由原生函数构建"])]
]));
[ui_info(view).kind, ui_validate(view, [{component:"Text", minimum:1}]).passed]
```

14 个组件：Stack、Heading、Text、Card、Metric、Table、BarChart、Tabs、Tab、Disclosure、Code、ActionButton、Slides、Slide。参数顺序和默认值由 `ui_prompt()` 和 `/api/v1/ui/schema` 的目录定义。字符串是纯文本，Code 使用已有语法高亮。

## OpenUI 声明与语言边界

声明支持换行赋值、组件调用、字符串、布尔值、有限数值、null、数组、记录及向前引用。最后的 `root` 指定根组件。公开目录是严格的组件子集，并未实现整个 OpenUI v0.5 解释器：`$state`、Query、Mutation、`@builtins`、算术表达式及任意脚本不在此声明层运行。计算使用 Chaos，效果由原智能体／AgentTool 执行。声明使用换行，不支持分号；重复赋值、未知组件、循环引用、未闭合最终源码会报错。

```chaos example=ui-openui-roundtrip
let view = ui_from_openui("root = Text(\"Hello\")", title="Demo");
let restored = ui_from_openui(ui_source(view), title="Demo");
[ui_info(view).hash == ui_info(restored).hash, ui_definition(restored).root.props.text]
```

`ui_node` 构造树；`ui_artifact`、`ui_from_openui` 创建值；`ui_info`、`ui_definition`、`ui_source`、`ui_program` 返回脱离句柄的数据／源码。跨求值器传递定义并重新编译，而不是传递活句柄。

## 流式智能体生成

```chaos example=ui-cold-agent
agent designer { provider:"model", prompt:"生成简洁且符合目录的报告", tools:[] };
type_of(ui_generate(designer, {request:"季度总结"}, kind="report", title="季度报告"))
```

`ui_generate` 返回冷 Task；`await ui_generate(...)` 才通过现有 Settings 的 baseURL、key、role／route 和模型候选发起 OpenAI 包的真实 `stream=True` 请求。`previous=view` 表示迭代完整替换。插件开关和调用者权限继续生效。已输出内容后不会跨模型拼接；取消关闭当前流与客户端，并等待读取工作结束。

`ui.delta` 是累计源码草稿，可以流式预览；只有完整响应通过严格解析并成功结束运行，才形成最终 UIArtifact。预览有节流和累计预算，达到预算后仍校验最终文本。失败或取消的草稿不能代替之前完成的产物。语言级生成与宿主 SDK 流式通道均有测试；回归测试使用模拟传输，不产生付费请求。

## 报告、演示与交互

```chaos example=ui-report-check
let report = ui_artifact("report", "季度", ui_node("Table", [
  ["项目", "数量"], [["完成", 12], ["进行中", 3]]
]));
ui_validate(report, [{component:"Table", minimum:1}]).passed
```

报告支持表格排序、图表、折叠与标签页。演示使用 `kind="slides"` 和 Slides 根组件，子组件必须为 Slide，带上一个／下一个、播放／暂停及间隔设置。自动演示是本地定时切页。`ui_export(view,"html")` 生成离线 HTML，可浏览和打印；JSON、OpenUI 和 `.chaos` 源码也可导出。目前不生成原生 PDF／PPTX 文件。

ActionButton 声明名称和固定 payload；`ui_dispatch(view,event,handlers)` 返回冷 Task，handlers 必须是原求值器的 AgentTool，不接受任意宿主函数、未声明动作或不同 payload。示例：

```chaos example=ui-native-action
let view = ui_artifact("page", "动作", ui_node("ActionButton", ["刷新", "refresh", {n:1}]));
let handlers = {refresh:agent_tool("refresh", fn(x){set(x,"done",true)}, input_type="Record", output_type="Record")};
await ui_dispatch(view, {name:"refresh", payload:{n:1}}, handlers)
```

在线按钮显式用 `{event:{name,payload}}` 输入重新运行产生此产物的原版本程序。程序需要处理该输入并调用 ui_dispatch；原执行闭包不会跨运行保存。仅当前项目相同版本且无未保存编辑时允许按钮运行，历史产物不会调用更新后的代码。离线 HTML 的按钮只发出 `ui.action` CustomEvent，不复活后端处理器。

## 需求验证

需求是结构化数据，不通过固定自然语言关键词判断。`{component:"Table",minimum:1}` 验证组件数量；`{path:"/props/text",equals:"Hello"}` 使用相对于根组件的 RFC6901 JSON Pointer 检查精确值。返回 `{passed,checks,artifactHash}`，每项 checks 包含 index、passed、actual。这是可执行的结构需求验证，不能证明自然语言内容正确或科研结论真实。

## IDE、SDK、HTTP 与 CLI

运行结果的“页面 / 报告”标签提供最终预览、生成草稿、OpenUI 源码编辑、需求验证及四种导出。源码编辑调用同一后端编译器；导入 `.chaos` 形成 IDE 本地草稿，按已有版本冲突检测保存。原始结果与历史仍可查看。

Python 使用 `Framework.ui_schema/ui_compile/ui_validate/ui_export` 或 `Client` 的同名方法。HTTP 使用 `GET /api/v1/ui/schema`，`POST /api/v1/ui/compile`（source/kind/title）、`/ui/validate`（definition/requirements）、`/ui/export`（definition/format）。字段和哈希均严格验证；export 返回 format/content/hash。

```bash
python -m chaos ui schema
python -m chaos ui compile view.openui --title Demo
python -m chaos ui validate view.openui --requirements requirements.json
python -m chaos ui export view.openui --format html --output report.html
python -m chaos ui export artifact.json --input-format definition --format chaos
python -m chaos run examples/ui-page.chaos
```

需求不满足时 CLI validate 返回退出码 1。显式指定输出文件，禁止覆盖输入文件。

## 资源与兼容范围

源码上限 100000 字符，定义／导出上限 200000 字符，树深度 64、节点 10000、动作和需求各 128、每求值 epoch 的 UI Task 64。数字必须可无损用于 JavaScript：整数绝对值不超过 `2^53-1`，浮点数必须有限；更大数值显式转字符串。Payload 只接受纯 JSON，不携带活函数、Task 或工具权力；内容中的敏感文本仍需调用方处理。产物不授予权限。

本增量提供组件声明、流式展示、页面／报告与 HTML 演示能力；没有声称完成 Python／Lisp 全部能力，也不依赖 OpenUI Cloud 的报表数据、MCP 或商业服务。
