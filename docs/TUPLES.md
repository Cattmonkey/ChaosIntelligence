# 元组、函数组合与复合索引

Tuple 是语言原生的不可变有序容器。它与 List、Record 是不同的值类型，具有自己的 AST、类型契约、结构模式及运行语义。普通函数、智能体函数、任务或实例可以成为元素；容器不会复制这些活值的执行权限。

## 创建与严格类型

`()` 是空元组，`(value,)` 是单项元组，`(a,b)` 是两项元组。单项末尾的逗号不能省略：`(value)` 仍是分组表达式。元组必须带括号；没有裸 `return a,b` 或裸逗号赋值。`(expression for ...)` 仍是惰性生成器表达式。

```chaos example=tuple-forms
let empty:Tuple=();
let single:Tuple=(42,);
let pair:Tuple=(20,22);
[type_of(empty),length(empty),single[0],pair[-1],
 (true,)==(1,),(20,22)==[20,22],pair==(20,22)]
```

结果为 `["Tuple",0,42,22,false,false,true]`。元素相等使用原有严格类型规则，Bool 不等于 Int，Tuple 不等于 List。普通相等和 contains 可以执行元素的用户相等协议；结构模式继续采用纯匹配，不运行用户方法或消费迭代器。

元组长度最多 10,000 项，并受现有总节点、字符串、整数、执行步数和时间预算约束。它的不可变性仅指容器结构：不能给 `pair[0]` 赋值，但元素内已有实例的可写字段仍可通过原协议修改。0.53 没有增加哈希、集合、元组重复、自动数值转换或 Python 对象导入。

## 转换、迭代与切片

`tuple(items=[])` 消费同步可迭代值，得到 Tuple；空调用返回空元组。它使用与 collect、位置展开和高阶消费相同的迭代调度器，能够消费 List、String、Tuple、生成器及用户同步迭代器。用户迭代方法里的 Agent 效果仍经过当前执行上下文。异步流要先显式 `await acollect(stream)`，再转换。

构造器借用传入的生成器或游标，普通完成或错误不自动关闭它。检查项数上限时可能多取一个溢出项，然后抛出 ResourceLimit；硬控制的清理继续遵循[迭代器生命周期](ITERATORS.md)与[函数消费规则](FUNCTION_COMPOSITION.md)，不会通过 Tuple 构造器额外执行用户 finally 或延长预算。

```chaos example=tuple-iteration
let values=tuple((x*2 for x in [10,11,12]));
fn add(a,b){a+b}
[collect(values),collect(values[::-1]),type_of(values[1:]),
 add(*(20,22)),map(fn(x){x+1},(20,21)),contains(values,22),
 bool(()),bool((0,)),collect((20,)+(22,))]
```

结果为 `[[20,22,24],[24,22,20],"Tuple",42,[21,22],true,false,true,[20,22]]`。元组切片和已有 `slice(value,start,stop)` 保留 Tuple 类型，单项下标返回元素。`Tuple + Tuple` 返回新元组；不会隐式与 List 混合。List 专用函数如 append、sort，以及要求 Task 列表的 gather，仍遵守各自已有合同；可用 collect 显式转换。

## 所有结构绑定位置

元组模式使用 `(left,right)`，单项模式使用 `(value,)`，剩余模式使用 `..rest`；剩余值仍为 Tuple。它可用于 match、let/var、函数参数、循环、推导、资源 as 和已有变量的批量赋值。List 模式不会匹配 Tuple，Tuple 模式也不会匹配 List。

```chaos example=tuple-patterns
var left:Int=1;
var right:Int=2;
(left,right)=(20,22);
let (first,..rest)=(left,right,99);
fn total(pair as (a,b):Tuple,/){a+b}
[left,right,first,collect(rest),total((20,22)),
 [a+b for (a,b) in ((20,22),(21,21))]]
```

结果为 `[20,22,20,[22,99],42,[42,42]]`。绑定失败不发布部分新变量，批量赋值仍先验证所有既有变量及契约再整体写入。参数模式在函数体开始前检查；异步函数及生成器也不会先创建可逃逸的冷句柄再检查参数。资源模式失败仍走已进入管理器的退出协议，见[解构绑定](PATTERN_BINDINGS.md)和[资源作用域](CONTEXTS.md)。

```chaos example=tuple-resource
class PairResource {
  fn __enter__(self){(20,22)}
  fn __exit__(self,error){null}
}
with PairResource() as (left,right){left+right}
```

结果为 `42`。括号分组不会创建元组模式；写单项模式时明确保留逗号。

## 函数及智能体作为元素

```chaos example=tuple-callable-message
let messages=channel(1);
await channel_send(messages,(fn(x){x*2},21));
channel_close(messages);
let (action,value)=(await channel_receive(messages)).value;
action(value)
```

