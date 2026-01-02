# Kiro Account Manager

一个用于管理 Kiro 账号的 Web 应用，支持自动刷新 Token 和机器码绑定。

## 功能特性

- 📥 导入/导出账号 JSON 数据
- 🔄 自动定时刷新所有账号 Token
- 🔑 为每个账号自动生成和绑定唯一机器码
- 📊 实时显示账号统计信息和额度使用情况
- 🎨 现代化的 Web 界面
- ☁️ 支持部署到 Koyeb

## 本地运行

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 运行应用：
```bash
python app.py
```

3. 访问 http://localhost:8000

## 部署到 Koyeb

### 方法 1: 通过 Git 部署

1. 将代码推送到 GitHub 仓库

2. 在 Koyeb 控制台创建新应用：
   - 选择 GitHub 仓库
   - 构建器：Buildpack
   - 构建命令：`pip install -r requirements.txt`
   - 运行命令：`gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
   - 端口：8000

3. 设置环境变量（可选）：
   - `REFRESH_INTERVAL`: Token 刷新间隔（秒），默认 3600
   - `ACCOUNTS_FILE`: 账号文件路径，默认 accounts.json

### 方法 2: 通过 Docker 部署

1. 创建 Dockerfile（已包含在项目中）

2. 在 Koyeb 选择 Docker 部署方式

## 使用说明

### 导入账号

1. 点击"📥 导入账号"按钮
2. 粘贴你的 `kiro-accounts-2026-01-02.json` 文件内容
3. 点击"导入"按钮
4. 系统会自动为每个账号生成唯一的机器码

### 刷新 Token

- **单个账号**：点击账号卡片中的"🔄 刷新Token"按钮
- **所有账号**：点击顶部的"🔄 刷新所有Token"按钮
- **自动刷新**：系统会每小时自动刷新所有账号的 Token

### 管理机器码

- 每个账号会自动绑定一个唯一的 32 位机器码
- 点击"🔑 重新生成机器码"可以为账号生成新的机器码

### 导出账号

点击"📤 导出账号"按钮，下载包含所有账号信息的 JSON 文件

## API 接口

- `GET /api/accounts` - 获取所有账号
- `POST /api/accounts/import` - 导入账号
- `PUT /api/accounts/<id>` - 更新账号
- `DELETE /api/accounts/<id>` - 删除账号
- `POST /api/accounts/<id>/refresh` - 刷新账号 Token
- `POST /api/accounts/<id>/machine-id` - 重新生成机器码
- `GET /api/export` - 导出账号
- `GET /api/stats` - 获取统计信息

## 注意事项

- 账号数据存储在 `accounts.json` 文件中
- Token 会自动定时刷新，默认间隔 1 小时
- 机器码在导入时自动生成，每个账号唯一
- 建议定期备份账号数据

## 技术栈

- Backend: Flask + APScheduler
- Frontend: Vanilla JavaScript
- Deployment: Koyeb (支持 Buildpack 和 Docker)

## License

MIT
