# 转录契约

输入：DOCX、PDF、PNG/JPEG/TIFF/BMP/HEIC/WebP；纯UTF-8 txt/md亦可（带图Markdown按需求解析规则连同资产处理）。多页图片当前只处理OCR引擎可读取的首图，不能声称整组TIFF都已转录；应逐帧拆图再解析。外部资源不联网下载。

输出：input/原件副本（名称包含原件SHA）、input/不可变底稿.md、input/底稿资产/；output/模块/解析.md、images/、解析来源.json、解析报告.md。业务图按内容SHA命名，避免覆盖同名图片。
解析来源含original.file/sha256/format、draft/draft_sha256、blocks（kind、原件location、底稿lines范围）、images及warnings/needs_review。location是PDF页码/表格bbox、Word part/body_index或图名；lines是生成Markdown的真实行号，不是原Word行号。
OCR每行保存文字及引擎置信度、定位；置信度不是准确率。原图可能不含可识别文字，此时仍保存图并标待核对，不能捏造转录。

需核对时由真正读过原件的核对者填写output/模块/解析核对.json：

```json
{
  "schema_version": "1.0",
  "original_sha256": "填写解析来源中当前原件指纹",
  "draft_sha256": "填写解析来源中当前底稿指纹",
  "status": "confirmed_with_issues",
  "reviewer": "实际核对者",
  "notes": "记录查看了哪些页、图及数字，勿套用示例当真实核对",
  "findings": [
    {"status": "uncertain", "notes": "第2页奖励金额辨认不清", "affected_requirements": ["REQ_TSK_003"]}
  ]
}
```

全部确认可用status=confirmed、findings=[]；已处理事项用resolved并说明处理依据，仍不确定用uncertain及受影响REQ。validate_spec要求这些REQ为blocked，其他确认的REQ可继续。核对记录必须对应原件和底稿SHA，修改原件/底稿会使旧记录失效。
不得把篡改底稿算作不可变输入；如需修正，应保留旧底稿，在新解析项目/输入版本中重新转录和核对，随后使用变更流程更新REQ证据与下游测试。

原始API资料：[Apple Vision文字识别](https://developer.apple.com/documentation/vision/recognizing-text-in-images)、[pdfplumber提取与页面渲染](https://github.com/jsvine/pdfplumber)。这些API能力不等于复杂策划案的完整理解。
