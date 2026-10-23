# 类字段模式与智能体组件

`C::{field: pattern}` 按真实类身份解构实例的存储字段。它是原生模式，能用于函数参数、声明、匹配、异常捕获、循环、推导、批量赋值和资源绑定；界面与无界面执行使用相同的 AST。类可以组织智能体角色、状态与普通函数，再由模式把这些值接入函数组合。

## 类身份、默认值与子类

```chaos example=class-pattern-component
class Component {
  let role = fn(value) { value * 2 };
  var offset: Int = 0
}
class Specialized(Component) { let label = "fast" }
fn execute(component as Component::{role: role, offset: offset}, value) {
  role(value) + offset
}
execute(Specialized(), 21)
```

结果是 `42`。role 可以是具有相同调用合同的 Agent；模式只提取它，实际调用仍经过原来的模型、工具、权限和预算入口。这里使用普通函数，因此例子无需模型服务。

类模式默认检查列出的字段，允许额外字段。`Component::{}` 只检查名义类身份，包括子类；Record 模式 `{x: x}` 仍要求字段精确吻合，允许额外字段需要 `..rest` 或 `.._`。两次执行同一个 class 声明也会产生不同身份，不能靠名字相同匹配。

```chaos example=class-pattern-identity
fn make() { class Box { let value = 42 }; Box }
let A = make(); let B = make();
[match A() { A::{} => true, _ => false },
 match B() { A::{} => true, _ => false },
 match {value: 42} { A::{} => true, _ => false }]
```

结果是 `[true,false,false]`。类路径在绑定任何叶子之前，从外层词法环境解析；支持模块中的 `Types.Box::{...}`。不存在的名字、不是 Class 的名字、尚未完成的类或跨解释器值是错误；合法 Class 对非实例或其他类实例是失配。JSON 中的类／实例描述不会自动恢复成活对象。

## 纯存储投影与剩余字段

投影读取实例覆盖值，以及按 C3 顺序找到的类 `let`／`var` 默认值。默认值不会为了匹配重新计算，也不会为每个实例重新复制。只检查指定字段时，不扫描无关字段。`..rest` 收集其余有效存储字段为新 Record；`.._` 忽略其余字段，无需提取它们。字段名可以是字符串，以下划线开头不意味着私有字段。

```chaos example=class-pattern-rest
class Base { let name = "worker"; var count: Int = 0 }
class State(Base) { let enabled = true }
let state = State(); state.count = 42;
let State::{count: count, ..metadata} = state;
[count, metadata]
```

结果是 `[42,{name:"worker",enabled:true}]`。List／Record／Tuple 容器递归重建并保留共享关系；内部类／实例和其它不能代理的活对象保持原身份，可调用值在权限收紧时可能成为调用代理。这不是活对象深克隆，后续修改不属于事务回滚。`vars(instance)` 仍只提取实例自有字段，不含尚未覆盖的默认值；类模式采用有效存储字段，两者用途不同。

模式绝不执行 getter、用户描述符、索引协议、方法、模型或工具。未被实例字段覆盖的方法和描述符不属于存储字段。Property 和具有 getter 的数据描述符会遮蔽同名实例槽；普通实例槽可以覆盖非数据描述符。直接保存在实例槽中的描述符对象是普通字段值，不会因提取而执行。

```chaos example=class-pattern-no-getter
var calls = 0;
class Computed {
  let stored = 42;
  let value = property(fn(self) { calls = calls + 1; 99 })
}
let result = match Computed() {
  Computed::{value: value} => value,
  Computed::{stored: answer} => answer
};
[result, calls]
```

结果是 `[42,0]`。需要计算属性时，应显式调用普通成员访问，然后匹配其返回值。

## 原子绑定、控制流与资源

```chaos example=class-pattern-state
class Pair { let left = 22; let right = 20 }
var left: Int = 0; var right: Int = 0;
Pair::{left: left, right: right} = Pair();
let values = [a + b for Pair::{left: a, right: b} in [Pair()]];
[left, right, values]
```

