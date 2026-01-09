# Kiro Account Manager

一个用于管理 Kiro 账号的 Web 应用，支持自动刷新 Token 和机器码绑定，**现已整合 2API 功能**。

## 功能特性

### 账号管理
- 🔐 密码保护 - 通过环境变量设置管理密码
- 📥 导入/导出账号 JSON 数据
- 🔄 自动定时刷新所有账号 Token
- 🔑 为每个账号自动生成和绑定唯一机器码
- 📊 实时显示账号统计信息和额度使用情况
- 🎨 现代化的 Web 界面
- ☁️ 支持部署到 Koyeb

### 2API 功能 (NEW!)
- 🤖 **OpenAI 兼容 API** - `/v1/chat/completions` 和 `/v1/models`
- 🧠 **Anthropic 兼容 API** - `/v1/messages`
- 🔐 **API Key 管理** - 创建、查看、删除 API Keys
- 📡 **流式响应支持** - 支持 SSE (Server-Sent Events)
- 📈 **使用统计** - 追踪 API 调用和 Token 使用量
- 🔄 **自动账号轮换** - 智能选择最佳可用账号

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
# 必需
ADMIN_PASSWORD=your_secure_password

# 可选 - 2API 功能
ENCRYPTION_KEY=your_fernet_key
UPSTASH_REDIS_URL=redis://...

# 其他可选
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
  -e ENCRYPTION_KEY=your_fernet_key \
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
  -e ENCRYPTION_KEY=your_fernet_key \
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

### 账号管理

#### 导入账号

1. 点击"📥 导入账号"按钮
2. 粘贴你的 `kiro-accounts-2026-01-02.json` 文件内容
3. 点击"导入"按钮
4. 系统会自动为每个账号生成唯一的机器码

#### 刷新 Token

- **单个账号**：点击账号卡片中的"🔄 刷新Token"按钮
- **所有账号**：点击顶部的"🔄 刷新所有Token"按钮
- **自动刷新**：系统会每小时自动刷新所有账号的 Token

#### 管理机器码

- 每个账号会自动绑定一个唯一的 32 位机器码
- 点击"🔑 重新生成机器码"可以为账号生成新的机器码

#### 导出账号

点击"📤 导出账号"按钮，下载包含所有账号信息的 JSON 文件

### 2API 使用 (NEW!)

#### 1. 创建 API Key

1. 访问 `http://localhost:8000/api-keys`
2. 点击"创建新 API Key"
3. 输入名称和描述
4. **重要**: 创建后立即复制并保存 API Key，它只会显示一次！

#### 2. 使用 OpenAI 兼容 API

```bash
# 获取模型列表
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"

# 聊天补全 (非流式)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kiro-pro",
    "messages": [
      {"role": "user", "content": "你好，介绍一下你自己"}
    ]
  }'

# 聊天补全 (流式)
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kiro-pro",
    "messages": [
      {"role": "user", "content": "你好"}
    ],
    "stream": true
  }'
```

#### 3. 使用 Anthropic 兼容 API

```bash
# 消息接口 (非流式)
curl http://localhost:8000/v1/messages \
  -H "X-Api-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'

# 消息接口 (流式)
curl http://localhost:8000/v1/messages \
  -H "X-Api-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-3-5-sonnet-20241022",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "你好"}
    ],
    "stream": true
  }'
```

#### 4. 在代码中使用

**Python (OpenAI SDK):**
```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="http://localhost:8000/v1"
)

response = client.chat.completions.create(
    model="kiro-pro",
    messages=[
        {"role": "user", "content": "你好"}
    ]
)

print(response.choices[0].message.content)
```

**Python (Anthropic SDK):**
```python
from anthropic import Anthropic

client = Anthropic(
    api_key="YOUR_API_KEY",
    base_url="http://localhost:8000"
)

message = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "你好"}
    ]
)

print(message.content[0].text)
```

**JavaScript/TypeScript:**
```javascript
// OpenAI 兼容
const response = await fetch('http://localhost:8000/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    model: 'kiro-pro',
    messages: [
      {role: 'user', content: '你好'}
    ]
  })
});

const data = await response.json();
console.log(data.choices[0].message.content);
```

## API 接口

### 认证接口
- `GET /api/auth/check` - 检查认证状态
- `POST /api/auth/login` - 登录(需要密码)
- `POST /api/auth/logout` - 退出登录

### 账号管理接口(需要认证)
- `GET /api/accounts` - 获取所有账号
- `POST /api/accounts/import` - 导入账号
- `PUT /api/accounts/<id>` - 更新账号
- `DELETE /api/accounts/<id>` - 删除账号
- `POST /api/accounts/<id>/refresh` - 刷新账号 Token
- `POST /api/accounts/<id>/machine-id` - 重新生成机器码
- `GET /api/export` - 导出账号
- `GET /api/stats` - 获取统计信息

### 2API 接口 (NEW!)

#### API Key 管理 (需要认证)
- `GET /api/api-keys` - 获取所有 API Keys
- `POST /api/api-keys` - 创建新的 API Key
- `DELETE /api/api-keys/<id>` - 删除 API Key

#### OpenAI 兼容接口 (需要 API Key)
- `GET /v1/models` - 获取可用模型列表
- `POST /v1/chat/completions` - 聊天补全 (支持流式/非流式)

#### Anthropic 兼容接口 (需要 API Key)
- `POST /v1/messages` - 消息接口 (支持流式/非流式)

#### 使用统计 (需要认证)
- `GET /api/usage/logs` - 获取 API 使用日志

## 注意事项

- **必须设置 `ADMIN_PASSWORD` 环境变量**来保护系统访问
- 账号数据存储在 `accounts.json` 文件中
- API Keys 存储在 `api_keys.json` 文件中
- Token 会自动定时刷新，默认间隔 1 小时
- 机器码在导入时自动生成，每个账号唯一
- **API Key 只在创建时显示一次，请妥善保管**
- 2API 功能会自动选择使用率最低的活跃账号
- 建议定期备份账号数据
- 如果不设置密码，系统将允许无认证访问（不推荐用于生产环境）

## 环境变量

```bash
# 必需
ADMIN_PASSWORD=your_secure_password

# 可选
ACCOUNTS_FILE=accounts.json        # 账号数据文件
API_KEYS_FILE=api_keys.json        # API Keys 文件
USAGE_LOGS_FILE=usage_logs.json   # 使用日志文件
UPSTASH_REDIS_URL=redis://...      # Upstash Redis URL (可选)
ENCRYPTION_KEY=your_fernet_key     # API Key 加密密钥 (可选)
SECRET_KEY=your_random_secret_key  # Flask session 密钥
```

## 技术栈

- Backend: Flask + APScheduler
- Frontend: Vanilla JavaScript
- 2API: OpenAI & Anthropic 兼容接口
- Storage: JSON 文件 + Redis (可选)
- Containerization: Docker + Docker Compose
- CI/CD: GitHub Actions
- Deployment: Koyeb / Docker / 任何支持容器的平台

## License

MIT
