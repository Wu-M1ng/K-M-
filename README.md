# Kiro Account Manager

一个用于管理 Kiro 账号的 Web 应用，支持自动刷新 Token、机器码绑定，并提供 OpenAI 兼容的 API 接口 (2api)。

## 功能特性

- 🤖 **OpenAI 兼容接口** - 提供 `/v1/chat/completions` 和 `/v1/models`，支持流式输出
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

## 使用说明

### API 调用 (OpenAI 兼容)

您可以使用任何支持 OpenAI API 的客户端连接到本服务。

- **Base URL**: `http://your-domain.com/v1`
- **API Key**: 您的 `ADMIN_PASSWORD` (Bearer Token)
- **Model**: `claude-sonnet-4-5`, `claude-opus-4-5-20251101` 等

**示例 (Python):**

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your_secure_password"
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

### 导入账号

1. 点击"📥 导入账号"按钮
2. 粘贴你的 `kiro-accounts-2026-01-02.json` 文件内容
3. 点击"导入"按钮
4. 系统会自动为每个账号生成唯一的机器码

### 刷新 Token

- **单个账号**：点击账号卡片中的"🔄 刷新Token"按钮
- **所有账号**：点击顶部的"🔄 刷新所有Token"按钮
- **自动刷新**：系统会每小时自动刷新所有账号的 Token

### 导出账号

点击"📤 导出账号"按钮，下载包含所有账号信息的 JSON 文件

## API 接口列表

### 2API 接口 (OpenAI 兼容)
- `GET /v1/models` - 获取可用模型列表
- `POST /v1/chat/completions` - 对话补全 (支持流式)

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

## License

MIT
