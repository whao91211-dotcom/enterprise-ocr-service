"""销售单据识别指令构建（简化版只保留 sales 8 列契约）。

模型为 InternVL3 微调（训练数据见 train.jsonl: 8 列 desc,date,from,item,amount,price,tax,sum）。
训练时 prompt 带完整字段定义；但 9052 实测: 过长 prompt 会卡死服务(读超时)。
因此保留可切换模式, 默认用折中方案:
  - train: 逐字复刻训练 prompt（最贴合分布, 但 9052 可能卡死）
  - cols8: 短指令 + 一句话 8 列顺序引导（实测 200 且返回 8 列, 列内容有错位/幻觉风险）
  - short: 旧 6 列短指令（仅存档/对照, 不作为生产）
"""

from __future__ import annotations

# 用户确认的 8 列标签(顺序即输出顺序)
SALES_COLUMNS_8: list[tuple[str, str]] = [
    ("desc", "顾客公司"),
    ("date", "发注日"),
    ("from", "源公司"),
    ("item", "项目"),
    ("amount", "数量"),
    ("price", "单价"),
    ("tax", "税率"),
    ("sum", "金额"),
]

TASK_HEAD = "# 任务描述\n你需要对图像中的销售消息进行结构化信息提取，输出一个标准CSV格式的数据。"


def build_sales_instruction(mode: str = "cols8") -> str:
    if mode == "train":
        # 逐字复刻训练 prompt（含 # 输入图像 结尾；<image> 由客户端作为图片 content part 提供）
        parts = [TASK_HEAD, "# 销售内容字段定义"]
        for _key, label in SALES_COLUMNS_8:
            parts.append(f"{_key}：代表{label}")  # 训练用中文说明见 train.jsonl
        return "\n".join(parts) + "\n# 输入图像"
    if mode == "cols8":
        # S1 实测(2026-09-08): 带中英语义的 8 列引导, from 列识别正确且不卡服务
        # （纯英文 key 引导会把 from 错认成日期; 训练同款长 prompt 会卡死 9052）
        return (
            TASK_HEAD
            + "\n每行输出 8 列，顺序固定为：desc(顾客公司),date(发注日),from(发货公司/源公司),"
            "item(货物名称),amount(货物数量),price(货物单价),tax(货物税率),sum(货物金额)。"
        )
    # short: 旧 6 列短指令（对照/存档）
    return TASK_HEAD
