# 1Cat Studio

自托管的 AI 工作台：管理本机模型与 1Cat-vLLM 服务，进行聊天、代码预览、Agent 任务和图像/视频创作。

## 源码许可

当前应用源码使用 [1Cat Studio Community Source License 1.0](LICENSE)：

- 允许个人使用、企业内部使用、学习、修改及符合条款的再分发。
- 未经书面授权，不得预装或捆绑在硬件设备中销售。
- 未经书面授权，不得换皮后作为自有软件产品销售。

这是附有限定销售限制的源码可用许可，不属于 OSI 定义的开源许可。
第三方组件和模型继续使用各自许可证；历史 AGPL 版本的既有授权不受影响。
实现来源、迁移边界及检查范围见 [SOURCE_ORIGIN.md](SOURCE_ORIGIN.md)。

## 从源码运行

需要 Python 3.12、Node.js 22.12+。以下命令在仓库根目录运行：

```sh
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r studio/requirements.lock
npm ci --prefix studio/frontend
npm run build:onecat --prefix studio/frontend
.venv/bin/python studio/scripts/prepare-agent.py
PYTHONPATH=studio/backend .venv/bin/python -m onecat
```

默认管理地址、安装、备份恢复和服务管理参见 [运行说明](studio/OPERATIONS.md)。
设置 `ONECAT_STUDIO_HOME` 可指定独立的数据目录。首次打开页面设置管理员密码。
模型权重、推理引擎和创作运行时按需独立安装。
不要把数据库、模型权重、访问密钥或生成的运行环境提交到源码仓库。

## 验证与打包

```sh
.venv/bin/pip install pytest pytest-asyncio
ONECAT_AUTO_GPU_ACTIONS=0 CUDA_VISIBLE_DEVICES= .venv/bin/python -m pytest -q studio/backend/onecat_tests
npm test --prefix studio/frontend
python3 studio/scripts/check-source.py
.venv/bin/python studio/scripts/package.py
```

离线安装包需要 `uv`，包含当前源码归档、应用许可证及第三方声明。
设置页面可以下载对应源码、查看许可证。浏览器验收脚本位于 `studio/scripts/`。

开发资料：[Agent](studio/AGENT.md)、[创作功能](studio/CREATIVE.md)、
[第三方声明](studio/NOTICE.md)、[独立实现验证](studio/docs/independent-source-validation.md)。
