# 用户索引、切片与智能体访问

新方言把对象成员与数据项分开：`object.field` 访问成员，`object[key]` 访问数据项。用户实例通过 `__getitem__`、`__setitem__`、`__delitem__` 定义数据项行为；方法内部可以调用 Agent，仍经过当前 Task 的权限、预算和取消检查。

本页示例使用新源码方言。历史项目按文件保存其语言版本，不会在启动时自动改写源码或统一升级。旧方括号含义与迁移见后面的兼容章节。

## 内置容器的索引和切片

新方括号索引对 List／String／Tuple 接受 Int 或通过 __index__ 无损转换的实例，包括负下标；-1 是最后一项。单项越界报错，Bool 不能充当整数。Record 仍只接受 String 键，缺键报错。[HashMap](HASH_MAPS.md)接受原生复合键和用户哈希实例，`mapping[key]` 缺键报 key_error；hash_set／hash_remove 返回新值，不新增方括号原地赋值。

```chaos example=indexing-native
let values=[10,20,30,40];
[values[-1],values[1:3],values[::2],values[::-1],"好!"[::-1],{answer:42}["answer"]]
```

结果为 `[40,[20,30],[10,30],[40,30,20,10],"!好",42]`。切片是左闭右开；缺省 start、stop、step 按步长方向决定边界，越界会截断。步长不能为 0。字符串下标和步长按 Unicode 码点计算，不按可见字形簇计算。

内置 List、Tuple、Record、String 仍采用值式操作，不支持方括号原位赋值或删除。`set(record,key,value)`、`append(list,value)` 等返回新容器。列表切片只复制外层元素引用，不把其中的实例深复制成独立对象。输出受 List 10,000 项、String 250,000 字符等既有预算限制，归一化切片长度在分配前检查。

已有 `slice(value,start,stop=null)` 保留原来的 String／List 行为和参数名；0.53 同时接受 Tuple 并保留 Tuple 返回类型；它不是 Slice 构造器，也没有因为新语法而获得 step 参数。

## 成员与数据项

```chaos example=indexing-members
class Box {
  var answer=40;
  fn __getitem__(self,key) { ["item",key] }
}
let box=Box();
[box.answer,box.["answer"],box["answer"],getitem(box,"answer")]
```

结果为 `[40,40,["item","answer"],["item","answer"]]`。`object.[key]` 是显式的旧成员／容器访问语法，key 可以动态计算。它和点成员访问都不会调用 `__getitem__`，因此类方法、property、用户描述符与 super 仍能明确地按成员规则读取。

实例的 `[]` 和 getitem 首先检查类的 `__getitem__`。只有该槽不存在时，才按原成员访问作兼容回退；这使没有数据项协议的已有实例仍可用字符串方括号访问成员。存在但不可调用的槽、描述符或方法错误都直接传播，不回退。实例动态字段里的同名函数不会替代类协议。

Class 支持下节的类参数化与元类条目协议；Super 与内置 Descriptor 值的方括号仍使用既有成员规则。Record 的键查找不会变成用户属性协议。

## 类参数化与智能体组件工厂

`Factory[参数]` 是原生条目操作，不是文本模板。类的元类若定义 `__getitem__`，优先绑定该槽到被访问的类；否则读取可见的 `__class_getitem__` 并调用一次。继承、元类数据描述符的优先级与普通成员访问一致。只有两者都不存在时，才按历史 String 成员访问回退；存在但不可调用或抛错的协议不触发回退。类自己定义的 `__getitem__` 服务于它的实例，不拦截类参数化。

```chaos example=indexing-class-factory
class Factory {
  fn __class_getitem__(cls, bias:Int) {
    fn work(value:Int)->Int { value+bias }
  }
}
let work=Factory[40];
[work(2),Factory.__class_getitem__(1)(2)]
```

结果 `[42,3]`。普通声明的 `fn __class_getitem__` 自动绑定实际接收类，子类继承时绑定子类；`super` 沿已有 C3 顺序协作调用。`new_class`／`Type`／`class_members` 往返通过相同成员安装规则，`class_member(fn(...),method=true)` 同样自动绑定。显式 staticmethod／classmethod 保持各自规则；`let __class_getitem__=fn(key){...}` 是保存的函数值，不额外插入 cls。

返回值是当前 Evaluator 内真实的函数、Class、Agent 或其他语言值。示例的 work 可以直接交给 `agent_tool`、Team 或 std/swarm；也可以在方法中返回 `agent_bind` 组成的角色。不经过 JSON 重建，不产生第二个解释器，不扩大创建或调用时已有的工具权限。async hook 只返回冷 Task，生成器 hook 只返回流，由调用方显式 await／消费。

