# 语言服务器、导航与重构

从 0.40 起，安装包提供 `chaos lsp`。当前文件读取后端支持 macOS/Linux；缺少 POSIX 无符号链接、目录描述符相对打开能力的平台（包括 Windows）会在初始化时返回明确错误。支持 Language Server Protocol 的编辑器可以启动它，通过标准输入／输出获得 Chaos 的诊断、代码补全、参数帮助、语义颜色、定义导航、引用查询、重命名和格式化。语言服务复用内置 IDE 的真正解析器与分析器；不用模型猜测关键词，不执行正在编辑的程序。

## 启动与编辑器接入

在已安装 Chaos 的环境中执行：

```sh
chaos lsp --stdio --root /absolute/path/to/package --entry main.chaos
```

也可使用同一解释器运行 `python -m chaos lsp`。`--stdio` 是默认传输方式的显式写法。`--root` 将读取范围限制在一个本地程序包，`--entry` 指定相对入口路径。没有显式根目录时，服务器使用客户端初始化提供的工作区根目录；客户端也未提供根目录时不会自动访问当前目录。`--language-version 1` 可用于历史方言；默认是 2，同一服务会话采用同一方言。

编辑器的通用 LSP 客户端需要设置：命令为已安装的 `chaos` 的绝对路径，参数为 `lsp --stdio` 及所需根目录／入口，语言标识为 `chaos`，文件匹配为 `*.chaos`。不同编辑器的配置格式不同，当前安装包不包含 VS Code 扩展或自动修改用户编辑器配置。可以使用编辑器已有的通用 LSP 客户端。

标准输出只用于 `Content-Length` 协议帧。不要把 shell 欢迎信息或其他日志混入该流。语言服务器不启动 HTTP 服务，不需要 API key、浏览器、Node.js 或 Codex App。

## 层级文档符号

客户端在初始化时明确发送 `capabilities.textDocument.documentSymbol.hierarchicalDocumentSymbolSupport: true`，`textDocument/documentSymbol` 才返回同一语言分析大纲映射的 `DocumentSymbol[]`，含完整 `range`、名称 `selectionRange`、详情及 `children`。未声明支持的旧客户端继续取得原平面 `SymbolInformation[]`，不改变其字段与名称范围。

层级表示原始语法声明的容纳关系，分析不执行宏、装饰器、属性协议或智能体。部分语法和大纲上限可限制可用条目；网页／SDK 的 `outline.complete`、`outline.truncated` 表达这些状态，LSP 标准数组本身没有相应完整性字段。引用模板和归属不明确的匿名函数体不虚挂到外层具名声明。

## 已实现的协议能力

| 方法／通知 | 行为 |
|---|---|
| `initialize`、`initialized`、`shutdown`、`exit` | 初始化、能力协商与标准退出流程 |
| `textDocument/didOpen`、`didChange`、`didClose` | 未保存草稿覆盖、增量变更与关闭恢复磁盘文件 |
| `textDocument/publishDiagnostics` | 发布带文档版本的解析及保守语义诊断 |
| `textDocument/completion` | 词法作用域、模块成员和真实内置签名提供补全及替换范围 |
| `textDocument/hover` | 名称种类、保守类型和签名 |
| `textDocument/signatureHelp` | 当前调用的参数及活动参数 |
| `textDocument/definition` | 已知声明的文件 URI 和精确名称范围 |
| `textDocument/documentSymbol` | 当前源码声明 |
| `textDocument/references` | 完整包内的已证明引用；不能证明完整时明确报错 |
| `textDocument/prepareRename` | 检查当前位置是否支持重命名，返回名称及范围 |
| `textDocument/rename` | 检查全包绑定关系后返回带文档版本的 WorkspaceEdit |
| `textDocument/semanticTokens/full` | 从词法与语义分析生成完整颜色标记 |
| `textDocument/formatting` | 返回文本编辑，使用共享源码保真格式化器 |
| `$/cancelRequest` | 取消请求并避免重复响应 |

