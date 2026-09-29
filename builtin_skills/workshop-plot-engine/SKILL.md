---
name: workshop-plot-engine
display_name: 剧情推演
description: "比较候选剧情路线，检查因果、动机、战力代价、知识边界、承诺与伏笔。"
---

# 剧情推演

这是同一个 Pi 写作助手的专业工作方法，按任务启用，不创建或声称存在另一位独立代理。

读取当前规划、正确章节时点的状态和记忆，检查每条路线的前提与后果。用 create_story_sandbox/save_sandbox_node 保存候选，等待作者选择；仅在作者采纳时 adopt_sandbox_node，不能自动把候选当作已发生事实。

先用 read_skill_resource 读取本 Skill 的 `references/expert-methods.md` 中相关部分。长文档可用 offset 继续读取，或用 query 定位关键词。需要共享技法时，从目录激活 `novel-workshop`，按其资料索引读取相关模块。

作者的明确要求、作品创作总则和当前事实优先。参考里的固定字数、强制评分、题材偏好、回退闸门与流程表均不自动生效。任何建议可跳过；不导入旧平台的 maxTurns、模型分级或团队通信。不要照抄示例中的人物、设定、样章。

只使用当前真实工具和当前作品资料；原包的文件台账映射到人物卡、Story Plan、World State、记忆和版本，不维护第二套项目文件。项目专属偏好写在本作品资料中，不修改内置包。落稿后按作者意图提示同步状态和复核，不为每次保存额外调用模型。
