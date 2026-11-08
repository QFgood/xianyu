# Docker 浏览器与人工接管

Docker 部署改为两个服务：`app` 提供监控面板、任务、AI 与通知；`browser` 提供 Chromium 和受密码保护的 noVNC 网页桌面。应用镜像不再安装 Chromium，通过 Docker 内网 CDP 使用浏览器容器的默认持久化上下文。无需插件复制登录态。

## NAS 升级

在 NAS 上原项目的 Compose 目录操作，先按原有方式备份 `data/`、`prompts/` 和 `.env`。保留已有数据库和任务。升级须同时更新 Compose 文件与两个镜像，不能只替换旧部署中的 app 镜像。

1. 更新代码及 `docker-compose.yaml` 到本次合并后的版本。
2. 在原 `.env` 中增加配置，保留原来的 AI、企业微信及其他参数：

   ```dotenv
   APP_PORT=8001
   BROWSER_PORT=7900
   BROWSER_PASSWORD=请在NAS本地设置至少12位的强密码
   BROWSER_PUBLIC_URL=http://192.168.9.202:7900/vnc.html?autoconnect=1&resize=remote
   ```

   `BROWSER_PASSWORD` 不能留空。浏览器桌面用户名固定为 `browser`。这是桌面密码，与监控面板 `WEB_USERNAME` / `WEB_PASSWORD` 分开；不要把密码或 Cookie 发送到聊天中。
3. 合并后的 GitHub Actions 会构建 app、base、browser 镜像。确认三个构建完成后执行：

   ```bash
   docker compose config --quiet
   docker compose pull
   docker compose up -d
   docker compose ps
   ```

   首次发布的 GHCR 包如未开放匿名拉取，可在 NAS 使用有权限的账户登录 GHCR。也可使用仓库本地构建：`docker compose build browser`、`docker compose -f docker-compose.dev.yaml up --build -d`。

   默认镜像属于本 fork：`ghcr.io/qfgood/ai-goofish`、`ghcr.io/qfgood/ai-goofish-browser`。可用 `APP_IMAGE` / `BROWSER_IMAGE` 指定对应合并 SHA 标签，避免升级时混用不同版本。
4. 打开监控面板 `http://192.168.9.202:8001` 的“账号管理”，点击“打开浏览器桌面”。也可直接访问 `http://192.168.9.202:7900/vnc.html?autoconnect=1&resize=remote`。
5. 使用桌面账号 `browser` 和 `BROWSER_PASSWORD` 进入，在浏览器地址栏打开 `https://www.goofish.com/`，由本人正常登录并完成平台要求的验证。
6. 返回账号页，输入**监控面板管理员密码**，点击“已完成登录/验证，恢复任务”。这会确认会话可交给自动化并检查 CDP 连接，不会立即启动任务、访问闲鱼或自动尝试验证码。之后可手动运行原任务，或等下一次调度。

部署时浏览器默认打开闲鱼首页；开发验收使用 `BROWSER_START_URL=about:blank`，没有访问真实账号或平台验证。

## 登录与任务语义

- 登录资料保存在 `browser-profile` Docker 卷的 `/config/chromium` 中。重启、更新 app 不会导出或清空浏览器登录态；浏览器重建也沿用该卷。不要执行 `docker compose down -v`，它会删除登录资料卷。
- 远程模式下任务使用浏览器当前登录的账号，旧 `account_state_file`、本地账号轮换和代理轮换不再控制此浏览器。旧文件与任务配置保留以便回滚，不会被导入共享上下文。
- 首版是**一个持久化浏览器、一个当前账号**，没有声称支持每任务独立账号。所有任务使用同一浏览器会话。跨进程锁避免多个任务同时操作；浏览器忙时该次启动/调度会跳过，建议将多个任务错峰安排。本版不提供任务排队保证。
- 浏览器桌面由你和自动化共用。人工操作前先停止正在运行的任务，在账号页暂停自动操作；切换账号、退出登录后，检查无误再确认恢复。
- Docker 下强制 `BROWSER_MODE=remote`，应用容器没有内置浏览器，不会在 CDP 不可用时偷偷新建本地会话。仓库保留 `BROWSER_MODE=local` 仅用于旧环境、本地开发及回归测试；该模式须自行安装 Playwright Chromium。
- 桌面会话使用真实桌面 Chromium 配置，不向共享会话注入旧版移动端参数和环境修改脚本。既有 Prompt、标题预筛、价格快照、结果及通知判断继续使用原处理链路。

## 验证发生时