```chaos example=indexing-metaclass-items
class RegistryMeta(Type) {
  fn __getitem__(cls, key) { cls.entries[key] }
  fn __setitem__(cls, key, value) {
    cls.entries=set(cls.entries,key,value); null
  }
  fn __delitem__(cls, key) {
    cls.entries=remove(cls.entries,key); null
  }
}
class Components(metaclass=RegistryMeta) { var entries={} }
Components["answer"]=42;
let answer=Components["answer"];
delitem(Components,"answer");
[answer,Components.entries]
```

结果 `[42,{}]`。类写入和删除查找元类的 `__setitem__`／`__delitem__`，沿用同步 Null 合同、效果次序与增强赋值的单次对象／键求值。`__class_getitem__` 只负责读取，不提供隐式写入工厂。元类槽缺失时，语法赋值保留历史成员回退；显式 setitem／delitem 则要求存在对应协议。`Class.[key]` 与 `Class.field` 始终明确访问成员。

索引参数可以是任意合法语言值，包括 Class、Tuple 和 Slice；不自动转成类型参数或调用 __index__，切片端点仍遵守原 Slice 规则。IDE 不运行这些方法：可证明的普通类按声明返回契约显示类型和绑定签名；动态成员、描述符或自定义元类保持保守提示。源码、IR、CLI、SDK 和包内入口共用这一套实现。