结果是 `[22,20,[42]]`。赋值先匹配全部字段，再检查所有目标可写性和契约，最后一次提交 Cell 写入。声明和参数也不会留下部分绑定。模式参数在创建冷 Task／Generator 之前验证；已经发生的实参求值效果不会撤销。

```chaos example=class-pattern-async
class Request { let value = 21 }
async fn execute(request as Request::{value: value}) {
  await sleep(0); value * 2
}
let failed = try { execute({value: 21}) } catch error { error.code };
[failed, await execute(Request())]
```

结果是 `["match_error",42]`。同步／异步循环、推导和资源作用域复用相同的匹配器。资源进入成功后，如果字段模式失配，仍执行对应退出协议。

```chaos example=class-pattern-resource
class Result { let value = 42 }
var exits = 0;
class Resource {
  fn __enter__(self) { Result() }
  fn __exit__(self, error) { exits = exits + 1; false }
}
let answer = with Resource() as Result::{value: value} { value };
[answer, exits]
```

结果是 `[42,1]`。异常捕获仍匹配完整错误记录。要按抛出的实例匹配，使用 `catch {value: C::{...}, .._}`；这不引入 Python 异常类层次或异常链。

## 卫生宏与程序自修改

```chaos example=class-pattern-macro
class Box { let value = 42 }
macro read(type, instance) {
  quote {
    let C = unquote(type);
    do {
      let C::{value: C} = unquote(instance);
      C
    }
  }
}
let C = 7;
[read!(Box, Box()), C]
```

结果是 `[42,7]`。模板先用外层类别名解析目标，然后卫生重命名叶子；字段键保持稳定。编译期纯宏不能执行运行时类模式；quote 可以生成这些模式。模块别名也须显式传入模板。

Program IR 使用 `p_class(name, fields, rest)`，与 `p_record`、`p_tag` 并列。`fields` 保留字段标签及其模式节点；类路径是词法引用，字段标签不是变量。Program 可以编辑它们、探测候选并采用；既有运行快照不会被重写。旧 `chaos.program/1` 形状保持不变。

## 提示、格式化与权限边界

IDE 对可静态确定的存储字段传播类型契约，对动态字段保守显示 `Any`。类名参与语义高亮、定位与重命名；叶子作为各自作用域的变量，字段键保留原文。分析不执行类、getter 或智能体。结构编辑、格式化、CLI、SDK、HTTP 和 LSP 使用相同语法。

`::` 只在模式位置连接类路径和字段块。底层仍是两个冒号 token，因此 `items[::2]` 的步进切片以及旧 `catch name {body}`、`with manager as name {body}` 不变。格式化保留注释，并用重新解析后的 AST 验证等价。

提取值递归检查所属解释器，受当前权限、接收者和类权限的交集限制。Function、Native、Agent 和绑定方法可以获得收紧权限的调用代理；类、实例、描述符和其它活资源必须保持身份，若其权限过宽而无法安全收紧，会抛 `capability_error`。不通过复制活对象或修改其权限绕过检查。一次捕获的字段总数最多 10000 项，单个原生容器最多 10000 项，投影最多 20000 个节点、深度最多 100 层，并消耗已有执行预算。循环容器以 `value_error` 拒绝；非循环共享结构保持共享关系。

本功能不支持位置类模式、`__match_args__` 或由用户协议计算匹配字段。它扩展了语言表达能力，不代表 Python/Lisp 全生态兼容或解释器自举已完成。

类模式仍只读取经过所有权、预算与能力检查的存储字段，不调用 getter、描述符、`__eq__` 或迭代协议。外围 match 分支和结构声明／赋值现由真实模式帧保存挂起位置，类字段投影本身仍是原同步纯叶算法，不因可视化或智能体调用改变。真实状态与剩余语言边界见[模式执行状态](EXECUTION_FRAMES.md#h-模式分支与结构绑定的执行状态)。
