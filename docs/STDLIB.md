# 内建函数与标准模块

本页覆盖 language core v1 在 `chaos/language.py` 的全部内建函数，以及 `chaos/modules.py` 的全部 `STDLIB` 导出。所有内建函数直接可用，无需 `import`；标准模块提供明确命名空间，例如 `import "std/list" as L; L.map(...)`。类型和错误机制见[语言参考](LANGUAGE.md)。

下面签名使用实现接受的真实参数名，具名调用必须使用这些名字，例如 `map(fn=fn(x) { x * 2 }, xs=[1, 2])`。`?` 表示可省略参数，`/` 表示其前面的参数只能按位置传入；这些记号与原生函数签名对应；0.52 起用户函数声明也支持 `/` 边界。`fn/f/g` 表示可调用值，`xs/ys/items` 在列表专用函数中表示 List；map/filter/fold/any/all 及 apply 的 args 接受同步可迭代值，`obj` 表示 Record。除效果函数外，这些函数不访问网络或宿主文件；回调仍可能由调用方传入带效果的函数。容器函数返回新值，均受资源预算约束。

## 组合契约

`contract_ref(name)`、`contract_recursive(definitions,root)`、`contract_list(element)`、`contract_tuple(elements)`、`contract_record(required,optional=null,allow_extra=false)`、`contract_union(alternatives)`、`contract_optional(contract)`、`contract_map(key,value)` 返回 Contract；`contract_accepts(contract,value)` 返回 Bool；`contract_info(contract)` 返回诊断 Record。十个接口同时由 std/contracts 同名导出，接入既有命名注解，详见[组合契约](CONTRACTS.md)。

## 通用键映射

`hash(value)` 返回 Int；`hash_map(entries=[])` 创建 HashMap；`hash_get(mapping,key,default=null)`、`hash_has(mapping,key)` 查询关联；`hash_set(mapping,key,value)`、`hash_remove(mapping,key)` 返回新映射；`hash_items(mapping)`、`hash_keys(mapping)`、`hash_values(mapping)` 返回有序 List；`hash_info(mapping)` 返回纯元数据。十个接口同时由 std/hash 同名导出。用户协议、同步输入、严格键类型、权限和预算统一规定在[HashMap 规范](HASH_MAPS.md)。

## 泛型函数

全局 `generic_function(name,arity=1)`、`generic_method(function,specializers,implementation,qualifier="primary",*,with_next=false)`、`generic_remove(function,specializers,qualifier="primary")`、`generic_methods(function)`、`generic_info(function)`、`generic_next_available(next)`、`generic_eql(value)` 和 `generic_eql_info(specializer)` 同时由 `std/generic` 同名导出。前三者返回不可变方法表的新 Function；原查询分别返回方法记录 List、原六字段 Record 和 Bool。generic_eql 返回原生 EqlSpecializer；generic_eql_info 只返回 kind／comparison／valueType 三个诊断字段，不暴露被捕获值。primary 可显式启用零参数 next 调用较一般的方法；旧方法记录不增加字段，启用项额外含 withNext:true。实际类型／值分派、组合顺序、next 与冷任务边界统一规定在[泛型函数规范](GENERIC_FUNCTIONS.md)。

## 惰性同步与异步流

`std/iter` 的27个导出是实际 Chaos 函数，返回冷 Generator／AsyncGenerator；参数检查立即发生，取得游标与回调按消费需求进行。它提供 map／filter／flat_map／take／drop／while／enumerate／zip／chain／batches／scan 及异步对应项、from_sync／count／repeat。准确参数、借用及关闭规则见[惰性流](STREAMS.md)。以下全局内建与 std/list 保持原有急切行为。

## 列表与高阶函数

| 函数签名 | 结果与边界 |
| --- | --- |
| `map(fn, xs)` | 依次计算 `fn(item)`，返回同长度列表 |
| `filter(fn, xs)` | 保留谓词返回 `true` 的元素；谓词必须返回 Bool |
| `fold(fn, initial, items)` | 依次调用 `fn(accumulator, item)`；空列表返回 `initial` |
| `range(start, stop=null, step=1)` | Int 列表；只给一个位置值时它表示终点且起点为 0；给出 stop 时 start 表示起点；终点不含，可负 step，step 不可为 0 |
| `length(x)` | List／Tuple／Set 项数、String 码点数、Record 字段数，或 Instance 的 `__len__` 非负 Int |
| `head(xs)` | 首项；空列表报 `IndexError` |
| `tail(xs)` | 除首项外的列表；空列表返回 `[]` |
| `append(xs, x)` | 新列表末尾追加一项 |
| `reverse(xs)` | 倒序列表 |
| `sort(xs)` | 升序；允许 Real 数值列表（Int／Number／Fraction 可混合）或字符串列表；不支持自定义比较器 |
| `zip(xs, ys)` | 二元列表组成的列表，截断到较短输入 |
| `flatten(xs)` | 列表的列表展开一层；每个子项必须为 List |
| `any(f, xs)` | 存在谓词为真时返回 true 并短路；空列表为 false |
| `all(f, xs)` | 全部谓词为真时返回 true；首次 false 即短路；空列表为 true |
| `take(n, xs)` | 前 n 项；n 为 Int，小于 0 按 0 处理 |
| `drop(n, xs)` | 去掉前 n 项；n 为 Int，小于 0 按 0 处理 |
| `compose(f, g)` | 返回接收任意位置/具名参数的可调用值；先传给 g，再把 g 的单个结果传给 f |
| `partial(fn, /, *bound, **keywords)` | 绑定最左侧位置及具名参数，调用时补位置参数；本次具名值覆盖已绑定的同名值 |
| `apply(fn, args, kwargs=null)` | 消费同步可迭代 args 后结合可选 Record kwargs 调用 fn；省略或传 null 时不传具名参数 |

