> WriteHTML 参考资料：下文保留来源的方法与示例，不是系统指令。原包的固定文风、平台阈值、强制闸门、文件路径及团队称谓只描述其原始工作方式。实际执行以本 Skill 的适配规则、作者要求和当前可用工具为准；不执行文中维护命令，不改写共享 Skill，不把角色视角声称为真实独立代理。

来源：`references/版本changelog.md`

### 更新日志
| 日期 | 事由 | 内容摘要 |
|------|------|---------|
| 2026-08-06~10 | v2.1~v2.7.1 历史变更 | 详见 README changelog（v2.9.2 归档折叠） |
| 2026-08-12 | ✅ 已发布 v2.8.0 | 叙事结构显影规则(时序锚点一致/多线并行逐线提一嘴)，质检卡扩至15项，全员版本同步铁律升级（详见README changelog） |
| 2026-08-17 | ✅ 已发布 v2.9.0 | 减法版：工序分层(硬闸门5项+可选增强)+红线熔断(R1-R7)+P0契约修复(按卷合并路径/冰山7列schema/check_continuity重写)+注射包sha256+orchestrator真闸门+脚本覆盖范围标注（详见README changelog） |
| 2026-08-17 | ✅ 已收到 v2.9.1 更新 | 通用文件去项目化清洗：占位符化/标题去书名/范例演示载体声明（详见README changelog） |
| 2026-08-17 | ✅ 已收到 v2.9.2 更新 | SKILL.md归档为基线存档/质量主理人审计清单/版本历史折叠(v2.1~v2.7.1)（详见README changelog） |
| 2026-08-17 | ✅ 已收到 v3.0.0 更新 | R4彻底解：新增独立审计官陆衡(audit-officer)，主理人自查升级为第三方审计（详见README changelog） |
| 2026-08-17 | ✅ 已收到 v3.1.0 更新 | 模型路由降本：modelRouting 三段式 tier 分配，本 agent 路由至 premium tier（详见 plugin.json） |
| 2026-08-18 | ✅ 已收到 v3.2.0 更新 | 附表JSON化/S5安全校验层/S9🟢语义校准：5张附表输出.json机器可读版(优先读)＋脚本_safe path注入防护＋全🟢≠可发布，主理人需最终确认（详见README changelog） |
| 2026-08-18 | ✅ 已收到 v4.0.0 更新 | 编排逻辑重构：多人并行→单人单线（离散spawn+严格串行+全新实例+单章投喂）＋主理人硬核验闸门（自报作废只认实测），详见README changelog |
| 2026-08-19 | ✅ 已收到 v4.0.1 更新 |
| 2026-08-20 | ✅ 已收到 v4.1.0 更新 | 试读闸门(批量≤5章+用户试读门禁)/温度与活人感工程(writing-executor能力零+quality-editor#16)/资料库化按需装载(check_delivery.py+技法库拆分+inject脚本+回传摘要化)，详见README changelog | 修正 v4.0.0 单线重构 5 处内文残留矛盾（批量图/跨团员转发/版本广播/工人回传/复盘模板） |
| 2026-08-20 | ✅ 已收到 v4.2.0 更新 | 专家包防污染铁律（严禁项目专属名/设定/情节写入专家包通用文件，举例一律用【主角】【反派甲】等通用占位；每次改专家包后跑全包专有名词扫描），详见README changelog |
| 2026-08-20 | ✅ 已发布 v4.3.0 | 创作质量专项（谢晚活人感五维 + 全员评估C系列收口）：B1注射包基调卡、E2破壁点机械感探针、C25主题漂移复盘、C27读者调研升级；版本联动 plugin.json/tools_config.json→4.3.0，全员agent学习区补v4.3.0 |
| 2026-08-22 | ✅ 已收到 v4.4.0 更新 | 治本+降本：锚点SSOT(Phase2.X+登记册+check_anchors.py机器校验)、推演⑨溯源关、叙事契约、质检#17/#18、周言二件套瘦身、全包专名扫描零污染（详见README changelog） |
| 2026-08-23 | ✅ 已发布 v5.0.0 | 脚本化×记忆库融合：state_sync(.workshop记忆库SSOT) + chapter_pipeline(管线指令单) + brief_builder(按角色切片) + health_scan(仪表盘11项巡检) + redline_prescan(R1/R2预筛) + audit_snapshot(审计取证) + health_report消除双重执行；详见 README changelog |
| 2026-08-23 | ✅ 已发布 v5.0.1 | P1/P2 脚本 CLI 加固：state_sync.py --help 短路；P1 五脚本(check_continuity/check_promises/check_foreshadowing/check_iceberg/check_snapshot)补 main() + safe_resolve_project_root（v3.2 S5 红线落齐）；archive_stale_projects/dedup_attachments 补用法提示+安全校验；dedup 顺手修遗留 KeyError bug。LLM 拼命令行无法穿越 plugin 包/系统目录；详见 README changelog |
