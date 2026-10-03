# OCR 补充验证：Qwen 路线

日期：2026-10-03。配置：北京区域、qwen-vl-ocr；决策模型 deepseek-chat。
本批只上传程序生成的合成图片，使用隔离 SQLite，没有上传真实票据，没有修改用户业务数据库。

## 结果与证据

| 验证项 | 本次结果 | 证据与限制 |
| --- | --- | --- |
| 中文普通明细、折扣/零金额、日文 | 首轮 2/3 成功，普通明细连接失败；普通明细单独补测 1/1 成功 | synthetic-recheck.json、clean-supplement.json；不可合并声称首轮 3/3 |
| 成功获得输出的三个固定样本 | 7 行、56/56 字段匹配，均待确认入库 | 文本按原文匹配，数字用 Decimal 比较，行按顺序对齐；只有清晰合成表格 |
| 实际图片上传→OCR→查看→修改→排除待确认→确认并统计→绘图→Word | 修复后 7/7 步骤通过 | full-flow-final.json，真实 DeepSeek 决策、真实 Qwen 推理、实际工具和数据库 |
| 超时、连接失败、HTTP 401/429/503、响应结构错误、JSON 错误、截断、零行 | 9/9 注入测试通过 | 真实 OCR 客户端及入库工具，网络响应注入；旧已确认记录保留，客户端仅调用一次，无密钥回显 |
| 重识别中途入库失败 | 修复前旧记录丢失，修复后回滚保留 | SQLite 触发器让第二条插入失败，不是简单替换工具返回值 |
| 成功重新识别 | 同一单据全部替换为新待确认记录 | 防止旧确认状态自动沿用 |
| 未调用修改工具却声称已修改 | 两个回归测试通过 | 一次纠正后执行工具，或重复虚假声明时停止；中文有限模式检测，不覆盖所有改写与多行操作 |
| 自动测试 | 84 passed，2 warnings | 上一批 71 项，本批新增 13 项；测试数量增加不等于业务准确率提升 |
| Word 实际呈现 | 2 页均已渲染并人工式逐页查看 | COM 导出 PDF、PDFium 页面；表格、中文、390.00 总额、数量 6、240/150 柱状图均正常 |

JSON 文件均位于本目录，名称前缀 `2026-10-03-qwen-ocr-`。渲染清单位于 `full-flow-render.json`，实际图片、图表、Word/PDF 在被 Git 忽略的 `data/benchmark_v1/` 下。

## 本批发现和修复

1. 同图重识别的“删除旧行”与“插入新行”原先分开提交。插入失败会留下空单据并丢失旧已确认明细。新增 `replace_recognized_rows`，在一个 `BEGIN IMMEDIATE` 事务中创建/查找单据、删除、插入；失败回滚。工具、聊天、直接 OCR HTTP 入口统一使用。
2. 完整流程中，模型没有调用 `correct_update_row` 就生成了修改后表格并声称已修改。数据库实际保持 340，后续统计及报告也保持 340。新增本轮修改成功证据检查；发现有限中文完成声明无证据时，要求模型执行已授权修改，重复无证据声明则停止。修复后实际金额为 150+240=390，统计、报告一致。
3. 新评测最初把“没有已确认数据”当作应返回总额 0，这是评分错误。改为检查确实查询 2026 年、不带公司限制、工具返回无已确认数据、数据库仍无已确认行、回答说明无匹配；确认后才校验 390。

两个代码问题均先通过失败测试复现，再修复并跑完整测试。评分修正只改变无匹配步骤的判定，不掩盖没有执行修改的实际业务失败。

## 不删除失败记录

- `full-flow.json`：首轮 DeepSeek Connection error，未进入 OCR，0/1 已执行步骤通过。
- `full-flow-retry.json`：复测成功 OCR，但修改未执行；原评分为 3/7（其中“排除待确认”实际正确但原评分错误），后续金额保持 340。这个记录不能解释为 OCR 识别失败。
- `full-flow-final.json`：应用事务与完成声明修复、修正无匹配评分后的独立新运行，7/7。
- `synthetic-recheck.json`：普通明细 ConnectError；另两张图片成功。
- `clean-supplement.json`：只补跑失败的普通明细，16/16 字段匹配。

## 耗时与成本边界

修复后各请求耗时：OCR 上传 11092 ms、查看 1866 ms、修改 3652 ms、排除待确认查询 2945 ms、确认并查询 3215 ms、绘图 5566 ms、报告 4057 ms；请求耗时相加 32393 ms。这包括模型决策、工具、HTTP 和评分等开销，不是纯 OCR 推理耗时。

普通明细补测的客户端 OCR 耗时 2577 ms（单样本，无 P95 或稳定时延结论）。首轮普通明细连接失败等待 33694 ms。
三个成功直接识别样本的 usage 合计输入 2415 tokens、输出 574 tokens；不含完整流程 OCR 和 DeepSeek 调用，未测实际人民币账单。

与上批清晰合成样本的 56/56 相比，本次成功输出仍为 56/56，没有质量提升的证据。事务修复改善的是失败时数据完整性；完成声明检查改善的是执行结果可信度，触发纠正时可能增加一次模型调用，尚无平均耗时/成本对比。

## 尚未完成的质量验证

这说明云端路线在当前小样本和故障注入条件下能跑通，不代表真实票据识别准确率、线上稳定性、自动故障切换或原 InternVL 质量已验证。
原微调 InternVL 服务仍离线。原校对数据集未上传云端，其独立真实 OCR benchmark 仍待合适环境与数据使用范围确定；模糊、倾斜、复杂版式和大表格也没有在本批新增质量测试。

## 复现

```powershell
python -m pytest -q
python evals/qwen_ocr_synthetic_probe.py --output docs/evals/new-ocr.json --artifacts-root data/benchmark_v1/new-ocr
python evals/qwen_ocr_synthetic_probe.py --cases clean --output docs/evals/new-clean.json --artifacts-root data/benchmark_v1/new-clean
python evals/qwen_ocr_full_flow_probe.py --output docs/evals/new-flow.json --artifacts-root data/benchmark_v1/new-flow
python evals/render_artifact_reports.py --manifest docs/evals/new-flow.json --output docs/evals/new-render.json
```

每轮使用新输出路径，保留失败记录。真实调用需要本地环境中已配置的云端和 DeepSeek 密钥，不将密钥写入结果或 Git。
