# GitHub 构建与发布制品

`.github/workflows/site-release.yml` 在 Pull Request、推送到 `main` 和手动运行时执行。工作流使用 Node.js 22 与 Python 3.12，按锁文件安装依赖，再执行仓库中的 `scripts/prepare-site-release.mjs`。

准备脚本负责检查源码状态、运行测试、构建站点和打包，并输出两个文件：

- `site-release.tar`：Sites 可部署的构建产物，保留根目录 `.openai/hosting.json` 和 `dist/server/index.js` 及完整 `dist/` 输出层级。
- `release.json`：本次构建对应的源码版本和产物校验信息。

所有步骤成功后，Actions 保存名为 `site-release-<GitHub commit SHA>` 的制品，保留 14 天。Pull Request 的检查使用 GitHub 提供的合并测试提交；生产发布应使用合并后 `main` 对应的成功任务及其制品。

## 更新网站

1. 在分支中修改源码，提交 Pull Request。
2. 检查 `Verify and package Site` 成功后合并到 `main`。
3. 等待该 `main` 提交的构建任务成功。
4. 由 ChatGPT/Codex 发布任务取回该任务的完整制品，校验源码提交、Site ID 和产物哈希，使用 `scripts/sync-sites-source.mjs` 将同一提交同步到现有 Sites 源仓库，再通过原生 Sites 工具保存版本并部署。
5. 读取线上 `/api/build-info`，核对线上提交与本次发布记录一致。

Actions 的职责是验证和生成制品；生产发布由持有当前 Sites 授权的任务执行。工作流不保存 Sites 源码凭据、服务访问 Token 或报告写入密钥，也不调用未提供的部署 API。日报任务继续更新数据，不通过这条构建流程重复发布网站代码。

## 验证工作流

工作流合入 `main` 后，可在仓库 **Actions → Verify and package Site → Run workflow** 手动运行。检查任务结果为成功，制品中同时存在 `site-release.tar` 与 `release.json`，且其版本与运行记录的提交 SHA 一致。只有完成原生部署并核对线上版本后，才记录生产发布成功。
