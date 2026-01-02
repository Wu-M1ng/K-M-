# Kiro Account Manager

一个用于管理 Kiro 账号的 Web 应用，支持自动刷新 Token 和机器码绑定。

## 功能特性

- 🔐 密码保护 - 通过环境变量设置管理密码
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

2. 设置环境变量：
```bash
# Windows
set ADMIN_PASSWORD=your_secure_password

# Linux/Mac
export ADMIN_PASSWORD=your_secure_password
```

3. 运行应用：
```bash
python app.py
```

4. 访问 http://localhost:8000 并使用密码登录

## 部署方式

### 方法 1: Docker Compose（推荐）

1. 克隆仓库：
```bash
git clone <your-repo-url>
cd kiro-account-manager
```

2. 创建数据目录：
```bash
mkdir data
```

3. 设置环境变量（创建 `.env` 文件）：
```bash
ADMIN_PASSWORD=your_secure_password
REFRESH_INTERVAL=3600
SECRET_KEY=your_random_secret_key
```

4. 启动服务：
```bash
docker-compose up -d
```

5. 访问 http://localhost:8000

### 方法 2: Docker 手动部署

```bash
# 构建镜像
docker build -t kiro-account-manager .

# 运行容器
docker run -d \
  -p 8000:8000 \
  -e ADMIN_PASSWORD=your_secure_password \
  -e REFRESH_INTERVAL=3600 \
  -v $(pwd)/data:/app/data \
  --name kiro-account-manager \
  kiro-account-manager
```

### 方法 3: 使用预构建的 Docker 镜像

```bash
# 从 GitHub Container Registry 拉取
docker pull ghcr.io/yourusername/kiro-account-manager:latest

# 运行
docker run -d \
  -p 8000:8000 \
  -e ADMIN_PASSWORD=your_secure_password \
  -v $(pwd)/data:/app/data \
  ghcr.io/yourusername/kiro-account-manager:latest
```

### 方法 4: 部署到 Koyeb

#### 通过 Git 部署

1. 将代码推送到 GitHub 仓库

2. 在 Koyeb 控制台创建新应用：
   - 选择 GitHub 仓库
   - 构建器：Buildpack
   - 构建命令：`pip install -r requirements.txt`
   - 运行命令：`gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
   - 端口：8000

3. 设置环境变量：
   - `ADMIN_PASSWORD`: 管理密码（必需）
   - `REFRESH_INTERVAL`: Token 刷新间隔（秒），默认 3600
   - `SECRET_KEY`: Flask session 密钥（可选）

#### 通过 Docker 部署

1. 在 Koyeb 选择 Docker 部署方式
2. 使用镜像：`ghcr.io/yourusername/kiro-account-manager:latest`
3. 设置相同的环境变量

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

### 认证接口
- `GET /api/auth/check` - 检查认证状态
- `POST /api/auth/login` - 登录（需要密码）
- `POST /api/auth/logout` - 退出登录

### 账号管理接口（需要认证）
- `GET /api/accounts` - 获取所有账号
- `POST /api/accounts/import` - 导入账号
- `PUT /api/accounts/<id>` - 更新账号
- `DELETE /api/accounts/<id>` - 删除账号
- `POST /api/accounts/<id>/refresh` - 刷新账号 Token
- `POST /api/accounts/<id>/machine-id` - 重新生成机器码
- `GET /api/export` - 导出账号
- `GET /api/stats` - 获取统计信息

## 注意事项

- **必须设置 `ADMIN_PASSWORD` 环境变量**来保护系统访问
- 账号数据存储在 `accounts.json` 文件中
- Token 会自动定时刷新，默认间隔 1 小时
- 机器码在导入时自动生成，每个账号唯一
- 建议定期备份账号数据
- 如果不设置密码，系统将允许无认证访问（不推荐用于生产环境）

## GitHub Actions 自动构建

项目包含 GitHub Actions 工作流，可以自动构建和发布 Docker 镜像到 GitHub Container Registry。

### 设置步骤

1. **无需额外配置** - GitHub Actions 会自动使用 `GITHUB_TOKEN` 进行认证

2. 推送代码到 `main` 或 `master` 分支，自动触发构建

3. 镜像会发布到：
   - GitHub Container Registry: `ghcr.io/yourusername/kiro-account-manager`

### 版本标签

- `latest`: 最新的 main/master 分支构建
- `v1.0.0`: 语义化版本标签（推送 git tag 时触发）
- `main`: main 分支的最新构建

### 使镜像公开访问

默认情况下，镜像是私有的。要使其公开：

1. 访问 `https://github.com/yourusername/kiro-account-manager/pkgs/container/kiro-account-manager`
2. 点击 "Package settings"
3. 在 "Danger Zone" 中选择 "Change visibility"
4. 设置为 "Public"

## 技术栈

- Backend: Flask + APScheduler
- Frontend: Vanilla JavaScript
- Containerization: Docker + Docker Compose
- CI/CD: GitHub Actions
- Deployment: Koyeb / Docker / 任何支持容器的平台

## License

MIT
