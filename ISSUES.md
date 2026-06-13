# RedFolio 上线审查问题清单

> 审查日期: 2026-06-13
> 版本: 0.1.0 (commit `c04b0a2`)

---

## 高优先级 (发布前必须修复)

### ISSUE-01: Electron 安全沙箱默认关闭

- **严重程度**: 高 / 安全性
- **位置**: `electron/main.cjs:12-14`
- **描述**: Linux 下 Electron 进程默认添加 `--no-sandbox` 参数，除非用户显式设置 `REDFOLIO_ENABLE_ELECTRON_SANDBOX=1`。沙箱是 Electron 关键安全边界，禁用后可被渲染进程漏洞利用提权至操作系统。
- **现状**:
  ```js
  if (process.platform === "linux" && process.env.REDFOLIO_ENABLE_ELECTRON_SANDBOX !== "1") {
    app.commandLine.appendSwitch("no-sandbox");
  }
  ```
- **建议**: 反转逻辑，改为仅在显式设置 `REDFOLIO_DISABLE_ELECTRON_SANDBOX=1` 时才关闭沙箱。如果沙箱开启时 Electron 无法启动，应排查根本原因（通常是内核不支持 user namespaces）而非直接禁用。

---

### ISSUE-02: dev 脚本也硬编码 --no-sandbox

- **严重程度**: 高 / 安全性
- **位置**: `package.json:8`
- **描述**: `npm run dev` 命令中 electron 启动参数硬编码了 `--no-sandbox`。
- **现状**:
  ```json
  "dev": "concurrently \"vite --host 127.0.0.1\" \"wait-on tcp:5173 && electron --no-sandbox .\""
  ```
- **建议**: 移除 `--no-sandbox`，或使用环境变量控制。

---

## 中优先级 (上线前应修复)

### ISSUE-03: 数据刷新循环无速率限制

- **严重程度**: 中 / 功能可靠性
- **位置**: `service/redfolio_service/main.py:275-277`
- **描述**: 刷新所有持仓标的的行情和分红数据时，对 AKShare 数据源连续发起请求，无任何请求间隔。当用户持仓较多时可能触发上游 API 限流甚至 IP 封禁。
- **现状**:
  ```python
  for instrument in instruments:
      result = refresh_instrument(state, source, instrument)
      items.append(result)
  ```
- **建议**: 在循环中添加 `time.sleep(0.5)` 或指数退避延迟；也可为 `AkshareDataSource` 添加请求频率限制器。

---

### ISSUE-04: AKShare HTTP 调用无超时配置

- **严重程度**: 中 / 功能可靠性
- **位置**: `service/redfolio_service/data_sources.py:192-221`
- **描述**: AKShare 底层 HTTP 请求无显式超时配置。若上游数据源（东方财富、新浪等）响应缓慢或挂起，刷新操作将永久阻塞，用户无法中断。
- **建议**: 检查 AKShare 是否支持 `timeout` 参数并设置合理的超时值（如 15s）；或在调用层面使用 `asyncio.wait_for` / `concurrent.futures` 包装超时。

---

### ISSUE-05: React 缺少 Error Boundary

- **严重程度**: 中 / 用户体验
- **位置**: `src/main.tsx`
- **描述**: 应用根组件没有 Error Boundary。一旦 React 组件树抛出未捕获异常，整个界面变为白屏，用户无法进行任何操作或恢复。
- **现状**:
  ```tsx
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
  ```
- **建议**: 创建 `ErrorBoundary` 类组件包裹 `<App />`，捕获异常后展示"程序遇到错误，请重启应用"的回退界面。

---

### ISSUE-06: 前端无测试覆盖

- **严重程度**: 中 / 代码质量
- **位置**: `src/` (App.tsx 614 行, api.ts 70 行)
- **描述**: 前端代码完全没有测试。后端 Python 有 13 个测试用例全部通过，但前端组件、API 调用、状态管理逻辑零覆盖。
- **建议**:
  1. 为 `api.ts` 添加单元测试（mock fetch）
  2. 为关键组件添加渲染测试 (Vitest + React Testing Library)
  3. 将 `npm run check` 扩展为包含前端测试

---

### ISSUE-07: 无代码检查 / 格式化工具

- **严重程度**: 中 / 工程规范
- **位置**: 项目根
- **描述**: 项目未配置任何代码检查或格式化工具，代码风格依赖开发者自觉。长期维护易产生不一致。
- **缺失项**:
  - JavaScript/TypeScript: 无 ESLint、Prettier
  - Python: 无 ruff、black、mypy
  - 无 `.editorconfig`
  - 无 pre-commit hooks
- **建议**:
  1. 添加 ESLint + Prettier 配置
  2. 添加 ruff 配置（替代 flake8 + isort + black）
  3. 添加 `.editorconfig`
  4. 配置 pre-commit hooks（可选）

---

### ISSUE-08: Python 依赖版本未锁定

- **严重程度**: 中 / 可复现性
- **位置**: `service/requirements.txt`
- **描述**: 所有依赖使用 `>=` 声明，不锁定版本上限。不同时间安装可能得到不同版本的依赖，导致行为不一致或运行错误。
- **现状**:
  ```
  fastapi>=0.115.0
  uvicorn>=0.32.0
  pydantic>=2.9.0
  akshare>=1.15.0
  pandas>=2.2.0
  pyinstaller>=6.11.0
  ```
