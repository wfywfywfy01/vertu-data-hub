"""幂等注册 data_source。跑一次就够，重复跑会 upsert 而不是报错。

用法：
    python -m app.cli.register_source
"""
import asyncio

from app import db
from app.catalog import registry

# 本轮验证用的初始数据源清单。以后新增数据源，在这里加一项（或直接用
# registry.upsert_data_source 写一次性脚本/交互式添加），不需要改任何 connector 代码。
SOURCES = [
    dict(
        code="policy_product_docs",
        source_type="file",
        display_name="政策/产品文档",
        description="陈列规范、产品手册等政策/产品类文档，走 RAG 检索",
        config={"path": "政策产品文档", "handler": "doc_rag", "default_tags": {"doc_type": "policy"}},
    ),
    dict(
        code="store_display_media",
        source_type="file",
        display_name="门店陈列/装修图片",
        description="门店陈列照片、装修图纸，走图片向量检索",
        config={"path": "陈列装修图片", "handler": "image", "default_tags": {"image_type": "display"}},
    ),
    dict(
        code="sales_history_files",
        source_type="file",
        display_name="销售历史数据（批量文件）",
        description="历史销售 Excel 等结构化数据批量导入，精确查询用",
        config={"path": "销售历史数据", "handler": "tabular", "dataset_code": "sales_history_import"},
    ),
    dict(
        code="unclassified_inbox",
        source_type="file",
        display_name="其他-待定（人工分类信箱）",
        description="分不了类的文件，不自动入库，只记日志提醒人工处理",
        config={"path": "其他-待定", "handler": "unclassified"},
    ),
    dict(
        code="vps_daily_sales",
        source_type="skill",
        display_name="公司总销日报（vertu-cli sales +headline-kpi）",
        description="通过 vertu-cli 取公司总销 KPI 快照",
        config={
            "domain": "sales",
            "shortcut": "+headline-kpi",
            "params": {"period": "today"},
            "dataset_code": "headline_kpi",
            "summarize": True,
        },
    ),
    dict(
        code="odoo_sale_view",
        source_type="db",
        display_name="Odoo 销售数据（预留，未启用）",
        description="Odoo 销售相关 public 视图。当前始终走 vertu-cli/skill 取数，"
        "本行只登记目录、不直连，enabled=false 直到确认要开直连权限",
        config={"dsn_env": "ODOO_DATABASE_URL", "exposed_datasets": [
            {
                "dataset_code": "odoo_sale",
                "display_name": "Odoo 销售明细视图",
                "description": "占位登记，未实际直连；实际取数走 vps-daily-sales-report 等 skill",
            }
        ]},
        enabled=False,
    ),
]


async def main() -> None:
    for s in SOURCES:
        row = await registry.upsert_data_source(
            code=s["code"],
            source_type=s["source_type"],
            display_name=s["display_name"],
            config=s["config"],
            description=s.get("description"),
            enabled=s.get("enabled", True),
        )
        print(f"registered: {row['code']} (id={row['id']}, enabled={row['enabled']})")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
