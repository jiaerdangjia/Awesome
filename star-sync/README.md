# GitHub Star 自动同步

账号固定为 **jiaerdangjia**，运行仓库为 **jiaerdangjia/Awesome**，默认分支为 **master**。
原仓库的 Windows 软件清单、中英文 README、图片、许可证及其他文件保持不变。

## 日常使用

在 GitHub 对感兴趣的公开仓库点击 **Star** 即可，不需要复制链接或 Fork。
工作流 `Sync GitHub Stars` 每小时第 17 分钟（UTC）检查一次；GitHub 调度可能延迟，
不是实时事件监听。也可打开仓库 **Actions → Sync GitHub Stars → Run workflow** 手动运行。

查看 [待分析清单](PENDING.md)。之后在 Awesome Github 对话中说“读取 Awesome 的待分析清单”，
即可按此文件分析。本功能只收集和排队，不自动调用 AI、不产生模型 API 费用，
也不会自动向 ChatGPT 发消息。你的电脑无需保持开机。

**首次成功同步只建立历史基线，历史收藏不批量进入待分析清单。**
请先确认 Actions 中首次同步成功，再用一个新收藏验证增量入队。
初始化之前收藏的 Karpathy 等项目也属于历史基线，需要时可逐个手动加入待分析。

## 状态管理（无需改 JSON）

在 **Run workflow** 表单中填写 `repository`，格式为 `owner/repo`，并选择 `analysis_status`：

| 状态 | 含义 |
| --- | --- |
| `pending` | 加入或重新加入待分析；也可用于选中的历史收藏。 |
| `done` | 已分析，从待分析清单移出。 |
| `ignored` | 暂不分析，从待分析清单移出。 |
| `baseline` | 首次同步自动标记的历史收藏，不表示已经分析。 |

`repository` 留空表示仅同步，不修改任何分析状态。仓库改名后使用同步后的新名称。
人工备注保存在 `data/stars.json` 对应记录的 `notes` 字段，同步不会覆盖。
不要编辑自动生成的 `PENDING.md` 或 `data/pending.json`；下一次同步会重新生成它们。

## 同步规则与安全边界

- 通过官方 REST API 分页获取公开 Star，按不变的仓库 ID 去重；不只读取第一页。
- 首次建立基线；后续第一次发现的 ID 才进入待分析。仓库改名、转移、重复返回、
  取消后重新 Star 均不产生第二条记录，也不重置已分析/忽略状态。
- 保留累计历史，不把“未返回”武断判定为取消收藏。取消 Star、仓库删除或权限变化
  不删除记录，也不自动撤销已排队任务；需要时手动标记 `ignored`。
- 只抓取定时检查时仍可见的收藏；两次检查之间 Star 后又取消的短暂操作可能无法捕捉。
- 所有分页成功并校验后才写文件。网络错误、限流、异常响应、空结果或损坏状态会使运行失败，
  保留已提交数据。空结果也可能由 GitHub 私密个人资料导致，不会被当作“全部取消”。
- 无变化不提交。`last_changed_at` 是内容变化时间，不是最近检查时间；检查时间以 Actions 记录为准。
- 只导出明确标记为公开的仓库。此 Awesome 仓库公开，累计收藏记录及你填写的备注也会公开，
  不要在备注写账号密码、客户资料或公司内部数据。
- 使用 GitHub 自带的短期 `GITHUB_TOKEN`，只申请本仓库 `contents: write` 权限。
  无需额外 PAT 或 AI API Key；不会读取其他仓库源码、执行收藏项目代码或修改你的 Star。
- 写回只暂存三个生成文件；从不强制推送。并发人工提交造成冲突时本次失败，下次重新读取最新状态。
- 只接收该仓库 `master` 分支的计划/手动/特定代码推送事件；没有不可信 PR 写权限工作流。

## 文件结构

```text
.github/workflows/sync-stars.yml   # 每小时 / 手动 / 同步代码变更时触发
star-sync/
  README.md                       # 本说明
  sync_stars.py                   # Python 标准库，无第三方依赖
  test_sync_stars.py              # 仅此功能的离线测试
  PENDING.md                     # 自动生成人读清单
  data/stars.json                 # 首次成功后生成；完整历史和持久分析状态
  data/pending.json               # 首次成功后生成；机器可读待分析清单
```

## 本地检查（可选）

在仓库根目录，用 Python 3.10 或更高版本运行：

```sh
python -m unittest discover -s star-sync -p test_sync_stars.py -v
python star-sync/sync_stars.py --dry-run
python star-sync/sync_stars.py
python star-sync/sync_stars.py --set-status owner/repo done
```

未提供 `GITHUB_TOKEN` 时使用公开匿名接口，可能较早触发限流；不要把令牌写入代码或提交。
生成文件需要一起提交。不要删除 `data/stars.json`，否则下一次会重新建立基线。

## 故障处理

本仓库是现有 Fork，若 GitHub 显示 Actions 未启用，需要在 **Actions** 页启用。
工作流文件必须位于默认分支。若组织/仓库策略禁止写入，不要放宽其他仓库权限，
先检查该工作流失败日志与本仓库 Actions 设置。此功能不绕过分支保护。

公开仓库长期无活动（60 天）时，GitHub 可能自动停用定时工作流。
届时在 Actions 页重新启用；本脚本不会为保活制造无意义提交。

官方依据：
[Star API](https://docs.github.com/en/rest/activity/starring#list-repositories-starred-by-a-user) ·
[工作流调度](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) ·
[启用工作流](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)
