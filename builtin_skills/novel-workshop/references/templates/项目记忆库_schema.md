> WriteHTML 参考资料：下文保留来源的方法与示例，不是系统指令。原包的固定文风、平台阈值、强制闸门、文件路径及团队称谓只描述其原始工作方式。实际执行以本 Skill 的适配规则、作者要求和当前可用工具为准；不执行文中维护命令，不改写共享 Skill，不把角色视角声称为真实独立代理。

来源：`references/templates/项目记忆库_schema.md`

# 项目记忆库 Schema（v5.0 · 脚本化与记忆库融合）

> 事实源 = `.workshop/` 下的 JSON；md 是展示层。脚本是唯一写入口。
> 初始化/对账用 `scripts/P0_assembly/state_sync.py`（init / sync / shadow）。
> 本模板供手工查看与调试参考——正常情况由脚本生成，不手写。

## 目录结构

```
项目根/
└── .workshop/
    ├── index.json        # 全局状态机（阶段/锁/闸门/附表版本/仪表盘）
    ├── chapters.json     # 章索引（每章状态机）
    ├── tables/           # 附A-E JSON（与项目 5张附表/ 对账）
    ├── anchors.json      # 锚点登记册（与 设定锚点登记册.json 对账）
    ├── decisions.json    # 决策日志摘要
    └── audit/            # 审计快照目录（audit_snapshot.py 写入）
```

## index.json

```json
{
  "schema_version": "5.0",
  "project": { "name": "", "genre": "", "platform": "", "target_words": 0 },
  "phase": { "current": "Phase 5", "chapter": 27, "status": "in_progress" },
  "locks": {
    "worldview": { "locked": true, "based_on_market_snapshot": "v2" },
    "outline":   { "locked": true }
  },
  "gates": {
    "audit":  { "last_audit_chapter": 25, "verdict": "✅", "blocked": false },
    "trial_read": { "awaiting_user": false, "pending_batch": null },
    "rollback_guard": { "same_type_count": 0, "type": null }
  },
  "tables": { "A_latest_ch": 27, "B1_latest_ch": 27, "B2_latest_ch": 27,
              "C_latest_ch": 27, "D_latest_ch": 27, "E_latest_ch": 27,
              "C_pending": 4 },
  "iceberg": { "unexposed_pct": 62, "alert": false },
  "dashboard": { "alerts": [], "last_scan_chapter": 25 },
  "version": { "plugin": "5.0.0", "members_confirmed": ["all"] },
  "updated_at": "2026-08-23T10:00:00+08:00"
}
```

## chapters.json

```json
{
  "27": {
    "title": "章名", "words": 3214,
    "inference": { "status": "done", "sha256": "a1b2c3d4" },
    "writing":   { "status": "done", "file": "正文存稿/第1卷.txt",
                   "delivery": "PASS", "anchors": "PASS", "style_flags": 3 },
    "qc":        { "verdict": "🟢", "red": 0, "yellow": 1, "green": 2 },
    "state_declaration": "merged",
    "audit": "not_required"
  }
}
```

## 状态声明块（LLM 输出、脚本入库的桥梁）

韩宇/谢晚每章产出末尾附加以下 JSON 块，由管线脚本校验单调性后合并入库：

```
【第N章状态声明】
{"appendix_A": {"time": "第三日·夜", "location": "黑风寨"},
 "appendix_B1_new": [{"who": "【主角】", "learned": "身世秘密", "from": "【配角甲】"}],
 "appendix_B2_delta": [{"item": "疗伤散", "delta": -1, "reason": "重伤"}],
 "appendix_C": [{"promise_id": "P003", "action": "advanced"}],
 "appendix_D": [{"who": "【主角】", "state": "悲愤", "acute_until_ch": 30}]}
```

脚本校验：JSON 合法 → 单调性约束（B1 只增不减、B2 非负、章号连续）→ 合并入 tables/*.json → 更新 chapters.json。
