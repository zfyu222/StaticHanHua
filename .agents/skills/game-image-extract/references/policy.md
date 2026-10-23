# 三级路由策略JSON

该策略用于 `image_workflow.py plan/pipeline --policy`，输入图名与manifest中sources.path一致；也可使用图片像素ID。

```json
{
  "script":"latin",
  "ordinary_score":0.95,
  "negative_samples":20,
  "weak_samples":10,
  "ordinary_samples":10,
  "qwen_batch":100,
  "seed":42,
  "families":{
    "regular_ui":{"kind":"normal","validated":true,"glyph_height_range":[12,30],"evidence":"实际查看哪些校准图片、字体和字号范围，以及核对结果"}
  },
  "images":{
    "menu.png":{"family":"regular_ui"},
    "title.png":{"known_text":true,"risks":["art"]},
    "small_hint.png":{"known_text":true},
    "glitch.png":{"known_text":true,"risks":["glitch"]}
  }
}
```

以上只是结构示例，不能把示例evidence字符串当作完成校准。实际没有查看样本就不填validated=true。ordinary_samples是抽查快速候选的数量；负面抽查按大小分层。极小字直接Agent需可信文字迹象/known_text，不能仅凭图像很小就把所有精灵交给Agent。

0.95/0.6是初始分流值，不是正确率。英语等确定拉丁源文用latin，中文/日文等用any。图像存在高风险标签时不能被高分覆盖。批次大小只分配本地调用工作量，不让超出批次的图自动通过。