- **建议**:
  1. 生成 `requirements.lock`（`pip freeze > requirements.lock`）
  2. 或迁移到 Poetry/uv 管理依赖
  3. 在发布脚本中使用 lock 文件安装

---

## 低优先级 (后续迭代改进)

### ISSUE-09: 死代码 - auth_health_passthrough 中间件

- **严重程度**: 低 / 代码整洁
- **位置**: `service/redfolio_service/main.py:64-66`
- **描述**: 定义并注册了一个 HTTP 中间件 `auth_health_passthrough`，但函数体仅执行 `call_next(request)`，未做任何鉴权或过滤。健康检查端点是通过不添加 `dependencies=[Depends(require_token)]` 来开放访问的，该中间件未贡献任何逻辑。
- **建议**: 删除该中间件定义和注册代码。

---

### ISSUE-10: extend_no_proxy_for_cn_sources 重复执行

- **严重程度**: 低 / 代码整洁
- **位置**: `service/redfolio_service/data_sources.py:38-51, 153`
- **描述**: 每次创建 `AkshareDataSource` 实例都会调用 `extend_no_proxy_for_cn_sources()` 修改环境变量。虽无害但浪费且不合语义（副作用在构造器中）。
- **建议**: 使用模块级标志或 `functools.cache` 确保仅执行一次；或将调用移至模块导入时。

---

### ISSUE-11: SQLite 未启用 WAL 模式

- **严重程度**: 低 / 性能
- **位置**: `service/redfolio_service/db.py:9-12`
- **描述**: 数据库连接未启用 WAL (Write-Ahead Logging) 模式。刷新操作涉及大量写入时，其他读取操作（如前端查询）可能被阻塞。
- **现状**:
  ```python
  connection.execute("PRAGMA foreign_keys = ON")
  ```
- **建议**: 增加 `connection.execute("PRAGMA journal_mode = WAL")`

---

### ISSUE-12: Python 进程无优雅关闭

- **严重程度**: 低 / 健壮性
- **位置**: `electron/main.cjs:160-163`
- **描述**: Electron 退出时直接 `pythonProcess.kill()` 终止 Python 服务进程，未给 FastAPI 优雅关闭（完成在途请求、关闭 DB 连接）的机会。
- **现状**:
  ```js
  app.on("before-quit", () => {
    if (pythonProcess && !pythonProcess.killed) {
      pythonProcess.kill();
    }
  });
  ```
- **建议**: 先发送 SIGTERM，等待超时后再 SIGKILL；或通过 HTTP 调用 FastAPI shutdown 端点。

---

### ISSUE-13: CORS 允许所有来源

- **严重程度**: 低 / 安全（当前影响极小，仅监听 127.0.0.1）
- **位置**: `service/redfolio_service/main.py:53-58`
- **描述**: `allow_origins=["*"]` 允许任意来源跨域请求。当前服务绑定在 127.0.0.1 上，外部不可达，实际风险极低。但若未来配置变更（如局域网共享），这可能成为安全隐患。
- **建议**: 将 `allow_origins` 限制为 Electron renderer 的实际来源。

---

### ISSUE-14: 无 Content-Security-Policy

- **严重程度**: 低 / 安全
- **位置**: `index.html`, FastAPI app
- **描述**: HTML 未设置 CSP meta 标签，FastAPI 未添加 CSP 响应头。在 Electron 中使用 `loadFile` 加载本地 HTML 时风险较低，但仍建议添加以遵循纵深防御原则。
- **建议**: 添加合理的 CSP meta 标签。

---

### ISSUE-15: 缺少 LICENSE 文件

- **严重程度**: 低 / 合规
- **位置**: 项目根
- **描述**: 项目无 LICENSE 文件，开源使用方无法明确自己的权利和义务。
- **建议**: 添加 LICENSE 文件（如 MIT、Apache-2.0）。

---

### ISSUE-16: 列表接口无分页

- **严重程度**: 低 / 扩展性
- **位置**: `service/redfolio_service/main.py:108-119` (transactions), `235-249` (dividends)
- **描述**: 交易流水和分红事件接口返回全量数据，不做分页限制。当前数据量小时无感，但长期使用后可能产生大量数据。
- **建议**: 添加 `limit`/`offset` 分页参数。

---

### ISSUE-17: 证券搜索无 debounce

- **严重程度**: 低 / 用户体验
- **位置**: `service/redfolio_service/main.py:73-74`
- **描述**: 前端若实现搜索联想功能，每次键入都会触发一次 SQL 查询。后端当前无问题，但需前端配合 debounce。
- **建议**: 前端在搜索输入时添加 300ms debounce。

---

### ISSUE-18: Path.mkdir 在每次连接时调用

- **严重程度**: 低 / 性能
- **位置**: `service/redfolio_service/db.py:8`
- **描述**: 每次创建数据库连接都执行 `Path(db_path).parent.mkdir(parents=True, exist_ok=True)`。虽然开销很小，但完全可以只在应用启动时执行一次。
- **建议**: 将目录创建逻辑移至 `AppState.__init__` 或 `create_app`。

---

## 统计

| 严重程度 | 数量 |
|----------|------|
| 高 | 2 |
| 中 | 6 |
| 低 | 10 |
| **合计** | **18** |

## 通过项

- TypeScript 类型检查通过
- Python 语法检查通过
- 13 个 Python 单元测试全部通过
- 构建产物正常产出
- 参数化 SQL 查询，无注入风险
- Token 鉴权 + contextIsolation 安全架构合理
- 打包配置完整（electron-builder + PyInstaller）
