# 符号引用与程序重构

0.48 将跨模块引用查询与重命名作为语言的 Program 操作，编辑器、HTTP、SDK、LSP 和编程智能体复用同一个原始 AST 符号索引。它不执行模型、工具、用户函数或宏来猜测名字。

## 从 Program 生成新候选

```chaos example=program-symbol-rename
let original=program_from({kind:"program",entry:"main.chaos",files:{
  "main.chaos":"import \"math.chaos\" as M;M.add(input)",
  "math.chaos":"fn add(x:Int)->Int{x+2};export {add};"
},languageVersions:{"main.chaos":2,"math.chaos":2}});
let uses=program_references(original,"math.chaos",3);
let candidate=program_rename(original,"math.chaos",3,"increase");
[uses.complete,length(uses.references),type_of(candidate),
 program_info(original).files["main.chaos"],program_info(candidate).files["main.chaos"]]
```

结果包含完整性、三个引用位置、新 Program，以及改名前后的入口源码。原始 Program 不变，候选保留来源和逐文件语法版本。位置采用 UTF-16 单元，与网页光标和 LSP 一致。

| 语言函数 | 返回 |
|---|---|
| `program_references(value,file,position,include_declaration=true)` | `{target,references,complete}`，不完整时另有 `limitations` |
| `program_rename(value,file,position,new_name)` | 同一解释器拥有的新 Program；没有保存或执行效果 |

`target` 包含声明文件、范围、名称、种类和命名空间；引用包含文件、范围和角色。批量赋值的每个目标叶子是已有符号的一次写引用，构造器是读引用；资源模式叶子是其局部作用域的声明。无符号的普通文本返回空目标。不能把 `complete:false` 的已知引用当作完整结果；重命名会拒绝不能证明完整性的情况。

候选可交给 `program_probe` 测试，再用 `program_adopt` 的既有版本校验采用；持续进程可返回 `process_evolve`，让已测试候选和进程检查点一起提交。当前执行栈继续使用开始时的源码快照。

## 依据绑定身份修改

索引遍历给定包的全部文件，包括未从入口可达的反向导入者。重命名跟随词法声明、导出和可证明的模块访问；普通别名是自己的声明，结构字段不是变量名。注释、普通字符串和同名数据键不会随意改变。

对确定的模块字面量键，例如 `M["add"]`，索引可把整个字符串 token 标记为模块成员引用，并保留引号形式进行修改。动态计算的键、失去可证明来源的模块值或不确定的调用关系可能导致 `complete:false`／拒绝重命名。普通记录的 `record["add"]` 不因此改名。

编辑采用精确 token 范围，保留其他源码字符。生成候选后重新解析整个包，并核对所有引用的绑定关系，避免目标或无关名称被捕获。非法名称、重复声明、缺失模块、不完整语法、内建／标准模块及不能确定的结构成员目标会明确报错。宏原始源码相位与运行时绑定分开；生成的符号和任意宏计算不由重构器执行或推断。

重构边界是提供的源码包。持久 Process 配置中的 export、Session 配置、外部宿主调用者和序列化协议不会被隐式改写；ProcessEvolution 会检查当前 Process 下一轮所需模块和导出。普通 program_adopt 不会同步修改或统一校验全部持久配置；重命名配置引用的入口时，开发者需显式协调配置。

这是一种保守的静态重构，不是任意程序等价性的证明。外部调用者、动态反射字符串、序列化协议字段和整个包之外的消费者仍需开发者按接口契约处理。没有可靠证据时拒绝修改，不能靠一次解析成功推定重构正确。

## 可恢复的自重构示例

[refactoring-dot 入口](../examples/refactoring-dot/main.chaos)启动一个持续进程；[角色程序](../examples/refactoring-dot/dot.chaos)查询自身 [behavior](../examples/refactoring-dot/behavior.chaos) 的引用，生成重命名候选并运行[验收入口](../examples/refactoring-dot/tests.chaos)，通过 ProcessEvolution 采用。下一轮及主机重启后从新源码恢复，旧运行仍保留原快照。

## IDE、语言服务器与无界面工具

内置 IDE 查询当前完整多文件草稿，展示引用和重命名候选；应用候选前再次核对草稿快照。修改一起进入本地草稿，保存沿用原有项目版本 CAS。期间发生输入、文件切换或外部更新时，过期结果不会覆盖新内容。系统编程智能体通过 `draft.references` 和 `draft.rename` 操作自己的提案，仍需已有提案采用流程。

HTTP 提供只读计算 `POST /api/v1/language/references` 和 `/language/rename`。参数沿用 `source/file/files/entry/position/languageVersions`，分别可加 `includeDeclaration` 或必需的 `newName`。`Framework.references`／`Client.references` 和 `.rename` 使用 Python 的 `include_declaration`／`new_name` 参数。重命名结果包含完整 `files/entry/languageVersions`、`edits` 和原源码 `baseHashes`，不会直接更新项目。

LSP 增加 `textDocument/references`、`prepareRename` 与 `rename`，使用有界全包读取与未保存覆盖。编辑返回带文档版本的 WorkspaceEdit，不写磁盘；标准 Location[] 无法表达不完整性，因此不完整引用查询明确报错。旧的补全与跳转仍使用原有依赖闭包读取。

命令行从明确提供的 Program JSON 查询或生成候选，不直接修改文件：

```sh
chaos symbols references program.json --file math.chaos --position 3
chaos symbols rename program.json --file math.chaos --position 3 --new-name increase
```

用 `-` 读取标准输入；`--exclude-declaration` 排除声明位置。输出是 JSON 结果，重命名结果的 `files` 是完整候选。它与 `chaos workspace` 的同步提交是两个显式步骤。

源码高亮、参数提示、格式化与源码／IR仍来自同一解析器。详见[语言设计](AGENT_LANGUAGE.md)、[自编程](SELF_PROGRAMMING.md)、[IDE](IDE.md)、[LSP](LANGUAGE_SERVER.md)和[实时同步](REALTIME.md)。

## 私有参数名与关键字数据

位置专用参数、`*rest` 和 `**options` 的槽名是私有词法名字；它们不是调用端的关键字参数标签。重命名这些槽只修改声明与实际词法引用，即使函数被导出、传给 AgentTool 或部分应用，也不会把同名关键字数据当成参数引用。普通位置或具名参数、仅关键字参数仍属于公开调用合同，保留动态调用和包外调用者的保守限制。

```chaos example=posonly-rename
let original = program_from({kind:"program", entry:"main.chaos", files:{
  "main.chaos":"fn consume(value:Int,/,**options){[value*2,options]};consume(input,value=\"metadata\")"
}, languageVersions:{"main.chaos":2}});
let renamed = program_rename(original, "main.chaos", 11, "amount");
[program_info(original).files["main.chaos"],
 program_info(renamed).files["main.chaos"]]
```

结果展示原始与候选源码：调用中的 `value=` 保持原样，函数内的 value 改为 amount。此例只构造和检查新 Program，不执行或保存项目。在允许 Program 执行的项目环境中，两份程序以 21 调用均返回 `[42,{value:"metadata"}]`。采用候选仍需正常测试与版本检查。新名字引起的变量捕获、冲突、不透明宏语法和只读模块限制仍会拒绝修改；私有参数不代表任意重命名都安全。
