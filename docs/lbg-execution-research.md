# LBG 远程执行调研

来源： [LBG OpenAPI v4 创建 Job 完整流程](https://dptechnology.feishu.cn/wiki/R5gNw7dlGihMRGkyHkgctlJtnNh?fromScene=spaceOverview)（飞书文档，2026-09-01）。

本文把文档中已经明确的 LBG 行为，与当前 Flywheel 的本地评测链路对照。文档描述的是 OpenAPI v4 网关和 `lbg` 4.x 的实际流程；不把 Lebesgue 内部接口或公开站上不完整的 v1 Job 接口当作接入依据。

## LBG 能力和边界

LBG Job 是远端批处理容器：提交命令、镜像、计算规格和输入包后，调度器在算力池新建容器，运行结束后回收。它不是调用方的开发机、沙箱，也不会复用已有 Node/Sandbox。

生产网关是 `https://open.bohrium.com`；测试环境为 `https://open.test.bohrium.com`。鉴权使用玻尔 `BOHRIUM_ACCESS_KEY`（Bearer AccessKey），不是 Vouch 身份凭据。作业必须挂在项目上，并使用项目数字 ID；文档实测项目为 `530206`。

输入、日志和结果都经过对象存储：本地输入目录先压成 zip，上传到第一阶段返回的 Tiefblue `storeHost`/`storePath`，第三阶段用 `ossPath` 告诉调度器从哪里下载。任务完成后，申报的结果文件会打成 `out.zip` 回收到对象存储。

## 一条 Job 的实际生命周期

对外可以把它看成一次提交，但底层必须完成三个阶段：

1. `POST /openapi/v4/job/create`：创建尚未提交完整的任务记录，取得 `jobId`、短时上传 `token`、`storeHost` 和 `storePath`。这一步不启动容器，也不进入调度。
2. `POST {storeHost}/api/upload/binary`：使用临时 token，把输入 zip 上传到对象存储。大文件应使用现有 Tiefblue/lbg 上传库，不应自行猜测分片协议。
3. `POST /openapi/v4/job/add`：提交项目、镜像、SKU、命令、输入 `ossPath`、日志文件列表和结果文件列表，任务才进入调度。

v4 请求中的关键字段包括：`projectId`、`jobType=container`、`jobName`、`imageName`、`scassType`、`nnode`、`cmd`、`ossPath`、`inputFileType`、`inputFileMethod`、`logFiles`、`outFiles`。`lbg job submit` 将三阶段封装在一个命令中；手写 HTTP 也应把它们作为一个提交事务处理。

规格从 `GET /openapi/v4/calc/list?chooseType=...&scene=job&productLine=bohrium&isVirtualNode=false...` 读取。不能按物理机、nodeId、IP 锁定机器；调用方选择的是 SKU（如 CPU 的 `c2_m4_cpu`），调度器负责选集群。无库存的 SKU 仍可能提交，但会一直排队。

镜像可由 `GET /openapi/v4/image/list` 查询，也可以直接提供镜像引用。文档中的 Hello 示例使用 `registry.dp.tech/dptech/ubuntu:20.04-py3.10`。

## 状态、日志和结果

- `GET /openapi/v4/job/list` 用于轮询状态。常见展示顺序为 Wait → Running → Finished；Wait 表示等待算力，不等于失败。
- v4 的 `status` 是 Bohrium 数字枚举，不能和 Lebesgue 内部枚举混用。文档示例把 `2`、`-1`、`5` 视为 Finished/Failed/Stopped 终态；`webStatus` 可能与 `status` 不同。
- `GET /openapi/v4/job/{lebesgueJobId}/log` 返回日志文本。临时 URL 会过期，优先使用这个日志接口；URL 中 JSON 转义的 `\\u0026` 需要还原为 `&` 才能直接访问。
- `GET /openapi/v4/job/detail/{bohrJobId}` 用于读取结果地址。这里必须传 `add` 返回的 Bohrium `bohrJobId`，不能传 Lebesgue Job ID。结果只包含在 `outFiles` 中申报的文件，未申报文件在容器回收后无法取回。
- `POST /openapi/v4/job/terminate/{jobId}` 停止排队或运行中的任务；`POST /openapi/v4/job/del/{jobId}` 删除记录并清理。删除不能替代停止。

文档特别提醒：v4 接口没有对外幂等键。阶段三超时后不要盲目重复提交，应先通过任务列表或记录查询确认是否已受理。

## 与当前 Flywheel 本地评测的差异

| 环节 | 当前 Flywheel | LBG 远程 Job |
| --- | --- | --- |
| 运行位置 | 当前进程在本机启动 `run.sh` | 远端调度器新建容器执行 `cmd` |
| 资源获取 | 按 Digest 反查 Repo，下载到 `~/.cache/flywheel/snapshots/<digest>/content` | 需要把本次输入打包并上传到 Job 的 Tiefblue 存储 |
| 数据采样 | `create_run_plan()` 先读取完整 Dataset，再写出 `runs/<run-id>/inputs/selected-dataset.jsonl`；脚本只消费选中的样本 | Job 看到的是上传的输入包；若要保持当前语义，应在上传前只打包选中样本及必要运行文件 |
| 配置传递 | 生成 `effective-run-config.json`，执行命令为 `run.sh --run-config <path>` | 需要把有效配置放入输入包，并把远端容器中的路径写入 `cmd`；不能把本机绝对路径传给远端 |
| 模型调用 | 本地 `bind_model()` 从项目 `.env` 加载 endpoint/model/credential 环境变量，脚本通过 OpenAI-compatible API 调用 | 远端容器需要可访问模型 endpoint，并通过安全的运行时注入提供凭据；不能把本地 `.env` 或 token 直接打入 zip |
| 脚本约定 | Script Snapshot 必须提供 `run.sh`，并输出 `fw-qa-summary/v1` | `cmd` 由 LBG 启动；建议仍运行资源内的 `run.sh`，保持同一 Script 合同 |
| 状态与日志 | 本地 `status.json`、`script.log`、`result.json`，后台 worker 通过 PID 判断失联 | 远端 `job/list`、`job/{id}/log` 和 `job/detail/{bohrJobId}`；需在 Flywheel 中映射为统一 Run 状态 |
| 结果 | 结果直接写入本地 run 目录 | 结果经 `outFiles` 打包为 `out.zip`，再下载解压到本地输出目录 |

## 对 Flywheel 接入的直接结论

1. 应新增一个 LBG 执行适配层，不要把 LBG 的三阶段 HTTP 细节散落在评测 Planner 或 Script Adapter 中。适配层负责 create、上传、add、轮询、日志和结果下载。
2. `RunPlan` 仍应先解析 Task/资源 Snapshot 并完成采样；远程提交的输入包只包含本次运行需要的选中数据、有效运行配置和脚本运行所需文件。这样不会因为远程执行而重新下载整个 Dataset 到容器。
3. 远端命令应使用容器内相对路径，例如 `sh -c './run.sh --run-config /work/effective-run-config.json > stdout.log 2>&1'`。本地路径只能用于打包，不能写入远端命令。
4. `logFiles` 和 `outFiles` 应由 Flywheel 固定申报：至少包含标准脚本日志和标准结果摘要；否则远端运行结束后无法恢复对应文件。
5. 远程 Run 的持久化记录至少要保存项目 ID、LBG/Lebesgue Job ID、Bohrium Job ID、镜像、SKU、提交时间和状态。两个 Job ID 用途不同，不能合并成一个字段。
6. 鉴权配置应独立于 Wenyon/Vouch。当前 LBG 文档使用 `BOHRIUM_ACCESS_KEY`；Flywheel 需要从受保护的运行环境读取，不能放进 Task Manifest、资源 Blob 或输入 zip。
7. 首版建议先封装官方 `lbg job submit`，它已经处理三阶段和上传细节；只有需要统一状态、日志和结果协议时，再逐步替换为文档中验证过的 v4 OpenAPI 调用。无论采用 CLI 还是 HTTP，都不能省略输入上传和 `outFiles` 声明。

## 待确认事项

- LBG 项目 ID、默认镜像、CPU/GPU SKU、区域和并发额度由谁配置；这些不应让每个 Task 用户重复填写。
- LBG 容器访问模型 API 的网络出口和凭据注入方式；如果模型服务只在本地网络可达，远端执行无法直接复用当前 `.env`。
- LBG 的输入包大小上限、上传分片策略和任务最长运行时间；大 Dataset 需要确认是否支持按需或分片读取。
- Task 的 `run.sh` 如何在远端工作目录中定位资源和输出，以及 `fw-qa-summary/v1` 如何从 `out.zip` 映射回统一 Run Output。
- LBG 无幂等键时，Flywheel 需要设计提交记录和超时恢复策略，避免重复创建计费任务。