`range(5)` 与 `range(start=5)` 都生成 `[0, 1, 2, 3, 4]`；`range(start=2, stop=5)` 生成 `[2, 3, 4]`。仅写 `range(stop=5)` 会缺少必需的 start，不能把文档里的“终点”误当作单参调用的关键字名。

`partial` 的 fn 只能按位置传入，`partial(target, fn=value)` 中的 fn 会转交 target；绑定目标参数的错误可能在最终调用时才报告。`apply` 和最终调用都会执行同样的契约、缺参及重复绑定检查。使用实例见[进阶编程](ADVANCED.md#把配置与调用参数分开)。

`any/all` 的回调也必须返回 Bool。map/filter/fold/any/all 接受与 iter 相同的同步数据源，String 按码点消费；它们不会自动等待异步输入或回调。其他列表专用函数如 head/tail 仍不接受 String。每次消费计入输入项预算，短路不预读且不隐式关闭借用游标，见[函数组合](FUNCTION_COMPOSITION.md)。

```chaos example=stdlib-list
import "std/list" as L;
{
  mapped: L.map(fn(x) { x * 2 }, [1, 2]),
  folded: L.fold(fn(acc, x) { acc + x }, 0, [1, 2, 3]),
  descending: L.range(5, 0, -2),
  zipped: L.zip([1, 2], ["a"]),
  flat: L.flatten([[1], [2, 3]]),
  first: L.take(2, [1, 2, 3]),
  rest: L.drop(2, [1, 2, 3]),
  vacuous: [L.any(fn(x) { true }, []), L.all(fn(x) { false }, [])]
}
```

结果为 `{"mapped":[2,4],"folded":6,"descending":[5,3,1],"zipped":[[1,"a"]],"flat":[1,2,3],"first":[1,2],"rest":[3],"vacuous":[false,true]}`。

## 字符串与通用容器查询

| 函数签名 | 结果与边界 |
| --- | --- |
| `upper(s)` / `lower(s)` | Unicode 大写 / 小写转换 |
| `trim(s)` | 移除两端空白 |
| `split(s, sep)` | 按明确分隔符拆分，保留连续分隔产生的空项；空分隔符报错 |
| `join(sep, xs)` | 用分隔符连接字符串列表；参数顺序是分隔符在前 |
| `contains(container, item)` | String 子串、Record 字段存在性或 List／Tuple 的递归运行时相等查找（可调用实例协议），Set 共享哈希／相等协议查找，或 Instance 的 `__contains__` |
| `replace(text, old, new)` | 替换所有匹配；old 为空串时在字符间及两端插入 |
| `slice(value, start, stop?)` | String/List/Tuple 的左闭右开切片；允许负 start/stop，越界自动截断；不支持 step |
| `str(x)` | String 原样返回；其余值做显示转换，包括可调用值/类型的描述记录 |
| `number(s)` | 从 String 或 Real（含 Fraction）转为浮点 Number；Bool 不接受，非有限结果被拒绝 |
| `type_of(value)` | 返回类型名称字符串，例如 `Int`、`Function`、`Task`、`Generator`、`AsyncGenerator`、`Iterator`、`AsyncIterator`、`Slice`、`Class`、`Instance`、`Descriptor` 或 `NotImplemented` |

当前语言版本的 `xs[-1]` 从末尾取元素；旧语法版本及显式 `xs.[-1]` 仍拒绝负直接下标。原有 `slice(xs,start,stop)` 的签名和行为保持不变。`contains` 搜索 String/Record 时 item 必须为 String。所有字符串长度和下标以 Unicode 码点计，组合字符和 emoji 序列可能占多个码点。

```chaos example=stdlib-string
import "std/string" as S;
[S.upper(S.trim("  chaos  ")), S.split("a,,b", ","),
 S.join("-", ["a", "b"]), S.slice("abcd", -2),
 S.replace("a-b-a", "a", "x"), S.contains("Chaos", "hao"),
 type_of(number("2")), str("plain")]
```

结果是 `["CHAOS", ["a", "", "b"], "a-b", "cd", "x-b-x", true, "Number", "plain"]`。

## 数值

| 函数签名 | 结果与边界 |
| --- | --- |
| `abs(x)` | Numeric 绝对值；Complex 返回有限 Number，或 Instance 的 `__abs__` |
| `min(xs)` / `max(xs)` | 非空 Real 列表最小 / 最大值；空列表报错 |
| `sum(xs)` | Numeric 列表求和，空列表为 Int `0` |
| `floor(x)` / `ceil(x)` | 向下 / 向上取整，返回 Int |
| `sqrt(x)` | 平方根，返回 Number；负数报错 |
| `pow(base, exponent)` | 与 `base ** exponent` 共用协议；数值幂须为有限值且满足整数预算；Instance 可通过 `__pow__` / `__rpow__` 定义结果 |
| `floordiv(left, right)` | 数值向下取整除法或 `__floordiv__` / `__rfloordiv__` |
| `bool(value)` | Bool 原值；Instance 的 `__bool__`，或缺失时由 `__len__` 转换；其它类型报错 |
| `round(x)` | 取最近整数，正好居中时取偶数；返回 Int，无小数位参数 |

数学常量 `pi` 和 `e` 由 `std/math` 导出，不是全局内建名称。普通数学函数不接受 Bool。原生 Complex 支持 Int 幂；旧 Number 的负底数非整数幂仍被拒绝，不自动生成 Complex。

```chaos example=stdlib-math
import "std/math" as M;
[M.abs(-3), M.min([2, 1]), M.max([2, 1]), M.sum([]),
 M.floor(2.9), M.ceil(2.1), M.sqrt(9), M.pow(2, 3),
 M.round(2.5), M.round(3.5), M.pi > 3]
```

结果是 `[3, 1, 2, 0, 2, 3, 3.0, 8, 2, 4, true]`。

## 记录

| 函数签名 | 结果与边界 |
| --- | --- |
| `get(obj, key, default?)` | 字段值；缺字段返回 default，省略时为 null |
| `set(obj, key, value)` | 新记录，增加或覆盖指定字段 |
| `keys(obj)` / `values(obj)` | 字段名 / 值的列表，按记录插入顺序 |
| `has(obj, key)` | 字段是否存在，不看对应值的真假 |
| `merge(a, b)` | 浅合并，冲突字段取右侧 |
| `remove(obj, key)` | 新记录，去掉字段；不存在时内容保持相同 |

所有 key 必须是 String。`get` 的默认值是普通实参，会在调用前求值，并非惰性回调。默认值不会替代已存在的 `null`。

```chaos example=stdlib-record
import "std/record" as R;
let obj = {x: null, y: 2};
[R.get(obj, "x", 99), R.get(obj, "missing", 99), R.has(obj, "x"),
 R.keys(obj), R.values(obj), R.remove(R.set(obj, "z", 3), "y"),
 R.merge({x: 1}, {x: 2, y: 3})]
```

结果是 `[null, 99, true, ["x", "y"], [null, 2], {"x": null, "z": 3}, {"x": 2, "y": 3}]`。

## JSON

| 全局函数 | `std/json` 名称 | 行为 |
| --- | --- | --- |
| `parse_json(text)` | `parse(text)` | JSON String → 语言值；拒绝非有限数，遵守值大小限制 |
| `json_copy(value)` | `copy(value)` | 严格验证有限 JSON 树并返回深复制；拒绝任何层次的活句柄、函数、Tuple／Set 等原生非 JSON 值 |
| `json_stringify(value)` | `stringify(value)` | 语言 JSON 值 → JSON String；Tuple／Set 按 `$tuple`／`$set` 递归投影，直接函数或类型值等非 JSON 值仍拒绝 |

```chaos example=stdlib-json
import "std/json" as J;
let value = {message: "你好", number: 1.5, active: false, empty: null};
[J.parse(J.stringify(value)) == value, J.stringify("plain"), str("plain")]
```

结果是 `[true, "\"plain\"", "plain"]`。`str` 用于显示，不能代替 JSON 编码。对于受支持且满足预算的 JSON 值，`parse(stringify(value))` 保留类型与内容；`data` 构造值是普通协议记录，因此也可经过 JSON。不要依赖 stringify 输出的空格样式来判断语义相等。

`json_copy` 用于状态、协议和智能体数据的边界：只接受 Null、Bool、Int、有限 Number、String、List 和字符串键的 Record。最大深度 32、遍历节点 20,000、每个 List 10,000 项，编码后至多 250,000 字符，整数仍受原有限位数预算约束；每步遍历计入当前计算预算。它不会运行用户转换函数，也不会把嵌套 Program、Task 或资源变成可接受的描述标签。`json_stringify` 的句柄／Tuple／Set 投影用于显示与导出描述；`parse_json` 读取那些标签后只得到普通数据，不会恢复原类型或授权。

```chaos example=json-strict-copy
import "std/json" as J;
let original = {items:[1,2], active:true};
let copied = J.copy(original);
[copied, try { J.copy({nested:[tuple([1])]}); false } catch error { true }]
```

## Option 与 Result

| 模块 | 导出 | 含义 |
| --- | --- | --- |
| `std/option` | `Option`、`None`、`Some`、`map` | `None()` 无值；`Some(value)` 有值；`map(f, o)` 只转换 Some |
| `std/context` | `closing, aclosing, contextmanager, asynccontextmanager, ExitStack, AsyncExitStack` |
| `std/result` | `Result`、`Ok`、`Err`、`map` | `Ok(value)` 成功；`Err(error)` 失败；`map(f, r)` 只转换 Ok |

```chaos example=stdlib-data
import "std/option" as O;
import "std/result" as R;
let option = O.map(fn(x) { x + 1 }, O.Some(2));
let result = R.map(fn(x) { x + 1 }, R.Err("unavailable"));
[match option { O.Some(x) => x, O.None() => 0 },
 match result { R.Ok(x) => x, R.Err(e) => e }]
```

结果是 `[3, "unavailable"]`。标准模块 map 是模块内声明的函数，其具名参数分别为 `O.map(f=..., o=...)` 与 `R.map(f=..., r=...)`，和全局 `map(fn, xs)` 不同。类型契约可写 `value: O.Option` 或 `value: R.Result`。没有通用 `unwrap` 或 `flatMap` 内建函数；用 `match` 明确处理所有预期分支。

## 具名调用与 data 构造器

内建函数按表中的真实参数名接受具名值；data 构造器按声明中的字段名接受具名值。`std/*` 对内建函数的别名保留相同参数名，例如 `std/string` 的 `join` 仍是 `sep/xs`。构造器和内建函数同样拒绝位置加具名重复绑定。

```chaos example=stdlib-native-keywords
data Point { Make(x, y) }
let point = Make(y=3, x=2);
[match point { Make(x, y) => x + y },
 pow(base=2, exponent=3), get(obj={answer: 42}, key="answer"),
 join(sep="-", xs=["a", "b"])]
```

结果是 `[5, 8, 42, "a-b"]`。保留字也可出现在具名标签位置，因此 `map(fn=..., xs=...)` 合法；参数标签仅表示名字，不会先求值同名变量。

## 效果入口

| 全局函数 | 行为 |
| --- | --- |
| `tool(name, value)` | name 为 String，调用当前执行上下文获授权工具；返回其结果 |
| `workflow(graph, value)` | 在授予工作流效果的上下文中执行图，返回图输出 |

`agent name { ... }` 是语言声明语法，不是标准库函数。以上效果在纯 `Framework.evaluate` 中不可用。扩展回调的输入输出经过值边界检查，能力与取消由运行时管理；配置步骤见[SDK](SDK.md)。

## 标准模块完整导出索引

| 模块路径 | 完整导出 |
| --- | --- |
| `std/iter` | `map, filter, flat_map, take, drop, take_while, drop_while, enumerate, zip, chain, batches, scan, amap, afilter, aflat_map, atake, adrop, atake_while, adrop_while, aenumerate, azip, achain, abatches, ascan, from_sync, count, repeat` |
| `std/list` | `map, filter, fold, range, length, head, tail, append, reverse, sort, zip, flatten, any, all, take, drop` |
| `std/string` | `upper, lower, trim, split, join, length, contains, replace, slice` |
| `std/math` | `pi, e, abs, min, max, sum, floor, ceil, sqrt, pow, round, fraction, complex, conjugate, numeric_encode, numeric_decode, decimal, decimal_text, decimal_context, get_decimal_context, decimal_context_info, decimal_clear_flags, decimal_quantize` |
| `std/record` | `get, set, keys, values, has, merge, remove` |
| `std/json` | `parse, stringify, copy` |
| `std/result` | `Result, Ok, Err, map` |
| `std/option` | `Option, None, Some, map` |
| `std/context` | `closing, aclosing, contextmanager, asynccontextmanager, ExitStack, AsyncExitStack, context_var, context_get, context_set, context_reset, context_bind, context, copy_context, context_copy, context_get_in, context_has, context_items, context_info, context_run` |
| `std/conditions` | `condition, condition_info, handler_bind, restart_bind, restart_case, signal_condition, error_condition, compute_restarts, find_restart, invoke_restart, restart_info` |
| `std/swarm` | `create, step, frames, run` |
| `std/evolve` | `create, run` |
| `std/generic` | `generic_function, generic_method, generic_remove, generic_methods, generic_info, generic_next_available, generic_eql, generic_eql_info` |
| `std/hash` | `hash, hash_map, hash_get, hash_set, hash_remove, hash_has, hash_items, hash_keys, hash_values, hash_info` |
| `std/contracts` | `contract_ref, contract_recursive, contract_list, contract_tuple, contract_record, contract_union, contract_optional, contract_map, contract_accepts, contract_info` |

`std/evolve` 的 `create(base,strategy=null)` 与冷异步 `run(snapshot,task,operators,mode="parallel",k=1,observe=callback)` 组合提出、真实探测、组合及跨任务学习。具体回调、状态、资源限制和持续示例见[Harness 持续改进](HARNESS_EVOLUTION.md)；源码采用仍走 Program／Process 的既有测试与权限边界。

`std/swarm` 的四个导出是同一 AST 执行的真实语言函数，step／run 是冷异步函数，frames 是异步生成器。快照、循环拓扑、负权重、TaskGroup 屏障、冷任务所有权移交与回调合同见[群体非线性函数协议](SWARM.md)。它不会新增宿主 Swarm 对象或更改 Team 能力树。

`apply, compose, partial, str, number, type_of, tool, workflow` 仅作为全局内建提供。`slice` 和 `contains` 的全局版本可用于多种容器；即使通过 `std/string` 导入，也保留相同实现。没有 `std/io`、任意文件导入或自动下载第三方模块。

## 挂起值和任务操作

| 调用 | 结果与用途 |
|---|---|
| `next(handle)` | `{done, value}`，逐项恢复 Generator |
| `send(handle, value)` | 恢复同步 Generator，并把 value 送回 yield 表达式 |
| `throw_into(handle, value)` | 在同步流暂停点注入语言异常，可由 catch 处理 |
| `close(handle)` | 关闭生成器，返回 null |
| `collect(handle)` | 有界地收集产出值到 List |
| `anext(handle)` | 冷 Task，完成值为 AsyncGenerator 下一项的 `{done, value}` |
| `asend(handle, value)` | 冷 Task，向异步流发送 value 并等待下一项 |
| `athrow(handle, value)` | 冷 Task，在异步流暂停点注入语言异常 |
| `aclose(handle)` | 冷 Task，完成异步流关闭，结果 null |
| `acollect(handle)` | 冷 Task，有界收集异步流产出值，结果 List |
| `spawn(task)` | 启动当前运行内的 Task，返回原任务 |
| `gather(tasks)` | 返回按输入顺序收集结果的 Task |
| `cancel(task)` | 请求单个 Task 取消，返回是否接受请求 |

这些句柄由 `fn*`、`async fn` 与 `async fn*` 创建；Task 的完成值用 `await` 取得。生命周期、失败及权限继承见 [生成器与异步](EFFECTS.md)。它们不是 Python 对象导入接口。

## 对象与名义类型

| 调用 | 行为 |
|---|---|
| `isinstance(value, klass)` | 判断实例是否属于 klass 或其子类；klass 必须是真实类值 |
| `issubclass(child, base)` | 按类身份和 C3 继承关系判断子类型 |
| `class_of(value)` | 返回 Instance 的真实类值，或 Class 的真实元类 |
| `super(klass, instance)` | 返回从实例 MRO 中 klass 后继续查找的代理 |
| `getattr(value, name)` | 普通属性读取，可由实例／元类钩子接管；默认声明方法与描述符沿原绑定规则，property 读取可执行 getter |
| `setattr(value, name, item)` | 按同一成员写入与类型契约规则赋值 |
| `hasattr(value, name)` | 查询受支持对象是否拥有成员 |
| `vars(value)` | 返回 Class 自有成员或 Instance 自有字段的新 Record；不含继承默认，不执行方法 |
| `dir(value)` | 显式调用 __dir__ 或原目录；同步消费 String 名称，保留重复并排序 |
| `default_getattr(value, name)` | 跳过属性钩子，沿原描述符／存储读取 |
| `default_setattr(value, name, item)` | 跳过写入钩子，沿原描述符／存储／契约赋值，返回 item |
| `default_delattr(value, name)` | 跳过删除钩子，沿原默认删除，返回 null |
| `default_dir(value)` | 原有排序成员目录，不执行用户属性／目录钩子 |

这些函数只操作 Chaos 支持的对象，不是访问任意 Python 对象的反射入口。成员默认值的共享规则、只读约束和调用协议见[对象与组件](OBJECTS.md)。

## 类创建与成员元信息

`new_class(name,bases=[],members={},options={})` 和声明共享创建合同；`class_member(value,mutable=false,method=false,contract="Any")` 显式配置字段／方法，`class_members(klass)` 提取自身声明快照，`alloc_instance(klass)` 仅分配实例。__new__ 与 __init_subclass__ 仍走原求值器。0.66 的 `Type` 是一等原生默认元类，`class_of(Class)` 返回元类；`metaclass` 选项可使用 Type 子类或原生可调用工厂，通用 new_class 返回值按协议决定。完整签名与边界见[类创建协议](CLASS_CREATION.md)及[原生元类](METACLASSES.md)。

## 装饰器和内置描述符

| 调用 | 行为 |
| --- | --- |
| `staticmethod(value)` | 包装可调用值；通过类、实例或 super 读取时不插入接收者 |
| `classmethod(value)` | 包装可调用值；读取时绑定实际访问类，包含派生类 |
| `property(fget=null, fset=null, fdel=null)` | 建立有界、同解释器所有的属性描述符；getter、setter 或 deleter 可为 null 表示缺失 |
| `descriptor.getter(function)` | 创建替换 getter 的新 property，保留其它访问器 |
| `descriptor.setter(function)` | 创建替换 setter 的新 property，保留其它访问器 |
| `descriptor.deleter(function)` | 创建替换 deleter 的新 property，保留其它访问器 |

描述符可存入类成员，也可用作装饰器结果。`type_of` 对以上包装返回 `Descriptor`；`Descriptor` 是运行期类型契约。静态方法包装自身也可调用；classmethod/property 必须通过成员访问绑定或触发。`super(klass, instance)` 的 instance 参数也接受属于 klass 子类的 Class 接收者，供类方法合作继承。

`getattr` 和 `hasattr` 的默认读取可执行实例 property getter，外层属性钩子可显式委托或替换读取；hasattr 仅将属性缺失错误转为 false；其它异常、取消和资源限制仍传播。`setattr` 和成员赋值走同一个 setter，完成值仍为赋入的值。`vars`、`default_dir`、结果序列化和调试预览不会执行属性钩子。显式 `dir` 可执行 __dir__；五钩子及元类委托规则见[属性协议](ATTRIBUTE_PROTOCOL.md)。

## 用户属性访问与删除

`delattr(value, name)` 接受 Class 或 Instance 和字符串成员名，完成值为 null。它删除实例的自有可写状态或自身可变类成员；不会删除继承类的字段。普通实例覆写删除后重新显露类默认值。数据描述符交给 `__delete__` 或 property 的 fdel；缺失删除器会报属性错误。仅 getter 的非数据描述符允许实例缓存值及删除缓存。

`Descriptor` 契约也接受类型上存在 `__get__`、`__set__`、`__delete__` 中至少一种协议的用户实例；其 `type_of` 仍为 Instance，`class_of` 和名义类型检查保持原身份。仅 `__set_name__` 的值不是 Descriptor。

`getattr`、`hasattr`、`setattr` 与 `delattr` 均通过当前语言任务调用用户协议；它们不是纯反射。Class 上的用户 getter 接收 null 和实际访问类，而内置 property 的 Class 读取仍返回属性自身。`property.fget`、`.fset`、`.fdel` 仅返回存储的访问器。完整规则见[用户描述符](DESCRIPTOR_PROTOCOL.md)。

`floordiv` 和 `bool` 是全局内建，本轮没有将它们加入现有标准模块导出。`NotImplemented` 是全局哨兵常量；JSON 输出为惰性的 `{"$notImplemented":true}` 描述记录，parse_json 得到的普通 Record 不能恢复哨兵身份。`contains` 对 Instance 没有迭代器回退。`if`、`while`、守卫、`!`、`&&`、`||` 保持严格 Bool，不自动调用 `bool`。完整规则见[运算协议](OPERATORS.md)。

## 用户迭代器内建

| 内建 | 行为 |
| --- | --- |
| `iter(value)` | 为 List/Tuple/Set/String 建立新游标；Generator/Iterator 保持自身；调用用户 `__iter__` 工厂并验证结果 |
| `aiter(value)` | AsyncGenerator/AsyncIterator 保持自身；调用用户 `__aiter__` 工厂并验证结果，不隐式等待 Task |
| `next(handle)` / `anext(handle)` | 推进对应生成器、迭代句柄或带对应 step 协议的实例；同步返回 Record，异步返回冷 Task |
| `collect(handle)` / `acollect(handle)` | 有界收集对应可迭代来源的产出值，不包含完成值 |
| `send` / `asend`、`throw_into` / `athrow` | 原有生成器双向 API 也接受显式定义了相应协议的用户迭代器 |
| `close(handle)` / `aclose(handle)` | 关闭对应句柄，用户对象只调用明确的 `__close__` / `__aclose__` 特殊协议 |

所有推进结果保留 `{done:Bool,value:Any}` ABI。async 内建操作返回的 Task 必须显式等待；工厂本身不自动 await。原始值、普通 Record 或序列化句柄标记不能伪装成活动句柄。0.34 的 map/filter/fold/any/all、apply 与调用星号展开也已接入同步协议；其他列表专用契约不变。详见[用户迭代器](ITERATORS.md)。

## 条目与切片函数

`getitem(value,key)` 读取条目，`setitem(value,key,item)` 和 `delitem(value,key)` 显式调用实例写入／删除协议并要求返回 null。`slice_key(start=null,stop=null,step=null)` 构造 Slice；`slice_indices(spec,length)` 归一化为 `[start,stop,step]`，不分配全部索引。Slice 的三字段只读，保存 Int/null，端点实例经 __index__ 转换，步长不得为零。index(value) 返回无损 Int；见[协议合同](INDEX_PROTOCOL.md)。协议不自动迭代、等待 Task 或扩大权限；见[索引指南](INDEXING.md)。

## 智能体定义值（0.20）

| 函数 | 返回与行为 |
| --- | --- |
| `agent_from(definition)` | 严格验证 `{kind:"agent",name,config}`，新建当前解释器拥有的 Agent，满足 Function |
| `agent_info(value)` | 返回公开定义深拷贝，不执行 Agent |
| `agent_source(value, name="saved_agent")` | 返回普通可导入模块源码，不执行 Agent |

派生角色使用 `merge` 组合定义并再次 `agent_from`，不会改变原值。默认参数、字段边界和旧配置兼容规则见[智能体定义值](AGENT_VALUES.md)。

## 团队与角色（0.21）

| 函数 | 行为 |
| --- | --- |
| `team_from(definition)` | 纯验证，构造当前解释器拥有的 Team |
| `team_info(value)` | 完整定义副本 |
| `team_source(value,name="saved_team")` | 普通语言模块源码 |
| `team_role(value,role)` | 单输入的角色 Function，调用返回冷 Task |
| `team_bindings(value)` | 新的 role id→Function Record，可传给 Plan／Goal |

`Team(input)` 调用入口角色并返回冷 Task。实际效果须 await／调度，继承当前能力和预算；完整合同见[团队指南](TEAMS.md)。

## Program 反射和演化（0.22）

`program_from(definition)`、`program_info(value)`、`program_metadata(value)`、`current_program()`、`program_ir(value,file=null)`、`program_edit(value,file,operations)`、`program_source(value,name="saved_program")` 是构造和反射接口。`run_program(value,input=null)`、`program_probe(value,cases)`、`program_adopt(base,candidate,cases,reason="")` 返回冷 Task。Program 的一元调用等价于 run_program。

定义、测试输入、采用权限和运行限制见 [Program 参考](SELF_PROGRAMMING.md)。同步描述符写入不隐式等待 Program。

## 持久 Process

`process_from`、`process_info`、`process_source` 构造／反射不可变定义；`process_step` 纯构造状态转换记录。`process_start`、`process_send`、`process_status`、`process_control`、`process_configure`、`process_reconcile` 创建冷效果。详见[精确签名及边界](PROCESSES.md)。

## 原生会话内建

`session_open(key,agent,context_limit=100000)`、`session_load(key)` 冷返回 AgentSession；`session_info(value)` 纯复制元数据。句柄 `(input,message_id=null)` 冷返回提交回执。`session_status(key)`、`session_history(key,after=0,limit=100)`、`session_fork(key,revision,new_key,agent=null)`、`session_compact(key,revision,keep_turns)`、`session_close(key,revision)`、`session_reconcile(key,revision,evidence)` 使用同一宿主与显式权限。详见[AgentSession](SESSIONS.md)。


## 电脑观察标准函数

computer_open、computer_connect、computer_release、computer_reconcile、computer_info、computer_observe、observation_info、computer_action、action_info、computer_act、computer_close、observation_input 见[签名与语义](COMPUTERS.md)。Computer 调用返回行动的冷 Task，JSON 描述不能恢复活资源。

## 程序历史（0.26）

`program_history(after=0,limit=50)` 返回历史元数据页的冷 Task，limit 为1–100；`program_revision(version)` 返回同项目精确历史 Program 的冷 Task。没有宿主的纯环境不能执行读取，probe 与 Skill 录制也不允许。取回值沿用 program_info／program_metadata／program_probe／program_adopt；见[程序历史](PROGRAM_HISTORY.md)。

## 资源上下文模块

`import "std/context" as Context` 导入以 Chaos 类实现的 `closing(value, closer=close)` 与 `aclosing(value, closer=aclose)`。默认函数关闭同步／异步流，自定义资源显式传入 closer。它们配合 with／async with 使用，不改变既有 close/aclose 契约。详见[资源作用域](CONTEXTS.md)。

## 任务状态与并发收束

`task_group() -> TaskGroup` 创建词法任务组；`async with group as workers { ... }` 进入一次；`workers.spawn(task:Task) -> Task` 接纳并启动冷任务。`task_group_info(group:TaskGroup) -> Record` 读取组状态与有界成员。`task_info(task)` 返回含 groupId 的有界调度元数据 Record；它不启动、等待、取消或观察任务结果。gather 在失败时取消并排空其成员，保留硬控制；详见[任务规范](TASKS.md)。

## 消息通信

`channel(capacity:Int=0) -> Channel`；`channel_send(channel:Channel,value:Any) -> Task`（完成值 null）；`channel_receive(channel:Channel) -> Task`（完成值 `{done:Bool,value:Any}`）；`channel_close(channel:Channel) -> Bool`；`channel_info(channel:Channel) -> Record`。发送与接收创建冷任务；await／spawn 才进行队列操作。Channel 可传给 aiter、async for 和 acollect，消费游标的关闭不关闭共享通道。签名、状态字段、FIFO 与取消边界见[通道规范](CHANNELS.md)。

## 原生计时与任务等待

| 函数 | 类型与作用 |
| --- | --- |
| `sleep(seconds:Number)` | Task；启动后等待非负有限秒数，完成 null |
| `task_wait(tasks:List,until:String="first",timeout=null)` | Task；完成为 `{done:List,pending:List,timedOut:Bool}`，列表元素是原 Task |

任务以首次出现去重；first／all／error 是明确等待条件。普通结果与失败由调用者 await 读取，超时不会取消成员；真实单调时钟、记录范围和预算边界见[等待指南](WAITS.md)。

## 原子消息选择

| 函数 | 返回 |
| --- | --- |
| `send_case(channel:Channel,value:Any)` | ChannelCase；捕获发送模板，不执行发送 |
| `receive_case(channel:Channel)` | ChannelCase；描述接收，不消费消息 |
| `case_info(case:ChannelCase)` | Record；id、op、channelId、location，不含正文 |
| `channel_select(cases:List,timeout=null)` | Task；完成为 `{index,op,done,value,timedOut}` |

候选列表非空，超时为有限非负 Number 或 null；全局配额包括可复用模板和冷选择捕获。详细规则见[原子选择](SELECT.md)。


`generator_context(generator, asynchronous=false)` 将当前执行周期未启动的 Generator／AsyncGenerator 接管为单次资源管理器；通常使用 `std/context` 的 `contextmanager`／`asynccontextmanager` 装饰资源工厂。恰好一次 yield、异常转发、清理与类型边界见[函数定义资源](CONTEXTS.md#用函数定义资源contextmanager)。


`exit_stack(asynchronous=false,max_entries=1024)` 创建原生 ExitStack／AsyncExitStack；推荐通过 `std/context` 的同名工厂使用。动态进入、回调、错误处理、pop_all 和关闭接口见[资源栈规范](RESOURCE_STACKS.md)。

## 语言函数工具

`agent_tool(name,handler,description="",input_type="Any",output_type="Any",capabilities=[])` 构造 AgentTool；`agent_bind(value,tools)` 构造调用时返回冷 Task 的 BoundAgent。`agent_tool_info` 和 `agent_binding_info` 返回隔离合同数据。工具输入输出使用 JSON 兼容的语言类型；完整契约见[函数工具](FUNCTION_TOOLS.md)。

## 词法取消保护

`cancel_shield() -> CancelShield` 创建一次性词法管理器，通过 with／async with 在活动 Task 中进入。`cancel_shield_info(shield:CancelShield) -> Record` 返回 id、state、taskId、location、cancellationRequested、cancellationDeferred、depth。最外层范围退出时交付延迟的局部取消；运行级取消、预算和硬错误不被屏蔽。句柄不能由资源栈动态接管，活动保护状态不自动继承给子任务；完整示例见[取消语义](CANCELLATION.md)。

## 元组构造与转换

`tuple(items=[]) -> Tuple` 从同步可迭代值构造元组，`collect(tupleValue)` 显式获得 List。元组支持 length、bool、contains、切片、迭代消费和同类连接；原有 append、sort 等 List 专用合同不变。`json_stringify` 输出无行为的 `$tuple` 数据描述，parse_json 不恢复原生类型。见[元组](TUPLES.md)。

## 集合函数

| 函数 | 合同 |
| --- | --- |
| `set_of(items=[]) -> Set` | 逐项消费同步可迭代值并去重；默认空集合，不隐式 await |
| `set_add(value: Set, item) -> Set` | 返回含 item 的集合，不改变原值 |
| `set_discard(value: Set, item) -> Set` | 返回去除 item 的集合；没有该成员时保留原成员 |
| `set_is_disjoint(left: Set, right: Set) -> Bool` | 是否没有共同成员 |

Set 支持 length、bool、contains、同步迭代和 `| & - ^ < <= > >=`，成员接受原生纯键与具有同步哈希／相等协议的 Instance，Tuple／Set 可递归作为键；纯宿主／宏模式仍无钩子，协议集合受所有权与能力约束。`collect` 显式转换为 List，json_stringify 用 `$set` 数据描述。原有 `set(record,key,value)` 保持 Record 更新合同。见[集合](SETS.md)。

## 原生编译函数

`compile_function(fn:Function,target="auto") -> Function` 创建保留调用合同的一等函数；`target` 可为 auto、native 或 interpreter。`compile_info(fn:Function) -> Record` 读取实际后端、范围守卫与原生／解释调用计数。可选 LLVM 后端支持受约束的纯整数与比较表达式区域，并保留局部绑定及分支的解释器控制流，不编译模型／工具效果；无后端或守卫失败时的行为见[原生函数](NATIVE_FUNCTIONS.md)。

## GPU 数值 Task

`gpu_map(fn:Function,values:List,*,device="auto",compiler="tensor") -> Task` 将单参数 Number／Any 纯数值函数应用于有限数值列表，支持[受限局部控制流](GPU_CONTROL_FLOW.md)；`gpu_matmul(left:List,right:List,*,device="auto") -> Task` 执行矩形二维矩阵乘法；`gpu_devices() -> Task` 检测可用设备。完成值是普通 Record，数值结果包含 values、实际 device、backend、固定 float32 dtype 和 fallbackReason。auto 可明确 CPU 回退，gpu／mps／cuda 请求不可用时抛错。它们不自动改变普通 Int 的精度；规模、纯度与取消边界见[GPU 指南](GPU_COMPUTE.md)。

## GPU 编译证据

`gpu_map` 新增仅关键字参数 `compiler="tensor"`，可选 auto／metal；默认计算及五字段返回不变。`gpu_kernel_info(task:Task) -> Record` 返回 requestedCompiler、compiler、status、irHash、sourceHash、sourceCacheHit、dispatches、isolated、fallbackReason。未执行时尚无编译与派发证据，相关字段为 null；它不启动或观察 Task，且仅接受同轮原 gpu_map Task。详细字段和失败合同见[融合 Metal](GPU_METAL.md)。

0.68 的 `isinstance(value,klass)`／`issubclass(child,base)` 通过实际元类判断并同步返回严格 Bool；默认 Type 提供显式名义回退。高层 new_class／class 支持 __mro_entries__，低层 Type 仍只接受 Class 基类。见[类型协议](TYPE_PROTOCOLS.md)。

## 精确数值与复数

fraction(numerator:Int=0, denominator:Int=1)、complex(real:Real=0, imag:Real=0)、conjugate(value:Numeric)、numeric_encode(value:Numeric)、numeric_decode(value:Record) 都是普通内建函数，并从 std/math 导出。sum 接受 Numeric 列表；min／max／sort 接受 Real 列表，floor／ceil／round／sqrt 接受 Real，number 显式转换 Fraction 为有限 float。Number 的历史契约不包含新数值类型；完整提升、只读成员、codec 和限制见[数值指南](NUMBERS.md)。

std/math 同时导出 decimal、decimal_text、decimal_context、get_decimal_context、decimal_context_info、decimal_clear_flags、decimal_quantize。sqrt(Decimal) 返回 Decimal；运行期上下文与纯宏边界见[Decimal](DECIMALS.md)。


## std/conditions

`std/conditions` 导出 `condition`、`condition_info`、`handler_bind`、`restart_bind`、`restart_case`、`signal_condition`、`error_condition`、`compute_restarts`、`find_restart`、`invoke_restart`、`restart_info`。这些是共享 NativeSpec 的原生语言功能，详见[情况处理与恢复出口](CONDITIONS.md)。


## 原生可调用 Bot

`bot(goal,planner,execute,verify,context=null,max_iterations=20)` 创建原生 Bot，`bot_info(bot)`／`bot_context(bot)` 复制状态，`bot_reconcile(bot,context,evidence)` 显式协调原 blocked 身份。四个接口由 `std/bot` 原样导出；当前模块目录由运行时标准库与 IDE 导入补全共同提供。`Bot(input=null)` 是原调度器的冷 Task，await 返回 `{done,status,state,output}`。回调合同、共享状态、准入 revision、完成前缀与硬取消边界见[原生 Bot](NATIVE_BOTS.md)。


0.109 的 `std/bot` 同时导出 `process_guide` 的原 Native 别名，用于普通 Process 的持久指导；模块仍保留原 bot／bot_info／bot_context／bot_reconcile 身份，未新增独立 Bot 应用或自动恢复闭包。


## std/memory：版本化共享记忆

`read(key:String) -> Record` 和 `compare_exchange(key:String, expected_revision:Int, value) -> Record` 是真正的 Chaos 函数，分别调用 `memory.read` 与 `memory.compare_exchange` 工具；需要项目及当前函数／角色拥有对应能力。它们可被普通高阶函数、Bot、AgentTool 和 Program 组合，不新增另一套调度器。

读取返回 `{key, exists, value, revision}`；按版本提交返回相同字段及 `swapped:Bool`。只有版本匹配才在同一 SQLite 事务写入值并加一，冲突返回当前值和版本、`swapped:false`，不运行用户回调或自动重试。键为1至128字符；版本为0至9007199254740991的严格 Int，Bool不接受；达到上限后写入失败且数据不变。新提交值是有界JSON数据，编码后至多100000字符。

不存在的键为 `{exists:false,value:null,revision:0}`；升级前已经存在、尚无版本记录的键也可为版本0，因此**版本0不是不存在或只创建的条件**，应同时检查 `exists`。存储的null仍为 `exists:true`。旧 `memory.get` 和 `memory.put` 输入输出保持，旧put也原子增加版本，即使写入相同值。无删除接口、自动记忆整理或隐式回放。

```chaos
import "std/memory" as M;
let cell=M.read("progress");
let candidate={completed:42};
let receipt=M.compare_exchange("progress",cell.revision,candidate);
if receipt.swapped { receipt.value } else { {conflict:true,current:receipt} }
```

CAS只保证该键的值和版本一起提交，不把外部工具动作或Process根检查点变成同一事务。提交后任务失败／取消不会回滚已提交记忆；恢复者先重新读取当前版本、结合证据决定后续处理，不能自动重做有外部效果的更新函数。

## std/process

该模块导出 process_from、process_info、process_source、process_start、process_send、process_guide、process_message、process_status、process_events、process_control、process_configure、process_reconcile、process_step、process_evolve、process_evolution_info 的原生别名；没有包装器或额外权限。`P.process_message(key,message_id)` 返回原冷 Task，等待得到精确消息关联记录；普通消息和指导身份保持分离。std/bot 也导出 process_message。详情见[持久 Process](PROCESSES.md#按消息-id-读取结果)。

## 命令行与内置智能帮助

可以直接使用 `chaos -c '40+2'`、`chaos main.chaos`、`chaos repl`；目录包对应 `python3 chaos.py -c '40+2'` 等入口。`chaos ask` 调用内置语言智能体，REPL 中 `.ask 问题` 随时请求帮助。自己的程序可导入 `std/intelligence` 组合规划、执行、复核与助手；`chaos assist` 连接网页 IDE 的同一个编程助手，查看事件、检查提案并显式应用。安装 PATH 命令、配置来源、使用范围和完整例子见[命令行教程](COMMAND_LINE.md)。
