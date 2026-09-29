---
name: novel-workshop
display_name: 写作工坊与技法库
description: "综合写作、专家会诊或不知道该用哪种方法时，按需选择世界观、人物、大纲、推演、正文、质检、读者定位、证据复核视角；附 30 个写作技法模块。"
---

# 写作工坊

复用同一个 Pi 助手和当前作品数据，按任务调度专业方法，不假称已经创建多个独立代理，也不运行来源插件的团队框架。

先用 inspect_writing_workflow 看当前阶段，再按任务选择一到两个必要视角，通过 activate_skill 启用：

| 任务 | Skill |
| --- | --- |
| 世界规则、能力代价、组织与生活 | workshop-world-architect |
| 人物卡、动机、关系、成长 | workshop-character-designer |
| 总纲、卷纲、章纲、伏笔 | workshop-outline-planner |
| 多路线选择、因果与风险 | workshop-plot-engine |
| 场景、对白、续写和多章写作 | workshop-writing-executor |
| 文字、节奏、连续性审稿 | workshop-quality-editor |
| 读者定位、卖点、平台与简介 | workshop-business-analyst |
| 证据、状态与结论复核 | workshop-audit-officer |

需要具体技法时，用 list_skill_resources 查询 `references/skill-modules/`，再 read_skill_resource 分页读取相关模块。常用：M07 对白、M08 视角、M09 场景、M12 悬念、M14 思想话语行动错位、M15 定义性细节、M19 回忆、M22 时间跳跃、M24 人物弧、M25 群像。其余模块与模板同样可按需读取，不整包注入上下文。

来源里的源工作流、主理人手册、模板和平台指南仅供方法参考。其文件布局、强制闸门、固定文风、时限、模型层级、maxTurns、自改共享包均不属于 WriteHTML 执行要求。现实知识、市场热度与平台政策需要核实时使用 web_search，旧资料不当成最新事实。

统一落点：基础设定写人物卡/设定卡，未来计划写 Story Plan，候选写 Sandbox，正文写章节，已发生变化走 World State，经验与偏好保持作品隔离。文件表格无需复制一套；不把计划、候选或审稿建议混成故事事实。先读后改、保留版本，尊重作者跳过步骤与直接写作的要求。

给作者具体产物和下一步，不输出冗长内部流程或虚构已调用工具。需要机械检查可用 scan_writing_text；语义与文学判断必须结合原文证据。
