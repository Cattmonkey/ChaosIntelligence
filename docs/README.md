# Chaos 文档中心

按“运行一个程序 → 查看进度并追加指导 → 使用编程助手 → 组合函数与 Agent → 嵌入或部署”学习。语言目标和有限验收见[语言契约](LANGUAGE_CONTRACT.md)；当前范围与可选演进见[范围与路线图](LANGUAGE_ROADMAP.md)。Python／Lisp 的[能力对照](PYTHON_LISP_CAPABILITIES.md)用于说明差异，不是发布完成清单。

## 开始使用

| 学习步骤 | 教程与可核对的结果 |
| --- | --- |
| 1. 取得项目并运行 | [快速开始](GETTING_STARTED.md)：目录启动器、模板42、double(21)→42 |
| 2. 保存、运行和理解历史 | [用户指南](USER_GUIDE.md)：CLI、IDE与持久状态的区别 |
| 3. 看见进度并引导当前运行 | [无密钥互动教程](REALTIME.md#从运行到追加指导)：网页、终端 stream 与指导接收 |
| 4. 编辑、调试与审阅助手提案 | [IDE](IDE.md) · [同步与冲突](REALTIME.md) |
| 5. 配置自己的模型 | [模型配置](MODEL_CONFIGURATION.md)：先确定配置来源，再验证实际请求；公开例子只用环境变量占位 |
| 6. 运行大模型智能体 | [五个学习示例](MODEL_EXAMPLES.md)：问答 → 多角色 → 并发 → 函数工具 → Bot |
| 7. 用函数组织复杂工作 | [Agent 函数语言](AGENT_LANGUAGE.md) · [语言参考](LANGUAGE.md) · [标准库](STDLIB.md) |

project ZIP、portable SDK ZIP 与 wheel 的用途不同，选择方式见[安装](INSTALLATION.md)。项目目录不是内置 Python 的独立 exe；纯计算可离线，首次完整 IDE／模型依赖需要显式 setup。

使用、修改或分发前请阅读[许可与 AI 训练限制](LICENSING.md)。

## 日常维护与交付

| 场景 | 说明 |
| --- | --- |
| 随包快速查命令 | [目录包速查](PROJECT_README.md)，也是 project ZIP 根 README 的唯一正文来源 |
| 放进现有工程，保留宿主文件 | [安装](INSTALLATION.md#放进已有工程) |
| 自动检测环境、配置模型与验收 | [环境配置与自检](DEPLOYMENT.md)：彩色进度、分层向导、可选智能建议 |
| 查当前版本、活动运行目录与依赖 | [用户指南](USER_GUIDE.md)中的目录命令和数据位置 |
| 换新版、回退或迁移旧项目 | [版本替换](UPDATES.md#目录项目直接换新版) |
| 断线、进度停滞、配置失败或同步冲突 | [故障排查](TROUBLESHOOTING.md) · [事件恢复与指导](REALTIME.md) |
| 交付可复制环境或嵌入其他语言 | [开发者指南](DEVELOPER.md) · [SDK](SDK.md) · [发布](RELEASING.md) |

## 语言与组合专题

- [可组合续延](CONTINUATIONS.md)
- [语法宏](MACROS.md)
- [挂起与任务](EFFECTS.md)
- [调试器](DEBUGGER.md)
- [增强赋值](AUGMENTED_ASSIGNMENT.md)
- [组合契约](CONTRACTS.md)
- [通用键映射与用户哈希](HASH_MAPS.md)
- [泛型函数与方法组合](GENERIC_FUNCTIONS.md)
- [群体协议](SWARM.md)
- [智能体 Harness 的持续改进](HARNESS_EVOLUTION.md)
- [对象与组件](OBJECTS.md)
- [属性协议](ATTRIBUTE_PROTOCOL.md)
- [机器人与设备](ROBOT_DEVICES.md)
- [树莓派 HID](RASPBERRY_PI.md)
- [高级编程](ADVANCED.md)
- [形式语义](SEMANTICS.md)
- [装饰器与属性协议](DECORATORS.md)
- [用户描述符协议](DESCRIPTOR_PROTOCOL.md)
- [运算协议](OPERATORS.md)
- [用户迭代器](ITERATORS.md)
- [对象索引与切片](INDEXING.md)
- [目标、计划与可执行技能](AGENT_MODES.md)
- [模式库编写与版本一致性](MODE_AUTHORING.md)
- [智能体定义值与角色重构](AGENT_VALUES.md)
- [团队与角色指南](TEAMS.md)
- [Program 与语言内自编程](SELF_PROGRAMMING.md)
- [Program 库链接与工程复用](PROGRAM_LINKING.md)
- [Process 持续函数](PROCESSES.md)
- [持久智能体会话](SESSIONS.md)
- [程序历史与恢复](PROGRAM_HISTORY.md)
- [资源作用域](CONTEXTS.md)
- [并发任务](TASKS.md)
- [Channel](CHANNELS.md)
- [计时器与多源任务等待](WAITS.md)
- [原子通道选择](SELECT.md)
- [可迭代值与函数组合](FUNCTION_COMPOSITION.md)
- [动态资源栈](RESOURCE_STACKS.md)
- [语言函数作为智能体工具](FUNCTION_TOOLS.md)
- [智能体协议测试](AGENT_TESTING.md)
- [Process 原子自我演进](PROCESS_EVOLUTION.md)
- [GPU 与 Mac M 系列计算](GPU_COMPUTE.md)
- [函数到融合 Metal 内核](GPU_METAL.md)
- [GPU 控制流](GPU_CONTROL_FLOW.md)
- [无损整数索引](INDEX_PROTOCOL.md)
- [类创建协议](CLASS_CREATION.md)
- [生成式界面、报告与演示](GENERATIVE_UI.md)
- [原生类型判断与基类适配](TYPE_PROTOCOLS.md)
- [数值指南](NUMBERS.md)
- [情况处理与恢复出口](CONDITIONS.md)

## 开发与维护

| 任务 | 参考 |
| --- | --- |
| 嵌入与可信宿主扩展 | [开发者指南](DEVELOPER.md) · [SDK与接口](SDK.md) |
| 订阅实时状态并追加指导 | [实时接口](REALTIME.md#从其他程序订阅实时事件) · [SDK](SDK.md) · [HTTP API](HTTP_API.md) |
| 模型、工具及后台任务的宿主边界 | [模型配置](MODEL_CONFIGURATION.md) · [Harness](HARNESS.md) |
| 外部编辑器语言服务 | [LSP](LANGUAGE_SERVER.md) |
| 安装、更新与交付制品 | [安装](INSTALLATION.md) · [更新](UPDATES.md) · [发布](RELEASING.md) |
| 排错与文档贡献 | [故障排查](TROUBLESHOOTING.md) · [文档维护](MAINTAINING_DOCS.md) |

## 在线与离线阅读

目录项目用 `./chaos` 启动后，按实际服务地址进入 `/docs`；原 `chaos serve` 使用其配置的监听地址。正文来自随包 Markdown，不依赖另一份手写网站。

维护者或已安装 CLI 的用户可以构建只读站点：

```sh
chaos docs path
chaos docs check
chaos docs build --output ./docs-site
python -m http.server 8000 --directory ./docs-site --bind 127.0.0.1
```

静态站包含搜索、原文与示例，供离线阅读；它不启动语言运行、设置或后台任务 API。构建目录是生成物，不存放项目源码或密钥。没有实际部署时，只能称为本地文档，不能声称公网已经发布。

## 在终端查阅与提问

运行 `chaos docs list` 查看安装版主题 ID，用 `chaos docs show command-line` 阅读教程；目录包使用 `./chaos docs ...`。查阅不需要模型、网络或网页。需要解释某页时，将该 ID 显式传给 `ask --topic`；需要修改项目时使用同一 IDE 编程助手的 `assist` 入口。配置与完整步骤集中在[命令行教程](COMMAND_LINE.md)。

## 支持项目

如果你愿意支持持续开发与文档维护，可在 [GitHub 项目的「支持项目」区域](https://github.com/Cattmonkey/ChaosIntelligence#支持项目)查看微信和支付宝赞赏码。赞赏完全自愿，不影响使用权限，也不改变项目许可。
