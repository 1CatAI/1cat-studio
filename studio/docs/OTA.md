# Studio 在线更新

0.5.0 起，管理员在 **设置 → Studio 更新** 中检查并确认更新。发布者只需向 VPS 发布一个签名安装包；每台已接入的 Studio 自行下载、校验和切换版本，无需逐台 SSH 部署。更新由用户确认，不会定时强制安装。

更新对象为 Studio 管理器、前端和随包组件。模型权重与独立推理环境不在 OTA 包内，沿用现有目录。活跃对话、Agent、创作任务及后台作业会阻止安装；加载着且没有请求的模型可保持运行。切换期间写操作暂时返回 503，管理器重启后浏览器继续查询进度。

## 首次接入已有源码部署

0.4.x 的源码部署没有新入口，必须一次性接入 0.5.0。保留原源码、systemd 主配置、其他 drop-in、模型和数据。使用从可信仓库取得的 0.5.0 源码和现有 Studio Python 执行：

```bash
/path/to/existing-studio/.venv/bin/python /path/to/0.5-source/studio/scripts/bootstrap-ota.py \
  --state "$HOME/.local/share/onecat-studio" \
  --prefix "$HOME/.local/share/onecat-studio-app" \
  --channel http://156.226.174.161:8089/onecat/studio/latest.json
```

先显示已验证的版本与摘要；加 `--install` 执行迁移。自定义部署需要填写实际 `--service`、`--host`、`--port`。服务必须为当前用户管理的 `onecat-studio[-名称].service`，并使用 `KillMode=process` 保留独立推理进程。非 systemd、容器及其他架构暂不支持此 OTA 路径。

源码版通过页面更新时也会明确确认切换到发行版。切换后继续编辑原源码不会改变线上应用，原源码目录仍可用于开发。

## 构建与签名

发布工作站需要 Node 22.12+、uv、Python 3.12。先提交源码，再从干净的提交构建。`--base` 和 `--output` 可放在容量充足的数据盘。

```bash
python studio/scripts/package.py --base /data/studio-build/base --output /data/studio-build/releases
python studio/scripts/publish-update.py \
  --bundle /data/studio-build/releases/onecat-studio-VERSION-linux-x86_64.tar.gz \
  --out /data/studio-build/channel \
  --base-url http://156.226.174.161:8089/onecat/studio \
  --signing-key /secure/offline/studio-release.pem --key-id studio-2026-09 \
  --notes '本次更新说明'
```

序号默认使用构建时间的 Unix 秒，也可给打包器传入单调增加的 `--sequence`。一个版本一旦发布，安装包、说明和序号都不可改写；需要修正时提交新源码并发新版本。每个包包含对应源码、许可证、源码提交号和序号。

下载的对应源码也可以离线重打包；没有 `.git` 时，用 `--source-commit` 记录原始提交号。缺少来源提交的离线包仍可本地安装，但不能签发为 OTA 更新。

私钥不进入 Git、安装包或 VPS；离线备份发布私钥并限制为发布账号可读。客户端信任 `studio/backend/onecat/data/update-keys.json` 中的 Ed25519 公钥。HTTP 镜像也必须通过签名和完整安装包 SHA256 验证；不能用未签名元数据替代。轮换密钥需先由旧密钥发布包含新公钥的客户端。

## VPS 发布顺序

现有 Nginx 通道对应 `/var/www/onecat-studio-updates/studio/`。先上传包和不可变版本清单，确认公网可下载，最后原子替换 `latest.json`。不要先覆盖 latest 再上传大包。

```bash
scp /data/studio-build/channel/onecat-studio-VERSION-linux-x86_64.tar.gz root@VPS:/var/www/onecat-studio-updates/studio/.bundle.upload
ssh root@VPS 'mv /var/www/onecat-studio-updates/studio/.bundle.upload /var/www/onecat-studio-updates/studio/onecat-studio-VERSION-linux-x86_64.tar.gz'
scp /data/studio-build/channel/VERSION.json root@VPS:/var/www/onecat-studio-updates/studio/VERSION.json
```

先让隔离测试实例的 `ONECAT_UPDATE_CHANNEL` 指向 `VERSION.json` 完成验证，再执行：

```bash
ssh root@VPS 'cp /var/www/onecat-studio-updates/studio/VERSION.json /var/www/onecat-studio-updates/studio/.latest.next && chmod 644 /var/www/onecat-studio-updates/studio/.latest.next && mv /var/www/onecat-studio-updates/studio/.latest.next /var/www/onecat-studio-updates/studio/latest.json'
```

正式发布者应串行执行这些步骤，不覆盖已有版本文件。需要撤回有问题的版本时，发布序号更大的修复版本；已见过较高序号的客户端拒绝重放较旧通道清单。

## 故障恢复与验证

下载可以断点续传；HTTP 服务器忽略 Range 返回 200 时从头重写。安装前校验空间、签名、包摘要及解包后身份，用临时数据目录探测 Python 与应用导入。

后台 `<服务名>-update.service` 独立于管理器，事务日志保存在 `<state>/updates/operations/<操作号>/transaction.json`。切换前记录原服务覆盖、current/previous 链接并备份 SQLite。新版本必须通过包含精确版本号的健康检查；失败或切换中断时恢复旧服务与数据库。恢复尚未完成则保持写入关闭，systemd 会继续重试。数据库回滚前额外保留故障版本数据库用于诊断。

```bash
systemctl --user status onecat-studio-update.service
journalctl --user -u onecat-studio-update.service
cat ~/.local/share/onecat-studio/updates/status.json
```

已完成的单元保持 enabled，以便重启后重放终态；不会重新下载或重复安装。当前版本、上一版本、下载缓存和事务备份暂不自动删除，运维可在确认运行稳定并保留恢复材料后归档。安装成功代表 Studio 健康检查通过，不代替具体模型或推理环境的验收。

本地验证：

```bash
PYTHONPATH=studio/backend .venv/bin/pytest -q studio/backend/onecat_tests/test_updates.py
npm --prefix studio/frontend run build:onecat
.venv/bin/python studio/scripts/check-studio-updates.py
```

发布前还需在隔离 systemd 实例上验证完整包下载、源码迁移、健康失败回滚和再次升级。不要用生产数据库做故障注入。
