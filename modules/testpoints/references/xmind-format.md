# XMind输出

导图由当前测试点JSON生成，每点出现一次，以模块为中心，按首个关联REQ分组。
节点展示TP编号、名称、优先级和评审状态；备注保留检查、前置条件、数据、预期、全部REQ/AC、真实来源及评审意见。
多个REQ的点在一个分组展示，其余关联保存在备注和覆盖矩阵，不复制成多个点。

没有评审时标未评审。有有效评审时按通过/需确认/不通过展示。
检测到过期结果应先重评，不沿用旧状态。导图用于计划和评审，不能标成实际执行结果。

文件同时提供现代JSON和经典XML主题树：ZIP包含content.json、metadata.json、manifest.json及content.xml。
content.json为sheet数组，rootTopic及子主题使用children.attached，节点ID唯一。中文及特殊字符保留。
现代结构依据 [XMind官方SDK归档代码](https://github.com/xmindltd/xmind-sdk-js/blob/master/src/utils/dumper.ts)和 [官方导图示例](https://github.com/xmindltd/xmind-viewer/blob/master/example/content.json)，经典树和文本备注依据 [官方XML主题实现](https://github.com/xmindltd/xmind-sdk-python/blob/master/xmind/core/topic.py)。两份内容的ID、标题、备注和层次须一致。
导出采用标准库，不需要额外SDK、联网或XMind授权账号。

默认写模块目录中的测试点.xmind，可以用--output指定其他派生文件。
结构和内容在自动检查中核对，交付样例还应由可用的XMind客户端或文件引擎读取并验证编辑保存。
验证针对具体版本，不等于对所有历史客户端的兼容承诺；具体结果和界面检查限制在版本验证记录中注明。
