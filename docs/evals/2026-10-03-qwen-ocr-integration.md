# Qwen-OCR 第二线路接入与合成验证

用户选定华北2（北京）、`https://dashscope.aliyuncs.com/compatible-mode/v1`、`qwen-vl-ocr`。2026-10-03 实际鉴权及图片识别成功。本轮只发送程序生成的合成图片，没有上传原始票据或校对标签。

## 配置及兼容性

- `OCR_PROVIDER=internvl|qwen` 明确选择线路，代码默认 internvl。本机 `.env` 已选择 qwen；原 InternVL URL/model 保留。没有自动尝试原离线服务，也没有隐式故障切换。
- 云端配置独立使用 `QWEN_OCR_BASE_URL`、`QWEN_OCR_MODEL`、`QWEN_OCR_API_KEY`、`QWEN_OCR_TIMEOUT_SECONDS=60`、`QWEN_OCR_MAX_TOKENS=4096`。
- 用户临时存入 `OCR_API_KEY` 的新百炼密钥移到 `QWEN_OCR_API_KEY`，本地线路密钥置空，避免把云密钥发送到本地旧端点。`.env` 被 Git 忽略，没有输出或提交密钥。
- Agent 仍使用现有一个 OCR 工具。共享 `recognize()` 保持 rows/raw/latency_ms，追加 provider/model/usage 元数据；上传接口、聊天工具、原 OCR 工具共享这层线路选择。
- InternVL 保留 CSV 提示与解析。Qwen 请求 JSON 八字段，字段值要求字符串，允许 null 转为空字符串，不重算票面金额，不改变负数/折扣；可解析 JSON 代码块。
- JSON 缺字段、多字段、非文本值、空行、响应结构错误及输出截断会拒绝，不返回可入库行。缺云密钥时不发请求。HTTP/网络错误不回显服务响应体或异常详情，避免将密钥或服务回显内容暴露到聊天。
- 结果依旧待确认，失败/空结果不会进入后续入库步骤；自动确认没有增加。现有入库覆盖旧行的多步操作未改为跨步骤事务，因此不宣称解决任意数据库写入中途失败的原子性问题。

## 测试结果

修改前新增接口/校验测试 7 failed、1 passed，证明旧实现缺少云端选择和严格校验。实现后全部 71 项测试通过（原 62 项 + 9 项新增）；仍有两条既有 pytest 配置/Starlette 弃用警告。新增测试验证独立鉴权、原 CSV 契约、负数票面金额保留、格式/截断拒绝、密钥缺失、错误信息不回显密钥，以及坏结果不删除同名已有记录。

三张合成图片先固定字段，再调用真实 API，各一次，不重跑挑选成功结果：

| 图片 | 预期行数 | 正确字段 / 字段数 | 行数与待确认入库 | 单用例耗时 |
| --- | --- | --- | --- | --- |
| 中文清晰表格 | 2 | 16/16 | 通过 | 9.934 s |
| 折扣、数量单价与票面金额不一致、赠品零金额 | 3 | 24/24 | 通过 | 4.692 s |
| 日文公司、供货商与商品名称 | 2 | 16/16 | 通过 | 3.010 s |

合计 7 行、56/56 字段匹配。文字仅去首尾空白，数值用 Decimal 比较（忽略千分位逗号）；字段与行按返回顺序对齐。不是逐字符 CER，也不是原始发票总体准确率。

表中耗时包含识别、结果评分及临时数据库入库检查，第一项还包含首次导入工具的开销，不能等同纯 OCR 网络时延。仅 3 样本，不报告稳定的 P95。三次直接识别 API 的 usage 合计输入 2415、输出 574 tokens；没有核查账单，不能当作实际费用。聊天流程另有真实 OCR/DeepSeek 调用，其费用没有计入这组 usage。

单独的真实聊天链路：TestClient 上传合成图片 → 真实 DeepSeek 工具选择 → 真实 Qwen 识别 → 隔离 SQLite，两行八字段正确。仅一次 `ocr_recognize_chat` 调用，全部 pending，confirmed_count=0，有最终回答且无 error；7/7 检查通过，用时 12.302 s。不是用脚本替身决定 Agent 动作。直接识别的三张图片入库检查则复用刚得到的真实模型结果，不重复付费调用。

原微调 InternVL 仍离线，没有同数据的修改前质量、延迟或成本基线，因此不能宣称云模型优于原模型。本次证明的是第二线路可用、合成条件下字段正确及真实 Agent 入库链路接通。

## 复现与剩余工作

```powershell
python -m pytest -q
python evals/qwen_ocr_synthetic_probe.py --output data/qwen-synthetic.json --artifacts-root data/benchmark_v1/qwen-ocr-synthetic-2026-10-03
python evals/qwen_ocr_chat_probe.py --output data/qwen-chat.json
```

聊天脚本只读取上述固定合成图 clean.png，需先运行图片生成脚本。测试数据库均为临时目录，不修改正式业务数据。原始图片留在本地忽略目录；公共 JSON 只含合成内容。

原始结果：`2026-10-03-qwen-ocr-synthetic.json`、`2026-10-03-qwen-ocr-chat.json`。实际密集表格、扫描噪声、模糊/旋转、漏行、真实票据质量，以及原模型对照仍需另行评测。当前按用户选择只发送合成图片，真实票据仍未发送。自动故障切换需在质量门槛和允许切换的条件明确后再加入。
