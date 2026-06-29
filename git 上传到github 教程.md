# Git 上传 GitHub 教程 — Polymarket Jack BTC

## 1. 确保本地代码已提交

```bash
cd polymarket-jack-btc
git status

# 应该显示：
# On branch master
# nothing to commit, working tree clean
```

---

## 2. 在 GitHub 创建仓库

目标仓库：`https://github.com/jackproceo/polymarket-jack-btc`

如果还没创建：
1. 打开 https://github.com/jackproceo
2. 点击 **New** → 仓库名填 `polymarket-jack-btc`
3. 选择 **Private** 或 **Public**
4. **不要**勾选 "Add a README file" / ".gitignore"（本地已有）
5. 点击 **Create repository**

---

## 3. 添加远程仓库并推送

### 方式一：HTTPS（推荐，简单）

```bash
# 添加远程仓库
git remote add origin https://github.com/jackproceo/polymarket-jack-btc.git

# 切换到 main 分支（GitHub 默认分支名）
git branch -M main

# 推送
git push -u origin main
```

> 首次推送会提示输入 GitHub 用户名和 **Personal Access Token**（非密码）。
> 创建 Token：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token，勾选 `repo` 权限。

### 方式二：SSH

```bash
# 添加远程仓库
git remote add origin git@github.com:jackproceo/polymarket-jack-btc.git

# 切换分支
git branch -M main

# 推送
git push -u origin main
```

> 使用 SSH 需要先在 GitHub 设置 SSH Key：
> ```bash
> ssh-keygen -t ed25519 -C "your-email@example.com"
> cat ~/.ssh/id_ed25519.pub
> ```
> 将公钥添加到 GitHub → Settings → SSH and GPG keys

---

## 4. 验证推送结果

```bash
# 查看远程仓库
git remote -v

# 应该显示：
# origin  https://github.com/jackproceo/polymarket-jack-btc.git (fetch)
# origin  https://github.com/jackproceo/polymarket-jack-btc.git (push)
```

推送成功后，打开 `https://github.com/jackproceo/polymarket-jack-btc` 即可看到所有文件。

---

## 5. 后续更新

```bash
# 修改代码后
git add -A
git commit -m "描述这次的改动"
git push
```

---

## 6. 服务器拉取部署

```bash
# 服务器上克隆（首次）
git clone https://github.com/jackproceo/polymarket-jack-btc.git
cd polymarket-jack-btc

# 后续更新
git pull
docker compose up -d --build
```

---

## 当前仓库状态

| 项目 | 说明 |
|------|------|
| 仓库地址 | https://github.com/jackproceo/polymarket-jack-btc |
| 分支 | main |
| 文件数 | 35 个 |
| 提交数 | 2 个 commit |
| 忽略项 | .env, __pycache__, data/*.db, logs/*.log, .codebuddy/ |
