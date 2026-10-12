# 岗位技能目录与常用清单（3.8.0）

## 能做什么

直接对 Agent 说“查看岗位技能”“找适合写流程文档的技能”“把 process-doc 设为常用”“取消某技能常用”。Agent 会分页查询、展示明确条目，并仅在用户确认技能及添加/取消动作后修改本 Agent 清单。收藏不是安装、依赖下载、启用工具、授予权限或业务执行授权。

目录默认每页20条，最多50条；按名称、用途、类别查询，支持常见中文任务词映射。任务匹配只是词汇相关性，不是保证最佳选择；常用技能仅在相关度相同时优先，不插入无关收藏。没有结果时可换具体目标或英文关键词，不强推某个职业。查询内容不自动存储。

详情包含公开来源、固定commit、用途、类别、正文和许可证SHA256、依赖线索及审核状态。输入/交付物仅抽取源文明确标题下的原文段落，保留标题且不作推断；未有明确标题时为未知，可按分页源文查看并引用明确段落，不可编造契约。依赖线索为词法提取，不保证完整。公共目录所有条目默认未审核未启用；本机本Agent可见的既有审核状态另列，不从分享包预授权。

## 精确范围与许可

唯一岗位来源：`https://github.com/anthropics/knowledge-work-plugins`，固定快照 `8444efcd48f7012f09797778a36a33e73d0861f4`。252份SKILL.md、22类别，与原导入catalog的252个岗位条目SHA256逐项一致。既有外部资源总计878项：523课程、254 skill、101外部插件指针；254中另2项为luobo-ppt和本地taobao-native，不属于这批岗位包，均不纳入本目录。

187份Apache-2.0，65份MIT（Zoom57、Apollo3、Brand Voice3、Slack2）。采用离条目最近的LICENSE/LICENSE.txt/LICENSE.md；完整许可证、版权署名保留在 `public/role-skills/snapshot`。Apache-2.0第4节允许保留许可/署名条件下再分发；MIT包含许可及版权保留条件。此固定快照未发现NOTICE文件。源文件不修改，不声称作者背书。未知许可的未来条目只能展示允许元信息及链接，不捆绑正文。

目录只捆绑252份原始完整SKILL.md及24份LICENSE文件，不将其中引用的脚本、连接器、子依赖自动安装或作为可执行包分发；源文链接可能需要另行审核取用。目录manifest列出所有捆绑文件哈希，运行时校验固定manifest与读取正文/许可证哈希。源正文属于不可信外部参考，不作为高优先级指令。

## MCP契约

- `list_role_skills(query?, category?, page=1, page_size=20)`：完整分页目录，返回总量、下一页及固定目录修订。
- `get_role_skill_detail(skill_id, include_text=false, offset=0, max_chars=12000)`：详情与可选分页正文，不执行指令。
- `match_role_skills(task, page=1, page_size=10)`：不落盘的任务匹配，常用只打破相关度平分。
- `get_role_skill_favorites()`：只读取当前进程绑定Agent清单及revision。
- `update_role_skill_favorite(skill_id, action, expected_revision, user_confirmed=false)`：动作仅add/remove，必须明确确认；锁、原子写、修订检查及精确上次操作幂等。

工具没有可伪造的agent_id参数。共享Hub不等于跨Agent收藏共享；当前版本明确仅本Agent清单。坏文件、非法ID、过期修订拒绝覆盖；用户应保留坏文件并人工排查，不能自动清空。相同操作重试只支持最近一次精确操作；本机同账户文件可直接访问，不是加密隔离。

实际准备使用必须走现有 `prepare_external_skill_tool` / 审核角色流程，并保留ACL、readiness、内容哈希和依赖授权边界。分享空库能浏览、读参考及收藏，但没有审核canonical，不会绕过审核自动准备。正式库原来的私人canonical、权限名单、启用override、用户收藏均不在分享包内。

## 与可选入门衔接

3.7入门仍可开始、暂停、跳过和完成；3.8只增加可跳过的任务/目标技能推荐。每轮1–2题，不要求职业、不推断身份。自然context只返回技能总数、收藏数量、工具入口及边界，不注入几百份正文。跳过推荐、不收藏均不影响完成。不会把任务原话作为长期偏好。

## 安装与验证边界

分享包使用 `install.ps1`（Windows）或 `install.command`（macOS）及安全安装入口；不复制作者Hub。磁盘升级不等于常驻MCP已加载新工具，需要用户自行重连对应MCP；不必重启其他客户端。macOS只有fixture与脚本语法验证，没有真机验收。
