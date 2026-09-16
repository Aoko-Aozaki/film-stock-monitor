# 历次巡检产物

每轮 `--json` 快照、日志与人工报告都放这里，**不要放回 skill 根目录**——
skill 目录应只含指令（SKILL.md）、参考资料（reference.md）、数据源
（stores.json）与脚本，产物混进去会让 skill 越滚越大。

快照用于 `--diff`：

```bash
scripts/check_stock.sh --json runs/snap-$(date +%F).jsonl \
                       --diff  runs/snap-上一轮.jsonl
```
