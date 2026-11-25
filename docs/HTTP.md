# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

晚到取消可提交到 `POST /api/runs/<id>/supplement`（也兼容 `/supplements`、`/cancellations`、`/backfill` 等别名），请求可使用 JSON 字段、JSON `records` 数组、CSV 片段或 `file=<CSV>`。请求应携带当前 `revision`；补录会把记录并入原批、按发生分钟全量复算，并将 `revision` 加一。此后用旧 `revision` 签发会得到 409 `STALE_REVIEW`。也可直接向 `PATCH /api/runs/<id>` 提交含 `kind=cancel` 或 `cancellation` 的补录内容。

`GET /api/runs/<id>` 除批次报告和复核状态外，还返回 `placements`、`supplements`、`workbench` 与 `conclusion`；`GET /api/runs/<id>/workbench` 只返回本批补位、对账暂缓结论和签发结论。占用数与 `audit` 对账登记不符时，`balance.releasable=false`、`balance.hold=true`，签发返回 409 `UNRESOLVED_ISSUES`。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据、补录记录、补位流水和复核信息的 JSON。
