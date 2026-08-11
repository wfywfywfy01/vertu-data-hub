"""数据库直连适配器：只读、只登记/刷新 structured_dataset 目录，不把源库数据复制进本库。

config: {dsn_env, exposed_datasets: [{dataset_code, display_name, description, columns_doc, query_hint}]}

Odoo 相关数据始终走 SkillConnector（vertu-cli），不用本 connector；本 connector 留给以后新接的、
真正会给直连权限的其他数据库。真正跑 SQL 查询的能力（run_query）等到 agent 层建的时候再实现，
sync() 本身现在只做目录登记，不连源库。
"""
from app.catalog import registry
from app.connectors.base import SyncResult


class DbConnector:
    async def sync(self, source: dict) -> SyncResult:
        config = source["config"]
        result = SyncResult()

        for ds in config.get("exposed_datasets", []):
            await registry.upsert_structured_dataset(
                data_source_id=source["id"],
                dataset_code=ds["dataset_code"],
                display_name=ds.get("display_name"),
                description=ds.get("description"),
                columns_doc=ds.get("columns_doc"),
                query_hint=ds.get("query_hint"),
                refresh_mode="live",
            )
            result.items_processed += 1

        return result
