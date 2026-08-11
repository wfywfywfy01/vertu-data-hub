"""Skill 取数：跑 `vertu-cli <domain> <shortcut> [--params json]`，解析 JSON 写入 structured_record。

config: {domain, shortcut, params, dataset_code, summarize}
- dataset_code 按当次拉取的周期 upsert（同周期重复拉取会覆盖，不会重复插入）
- summarize=true 时额外生成一段摘要文本 embedding 进 doc_chunk，仅作语义发现入口，
  精确数值以 structured_record.data 为准
"""
import hashlib
import json
import subprocess
import sys
from datetime import date

from app.catalog import registry
from app.config import settings
from app.connectors.base import SyncResult


class SkillConnector:
    async def sync(self, source: dict) -> SyncResult:
        config = source["config"]
        result = SyncResult()

        try:
            output = self._run_cli(config)
        except Exception as exc:
            result.errors.append(str(exc))
            return result

        params_hash = hashlib.sha256(json.dumps(config.get("params", {}), sort_keys=True).encode()).hexdigest()[:12]
        today = date.today()
        natural_key = f"{config['shortcut']}:{params_hash}:{today.isoformat()}"

        external_key = f"{config['domain']}{config['shortcut']}:{params_hash}"
        item = await registry.upsert_source_item(
            source["id"], external_key, hashlib.sha256(output.encode()).hexdigest(), status="ingested"
        )

        await registry.upsert_structured_record(
            data_source_id=source["id"],
            dataset_code=config.get("dataset_code") or config["shortcut"].lstrip("+"),
            natural_key=natural_key,
            data=json.loads(output),
            record_kind="snapshot",
            source_item_id=item["id"],
            row_date=today,
        )
        result.items_processed = 1

        if config.get("summarize"):
            from app.ingestion.doc_ingest import ingest_text

            summary = f"数据源 {source['display_name']}（{config['domain']} {config['shortcut']}）于 {today} 拉取结果：\n{output}"
            await ingest_text(
                summary,
                data_source_id=source["id"],
                source_file=f"skill:{external_key}",
                section=config["shortcut"],
                source_item_id=item["id"],
                tags={"domain": config["domain"], "shortcut": config["shortcut"]},
            )

        return result

    def _run_cli(self, config: dict) -> str:
        # 参数走 CLI flag（与 vertu-cli 的实际调用约定一致，如 `--period today --dept-l1 线上事业部`），
        # 不是把 params 打包成一个 JSON blob 传进去。
        cmd = [settings.vertu_cli_bin, config["domain"], config["shortcut"]]
        for key, value in config.get("params", {}).items():
            flag = "--" + key.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    cmd.append(flag)
                continue
            if isinstance(value, list):
                cmd += [flag, ",".join(str(v) for v in value)]
                continue
            cmd += [flag, str(value)]

        # Windows 下 npm 装的 CLI 是 .cmd 包装脚本，CreateProcess 不会自动解析 PATHEXT，
        # 必须走 shell=True（等价于 cmd.exe /c ...）才能找到它，否则报 WinError 2。
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
            shell=(sys.platform == "win32"),
        )
        if proc.returncode != 0:
            raise RuntimeError(f"vertu-cli failed ({proc.returncode}): {proc.stderr.strip()}")
        return proc.stdout.strip()