此设计采用 Python 数据模型中的[元类槽优先与类方法绑定规则](https://docs.python.org/3/reference/datamodel.html#class-getitem-versus-getitem)，但 Chaos 允许它构造可执行智能体组件；不声称实现 Python typing.Generic、GenericAlias 或 `List[Int]` 类型注解。Super、原生容器和纯宏阶段的原有边界不变。

## 受控 Slice 值

`slice_key(start=null,stop=null,step=null)` 创建不可变 Slice。三个端点接受 Int、null 或通过 __index__ 无损转换的实例，构造结果只保存 Int／null，Bool 被拒绝；构造时检查整数预算和非零步长。切片语法会构造同样的值并交给用户协议。省略端点保留 null，不提前改成某个具体索引。

```chaos example=indexing-slice-value
let spec:Slice=slice_key(step=-1);
[type_of(spec),spec.start,spec.stop,spec.step,slice_indices(spec,5),
 spec==slice_key(null,null,-1)]
```

结果为 `["Slice",null,null,-1,[4,-1,-1],true]`。Slice 相等按三个字段的值比较。字段只读，不转发到宿主 Python 属性。

`slice_indices(spec,length)` 要求 Slice 与经 index 转换后的非负 Int 长度，返回归一化的 `[start,stop,step]`，不分配整个索引列表。负步长时，省略 stop 与显式 -1 不相同：前者可跨过第 0 项，后者表示序列最后一项之前停止。

```chaos example=indexing-reverse-boundary
let values=[10,20,30];
[values[::-1],values[:-1:-1],values[slice_key(0,null,2)],
 slice_indices(slice_key(null,-1,-1),3)]
```

结果为 `[[30,20,10],[],[10,30],[2,2,-1]]`。用户协议收到原始 Slice，可以自行决定其业务含义；内置归一化帮助自定义序列实现一致的边界规则。

Slice 只包含有界标量数据，可以跨执行周期保留；它不是 Task 或迭代器那样的活动句柄。外部 JSON 和调试只提供惰性描述，不暴露宿主对象，普通标记 Record 不会自动恢复为 Slice。类型检查、序列化与预览不会执行索引方法。

## 写入和删除

| 操作 | 实例协议 | 结果与边界 |
| --- | --- | --- |
| `getitem(value,key)`、`value[key]` | `__getitem__(self,key)` | 任意语言值；槽缺失时可按成员规则兼容回退 |
| `setitem(value,key,item)` | `__setitem__(self,key,item)` | 必须有该槽，成功为 null，不回退属性赋值 |
| `value[key]=item` | `__setitem__(self,key,item)` | 有槽时成功为 null；缺槽时兼容原成员写入并保留其赋值返回值 |
| `delitem(value,key)` | `__delitem__(self,key)` | 必须有该槽，成功为 null，不回退 delattr |
| `value.field=item`、`value.[key]=item` | 原成员赋值 | 不调用数据项协议，保持已有成员契约与返回值 |

没有新增 del 语句。setitem／delitem 不为原生 List／Tuple／Record／String 提供原位修改，也不会自动转成 set／remove 的复制操作。

```chaos example=indexing-mutation
class Mapping {
  var items={};
  fn __getitem__(self,key) { self.items[key] }
  fn __setitem__(self,key,item) { self.items=set(self.items,key,item); null }
  fn __delitem__(self,key) { self.items=remove(self.items,key); null }
}
let mapping=Mapping();
let saved=do { mapping["answer"]=42 };
let answer=mapping["answer"];
let removed=delitem(mapping,"answer");
[saved,answer,removed,mapping.items]
```

结果为 `[null,42,null,{}]`。实例保存新 Record，而原来的 Record 不被原位修改。成员名 items 始终通过点访问读取，不会递归进入本类的 `__getitem__`。

```chaos example=indexing-fallback
class OldStyle { var answer=40 }
let value=OldStyle();
let assigned=do { value["answer"]=42 };
let explicit=try { setitem(value,"answer",99) } catch error { "no item setter" };
[assigned,value.answer,explicit]
```

结果为 `[42,42,"no item setter"]`。只有语法方括号赋值保留缺槽成员回退；显式 setitem 适合要求“必须是真正数据项协议”的组件接口。

## 调用、求值次序与效果

协议通过原始类 C3 查找、描述符绑定和统一函数调用执行。取值先求对象，再求 key；切片端点按 start、stop、step 从左到右求值。赋值随后求右侧值，各表达式只执行一次，最后才调用写入协议。

```chaos example=indexing-order
var seen=[];
fn mark(label,value) { seen=append(seen,label); value }
class Target {
  fn __setitem__(self,key,item) { seen=append(seen,"write"); null }
}
mark("object",Target())[mark("start",0):mark("stop",3):mark("step",2)]=mark("value",42);
seen
```

结果为 `["object","start","stop","step","value","write"]`。无效的端点、协议或权限导致错误，不撤销此前已经完成的表达式效果或实例修改。

读取方法可以是普通、async 或生成器函数。async `__getitem__` 只返回冷 Task，需要显式 await；生成器 getter 返回流，需要显式消费。没有隐式等待或物化。

写入和删除必须同步完成为 null。明确声明为 async／生成器的方法在主体执行前被拒绝；描述符绑定本身可能已经执行效果。普通 fn 若返回 Task、生成器或其它非 null 值，同样失败，不隐式等待。普通 fn 内部调用 Agent 则仍可以在当前 Task 内协作挂起。取消和预算复用既有[效果生命周期](EFFECTS.md)，并排空合作式在途效果。

没有 NotImplemented 回退、反向索引方法，也不根据 `__getitem__` 自动获得 iter／contains 的回退行为。

## 宏生成索引语法

```chaos example=indexing-macro
macro window(value,start) {
  quote { let result=unquote(value); result[unquote(start)::2] }
}
let result=99;
let start=0;
[window!([10,20,30,40],start),start,result]
```

结果为 `[[10,30],0,99]`。subscript、set_item 与三个可空端点的 slice 是独立语法节点；旧 index／set_member 仍表示成员访问。模板的词法变量卫生重命名，协议名、记录键及 Slice 的 start／stop／step 标签保持字面身份。所有端点都参与展开、控制作用域验证和来源位置保存。

纯宏可以对纯 List／String／Record 做新的方括号读取、负索引及原生切片；只计算有界内置数据，不调用用户索引或 Agent。纯阶段仍不提供 getitem、setitem、delitem、slice_key、slice_indices 运行期函数。切片 AST 的内部纯算法不意味着可把活动 Slice 对象交给 syntax_literal。

```chaos example=indexing-pure-macro
macro select() {
  let values=[10,20,30,40];
  syntax_literal([values[-1],values[::2]])
}
select!()
```

结果为 `[40,[10,30]]`。生成的原始源码和 IR 仍保留宏声明／调用，展开树单独用于执行。Slice 节点只能出现在新索引读写的 key 位置，不能成为任意独立表达式；需要独立运行期 Slice 值时调用 slice_key。跨模块宏沿用[宏规范](MACROS.md)。

## 实际 Agent 读写链

以下示例使用本地确定性 Registry 提供方，实际经过 Agent 调度与事件记录，不需要外部模型。提供方只接收显式构造的普通数据：

```python
from chaos import Final, Framework, Registry
from chaos.settings import Settings

registry = Registry()
seen = []

@registry.provider("app.index")
def access(request, context):
    context.checkpoint()
    data = request.input
    seen.append(data["op"])
    return Final(data.get("value"))

source = '''
agent worker {provider:"app.index",tools:[],maxSteps:1}
class Remote {
  var value=40;
  fn __getitem__(self,key) { worker({op:"read",value:self.value+key}) }
  fn __setitem__(self,key,item) { worker({op:"write",value:item}); self.value=item; null }
  fn __delitem__(self,key) { worker({op:"delete",value:key}); self.value=0; null }
}
async fn exercise() {
  let remote=Remote();
  let before=remote[2];
  remote[0]=41;
  let after=remote[1];
  delitem(remote,0);
  [before,after,remote.value]
}
await exercise()
'''
with Framework(registry=registry, settings=Settings(":memory:")) as app:
    project = app.create_project("item-agent", source=source, capabilities=[])
    run = app.start(project["id"], project["version"], None, mode="source")
    assert run.result(timeout=5) == [42, 42, 0]
    events = app.events(project["id"], run_id=run.id, limit=300)["items"]
    assert sum(event["type"] == "agent.completed" for event in events) == 4
assert seen == ["read", "write", "read", "delete"]
```

Framework／Registry、API 和异步 worker 的 JSON 边界保持不变，协议闭包不是对外数据。低层受信任同步 Evaluator 回调仍保留既有 live-callable ABI，不构成宿主沙箱。

## 历史源码、IR 与迁移

旧方言的 `object[key]` 与 `object.field` 都表示旧 index，序列直接下标只接受非负 Int；新方言的 `object[key]` 产生 subscript。新源码的 AST／节点 ID 可以因此与旧解析器不同，不能把它描述为所有旧源码重新解析后的 IR 字节不变。

持久项目按文件维护语言版本；保存时只升级被修改的文件，其余文件继续用原方言。打开旧项目和启动服务不自动重写或统一升级源码。修改已有文件时，若实例已有 `__getitem__`，新方括号会启用该协议；要明确保留原访问含义，使用 `object.[key]` 或适合的点成员语法。旧项目的历史运行仍使用其固定源码和方言快照。

旧 `chaos.program/1` 的 index／set_member 节点仍可解码，缺省版本按旧方言解释。格式化旧节点使用明确的 legacy 写法，不把它们误输出成会调用新协议的方括号。新 IR 增加 subscript、set_item 和 slice，并记录语言版本；结构编辑、源码保存与执行必须使用同一版本映射。保存了多个文件的项目可以同时含旧、新方言，宏处理的是各文件已解析的语法树，不根据文本猜测版本。

## 同一执行状态与可视化

条目访问、显式成员索引、切片键转换和赋值的实际目标／键／右值由对象访问帧保存，属性默认委托与描述符共用原语言调用。源代码、结构 IR、Program、智能体工具函数和编译回退没有第二套条目分派；界面仅展示真实执行结果，编辑分析不会推进用户协议。挂起后继续不会重算目标或重放已完成效果，取消仍按原任务和资源规则清理。详见[对象访问执行状态](ATTRIBUTE_PROTOCOL.md#对象访问的执行状态)与[整数协议](INDEX_PROTOCOL.md#整数与条目协议的执行状态)。

## 与 Python 的区别

协议名称与切片传递方式参考 [Python 数据模型](https://docs.python.org/3/reference/datamodel.html#object.__getitem__)和[切片语法](https://docs.python.org/3/reference/expressions.html#slicings)。Python 的 slice 端点可保存任意对象；Chaos 0.63 使用语言原生 `__index__` 无损转换，并将 Slice 限制为转换后的 Int／null，不执行任意宿主转换。详见[整数索引协议](INDEX_PROTOCOL.md)。[Python Slice 说明](https://docs.python.org/3/reference/datamodel.html#slice-objects)。

Chaos 的原生 List／Record 保持值式更新；用户写入协议要求同步 null，不接受任意返回值。缺槽成员回退、显式 `.[key]` 和按文件保留历史方言也是本系统兼容规则。类参数化已支持 `__class_getitem__`，优先级及元类条目规则见上文。Ellipsis、星号索引、自动序列迭代回退和完整 Python／Lisp 生态兼容仍未实现。

## 复合下标

0.53 新方言的 `object[row,column]`、`object[row,]` 将 Tuple 交给用户条目协议，直接组件可含 Slice；对应的括号元组键具有相同语义。原生 List／String／Tuple 不提供多维解释，旧方言与显式成员索引保持不变。完整求值顺序和 IR 约束见[复合索引键](TUPLES.md#复合索引键)。