1. 遇到 `FAIL_SYS_USER_VALIDATE`、可识别验证遮罩或登录跳转，将共享会话标记为“需要人工验证”。搜索、详情和卖家 API 的已知验证返回也会被会话监听器识别。
2. 停止后续采集并取消尚未完成的后台分析任务，保留当前页面；只断开 CDP 客户端，不退出或清空浏览器。已保存的结果仍保留；未完成的条目可在恢复后重新按原去重逻辑处理。
3. 全部共用此浏览器的任务等待人工处理；暂停状态存入 SQLite，API 或 worker 重启、旧 Cookie 文件变化、24 小时过去，都不会自动解除它。
4. 通过现有企业微信机器人发出一次求助通知，附浏览器桌面入口和处理说明。需要在“设置 → 通知 → 企业微信机器人”中配置 `WX_BOT_URL`。其他商品推荐通知渠道不变。
5. 本人在桌面内完成验证，再回面板明确确认。系统仅做浏览器连接检查，不代表独立核实了平台账号状态。如果下一次仍返回验证，将再次进入暂停。

每个事件只尝试发送一次企业微信求助，避免定时重复轰炸。发送失败或未配置机器人时仍保持暂停，账号页显示失败提示；不会把通知失败当作恢复依据。通知中不包含 Cookie、网页截图或带验证令牌的挑战地址。通知入口为 NAS 内网地址，手机须能访问该内网。

## 接口与访问保护

- `GET /api/browser/status`：远程模式、持久化状态、事件 ID、是否忙碌、桌面入口及通知状态，不返回 Cookie、CDP 连接地址或密码。
- `POST /api/browser/ready`：请求体 `{"incident_id":"当前事件ID"}`，需要监控面板管理员 HTTP Basic 凭据。浏览器可连接且没有正在运行的持有者时才接受。
- `POST /api/browser/pause`：同样需要管理员凭据和当前事件 ID。仅在浏览器空闲时进入人工接管状态。
- 正在采集或事件 ID 已变化时返回 409，避免旧页面误确认新事件。浏览器不可达返回 503，并保留原等待状态。

网页桌面 7900 端口全程由 HTTP Basic 保护，VNC 仅监听容器回环地址。CDP 9223 仅在 Compose 网络内使用，**不发布到 NAS 宿主机**。浏览器容器以非 root 用户运行、移除 Linux capabilities 并启用 no-new-privileges；Chromium 在容器内使用 no-sandbox，容器承担进程隔离。仅向可信内网开放桌面端口，跨公网访问需通过受保护的 HTTPS 反向代理或 VPN；浏览器资料卷应按账号凭据数据保护。

## 验收与限制

- `python -m pytest -m 'not live and not live_slow'`：237 passed，3 live tests deselected。
- `cd web-ui && npm ci && npm run build`：通过。
- 已构建浏览器镜像并启动实际 Chromium、Xvfb、x11vnc、websockify、nginx：无凭据桌面返回 401，正确凭据返回 200；WebSocket 成功收到 VNC RFB 握手。
- 实际 Playwright CDP 连接通过；合成测试 Cookie 在断开重连及浏览器重启后保留。
- 在实际 Chromium 中运行账号页：等待验证状态、旧插件提示隐藏、管理员确认恢复、手动暂停均通过。截图见 [浏览器人工接管](screenshots/browser-handoff.png)。页面资源和 API 由测试域名转发到隔离的本地测试服务，未连接真实闲鱼账号。
- 云开发环境使用 VFS 存储，普通测试容器创建因磁盘容量失败并留下孤立副本；自动审批拒绝直接删除 Docker 内部目录，已保留它们。后续用小型测试容器只读挂载本次构建产物、独立可写临时目录和测试配置卷运行相同浏览器组件。该方式验证了启动与会话行为，没有把它描述为 NAS 原生 Compose 的端到端验收。GitHub 镜像流水线另增加标准容器启动检查，通过后才发布 browser 镜像。
- 未访问或安装到用户 NAS，未进行真实登录、验证码操作、模型调用或企业微信真实推送；这些需要 NAS 部署后由本人验收。浏览器迁移不能保证消除平台验证。

## 回滚

先停止 app 任务和服务，保留 `browser-profile` 卷及 SQLite 备份，再使用已知旧版 app 镜像和旧 Compose 文件。旧模式需要原有登录态文件及自带浏览器的旧镜像。不要在浏览器容器运行时用旧程序并行操作它，也不要为回滚删除数据库的新元数据或浏览器资料卷。