结果为 `42`。在项目中，action 可以换成真实 Agent、语言工具或 Team 角色函数。Channel 递归复制 Tuple 内的原生 List/Record 容器，合法活值保留身份；创建、启动和交付仍复验解释器、执行周期与录制范围。把更宽权限的函数藏在元组里，不会扩大接收者的权限。

AgentTool 的模型工具合同保持单个 JSON 输入与 JSON 输出。handler 可以在内部构造、解构和传递元组；输出元组数据时需显式转换，不能把包含活函数或任务的元组当作模型工具实现传输出去。持久 Session、Process 状态及 Plan 定义继续遵守各自 JSON 边界，不恢复暂停栈或闭包。

## 复合索引键

新方言中的 `object[row,column]` 向用户条目协议传入一个 Tuple，与 `object[(row,column)]` 等价。`object[row,]` 传入单项 Tuple。组件可以是普通表达式或切片；普通读取和赋值先按对象、各键组件从左到右求值。普通赋值随后求右侧值，再进入写入协议；增强赋值则先调用读取协议取得原值，再求右侧值、运算并写回，保持原有只求一次目标的规则。

```chaos example=tuple-composite-key
class Matrix {
  fn __getitem__(self,key as (row,column):Tuple) {
    [row.start,row.stop,row.step,column]
  }
}
Matrix()[1:5:2,3]
```

结果为 `[1,5,2,3]`。用户方法收到 `(Slice,Int)` 元组，Slice 仍只含 Int/null 端点。原生 List、String、Tuple 不解释多维键，收到 Tuple 键会报错；可以用用户类定义矩阵、视觉坐标或多维数据访问。没有增加省略号索引；0.63 原生序列接入[无损整数索引](INDEX_PROTOCOL.md)，用户 Tuple 业务键仍保留原值。

Slice AST 只能出现在直接下标键或其直接元组组件中；不能嵌入任意列表、嵌套元组或普通表达式。需要独立 Slice 值时使用 slice_key。旧方言 1 的方括号访问与显式 `object.[key]` 保持原来的成员／容器规则，不接受新的逗号下标语法。

## JSON 与外部接口

运行结果、普通宿主值投影及 json_stringify 将 Tuple 表示为 `{"$tuple":[...]}`，逐项使用已有语言值投影。该结构是数据描述；parse_json 和 API 输入不会看到标签就自动恢复 Tuple，更不会恢复其中的函数、实例或 Task。严格 JSON 入口不隐式接受原生 Tuple，可显式使用 `parse_json(json_stringify(value))`。

```chaos example=tuple-wire
let original=(20,(22,));
let wire=parse_json(json_stringify(original));
let outer=tuple(wire["$tuple"]);
[type_of(wire),wire,type_of(outer),type_of(outer[1])]
```

结果为 `["Record",{"$tuple":[20,{"$tuple":[22]}]},"Tuple","Record"]`。显式重建外层不会自动重建内层。需要普通 JSON 数组时可对适当层次调用 collect；包含活值的元素仍受原有序列化及工具边界约束。旧的普通 `{"$tuple":...}` 记录始终仍是 Record。

## 宏、IDE 与自编程

```chaos example=tuple-macro
macro values(){
  let (left,right)=tuple([20,22]);
  syntax_literal((left+right,(left,right)))
}
values!()
```

结果为 `{"$tuple":[42,{"$tuple":[20,22]}]}`。纯宏阶段的 tuple 只接受有界 List、String、Tuple；不会调用运行期迭代协议。syntax_literal 保留元组 AST，拒绝嵌套的编译期函数或 Syntax 对象；syntax_value 仍只读取标量字面量。卫生模板中的模式变量、赋值目标和解构参数使用真实词法身份。

源码、`chaos.program/1`、CLI、SDK、HTTP、LSP 和 IDE 使用相同 tuple／p_tuple 节点。结构面板可编辑元组元素与模式；保真格式化保留注释、单项逗号和生成器边界。提示可以识别静态已知元素中的函数，遇到动态修改则保守丢弃精确形状。分析不会执行元素、用户迭代器或 Agent。

调试器显示有界的 Tuple 元素快照。Program 可编辑或重命名元组模式，再通过原有 probe/adopt 流程验证和采用候选；已运行项目保留原版本快照。元组并不提供跨重启活值恢复或任意优化正确性证明。

语法设计参考 [Python 的元组说明](https://docs.python.org/3/tutorial/datastructures.html#tuples-and-sequences)和[复合切片规则](https://docs.python.org/3/reference/expressions.html#slicings)。以上述 Chaos 规范及测试为实际合同，不承诺 Python 的全部元组语法或生态兼容。

原生 [Set 集合](SETS.md) 与 HashMap 支持递归 Tuple 键：纯键路径无钩子，运行时可含守约的 Instance 和协议集合；普通 Tuple 仍可容纳任意类型，哈希容器会另行验证成员及权限。
