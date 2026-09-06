# 经济学看板

个人使用的经济指标看板：展示国内外经济/金融指标，并用统计/机器学习方法做预测。

## 技术栈

- 后端：FastAPI + SQLAlchemy + MySQL（阿里云 RDS）+ APScheduler + statsmodels
- 前端：Next.js（App Router）+ TypeScript + Tailwind + shadcn/ui + Motion
- 数据源：akshare（股指/汇率/大宗商品）

## 本地运行

### 1. 配置数据库连接

复制 `backend/.env.example` 为 `backend/.env`，填入你的阿里云 RDS MySQL 连接信息：

```
DATABASE_URL=mysql+pymysql://用户名:密码@host:3306/数据库名?charset=utf8mb4
```

确保该 RDS 实例的白名单已加入本机公网 IP。

### 2. 启动后端

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

后端跑在 `http://localhost:8000`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端跑在 `http://localhost:3000`。

## 目录结构

见项目计划文档，或直接看 `backend/app` 和 `frontend/src` 下的代码组织。
