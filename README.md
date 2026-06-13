# RedFolio

RedFolio 是一个桌面红利持仓应用，用于记录 A 股股票和场内 ETF 的买入/卖出流水，计算股息率、预计本年税前红利收入，并展示持仓结构。

## 技术栈

- Electron + React + Vite
- 本地 Python FastAPI 数据服务
- SQLite 本机数据存储
- AKShare 自动拉取 A 股/ETF 行情和分红数据

## 本地开发

安装前端依赖：

```bash
npm install
```

安装 Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r service/requirements.txt
```

启动桌面应用：

```bash
npm run dev
```

运行检查：

```bash
npm run check
```

## 当前口径

- 支持 A 股股票和沪深场内 ETF。
- 交易流水手动录入，行情和分红手动点击刷新。
- 买入费用计入持仓成本，卖出按移动加权平均成本扣减。
- 股息率同时展示当前价格口径和个人成本口径。
- 预计本年红利为税前口径：当年已公告/已发生优先，未公告部分用近 12 个月现金分红估算。
- 数据保存在 Electron 用户数据目录下的 `redfolio.sqlite3`。


## 发布安装包

当前发布链路会先用 PyInstaller 把本地 Python 数据服务打包成 `redfolio-service`，再用 Electron Builder 生成桌面安装包。

安装发布依赖：

```bash
npm install
pip install -r service/requirements.txt
```

构建当前 Linux 系统的安装包：

```bash
npm run package:linux
```

Windows/macOS 产物需要在对应系统或 CI 上构建，因为 PyInstaller 生成的是平台相关的服务可执行文件：

```bash
npm run package:win
npm run package:mac
```

构建完成后，产物会出现在 `release/` 目录。Linux 当前配置会生成 AppImage、deb 和 tar.gz。

发布包内会携带 Python 服务可执行文件，其他设备安装后不需要单独安装 Python 或 AKShare。持仓数据仍保存在各设备本机的 Electron 用户数据目录。
