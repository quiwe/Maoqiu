# Security Policy

## Scope

毛球是本机单用户开发助手，不是公网 shell 服务，也不是强隔离沙箱。

## Default safeguards

- Web 默认绑定 `127.0.0.1`。
- Web API 使用每次启动生成的 Bearer Token。
- 副作用操作默认需要确认。
- 高风险命令直接拒绝。
- 文件访问限制在 `workspace`，并检查符号链接逃逸。
- 凭据与密钥文件默认不可访问。
- 命令有超时、退出码和输出上限。
- 网络工具阻止 localhost、私网和云元数据地址。

## Operator requirements

- 不要把服务监听到 `0.0.0.0`，除非已部署反向代理、TLS、强认证和额外隔离。
- 不要分享带 `token` 参数的 Web 地址。
- 不要在不可信仓库中启用 `confirm_mode=auto`。
- 不要把 `.maoqiu/`、`config.json` 或环境变量文件提交到 Git。
- 不要把容器当作足够的命令执行沙箱。

## Reporting

请不要公开敏感漏洞利用细节。请提供复现条件、影响范围和建议修复方向。