只协商 UTF-16 位置编码。行号和列号从 0 开始；包含 emoji 等非 BMP 字符时，列按 UTF-16 单元计算。分析器公共 API 的 `from`／`to` 同样使用 UTF-16，但表示文件内绝对偏移；协议适配器负责转换。

格式化保留注释、字符串内容和语义。LSP `tabSize` 对应缩进宽度，受共享格式化器的 1 至 8 限制；行宽使用默认 88。`insertSpaces: false` 将格式化后的前导缩进转换为制表符，字符串和注释内容保持原样。格式化响应由编辑器应用，不直接写磁盘。内置 IDE 的“格式化”和“保存时格式化”、CLI `chaos fmt`、SDK `format_source` 使用相同排版逻辑。

## 草稿、版本与同步

打开文档后，未保存内容优先于磁盘文件，并参与同一包内其他文件的导入分析。一次增量通知的多个编辑按顺序应用；过期版本、非法范围或切断 UTF-16 代理对的修改不会部分写入草稿。关闭文档后移除内存覆盖，再从磁盘分析。

分析在工作线程中进行，协议读取仍能接收编辑和取消。请求使用捕获的源码快照；编辑器应像处理其他 LSP 服务一样丢弃已经过期的补全、跳转或格式化结果。诊断在发布前校验版本及工作区修订，过期结果会丢弃。服务不会在编辑中自动执行程序或调用模型。

目录项目同样可作为 LSP 的 `--root`；内置 IDE 运行时会按原 Workspace 协议同步磁盘已保存文件，无需另启 checkout/watch。目录启动器 `chaos.py` 不直接接收 `lsp` 子命令；可使用已安装 CLI，或 `python3 /absolute/path/project/.chaos/runtime/run_chaos.py lsp --stdio --root /absolute/path/project --entry main.chaos` 调用包内原 CLI。

LSP 草稿仅属于编辑器会话。它不写 `.chaos-workspace.json`，也不直接提交服务项目。已有项目可先用 `chaos workspace checkout` 取得本地包，编辑保存后通过 `chaos workspace sync` 或 `--watch` 同步；版本冲突仍由既有 CAS 机制处理。详见[实时同步](REALTIME.md)。内置 IDE 则使用自己的项目草稿和相同公共分析 API。

## 定义定位与边界

定义来自实际词法绑定和模块导入。普通别名定位到别名声明；导入模块成员定位到目标模块；类方法、递归绑定和宏保留各自的来源。内置 IDE 使用 F12 或“转到定义”，导航时保留多文件草稿，并验证异步响应仍对应当前源码。

没有可靠来源时返回空结果：例如动态生成的成员、记录字面量的字段名、类型注解位置、未知运行时值，以及没有可打开项目文件的内置值。分析是保守静态推导，不能预测模型响应、任意用户描述符或动态程序修改。定义跳转不是运行时反射，也不改变当前执行快照。

工作区只读取受支持的本地文件 URI 和根目录内的 `.chaos` 文件，不跟随符号链接。程序包沿用 64 个模块、每文件 100,000 字符、总 UTF-8 内容 1,000,000 字节的上限；协议帧、待处理请求及文档数量也有界。补全、诊断与定义分析包包含当前文件、配置入口及它们的传递导入；可达模块优先读取已打开的草稿。引用查询和重命名另行读取有界完整包，以包含未从入口可达的反向导入者；同样叠加已打开的草稿。读取或完整性检查失败会明确报错，不把部分引用当作完整结果。

当前不提供代码操作、语义标记增量、任意远程 URI、自动第三方包安装或完整静态类型推导。调试使用现有语言调试器，不能把这个 LSP 子集当作 Debug Adapter Protocol 服务。协议依据 [LSP 3.17 规范](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)。


## 引用与重命名

引用索引读取原始 AST，不执行宏或用户函数。重命名验证候选不会引入名称捕获，并由编辑器应用版本化 WorkspaceEdit；服务器不写文件。未知动态模块访问、不能证明身份的调用或宏生成绑定可能阻止重命名，普通字符串和记录键不会随意改名。范围以本次提供的源码包为界；外部宿主调用者与持久配置需显式协调。见[重构协议与限制](SYMBOL_REFACTORING.md)。
