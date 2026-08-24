"""安全层: 路径沙箱、命令策略、日志脱敏。

这是整个项目最重要的一层。模型的输出永远视为不可信输入,
工具参数必须经过校验, 而不是直接透传给操作系统。

三态策略:
- allow   : 只读类操作, 直接执行
- confirm : 有副作用, 需要用户点头(auto 模式下自动通过)
- deny    : 不可逆或高破坏性, 任何模式都拒绝
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .errors import SandboxViolation

# --------------------------------------------------------------------------
# 日志与输出脱敏
# --------------------------------------------------------------------------

_SECRET_PATTERNS = (
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,})"),
    re.compile(r"\b(AKIA[0-9A-Z]{12,})"),
    re.compile(r"\b(ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"),
    re.compile(
        r"(?i)\b(api[_\-]?key|secret|token|password|passwd|authorization)"
        r"\s*[:=]\s*[\"']?([^\s\"',]{6,})"
    ),
)


def redact(text: str) -> str:
    """把疑似密钥的片段替换掉, 避免写进日志或回显到界面。"""
    if not text:
        return text
    result = text
    for pattern in _SECRET_PATTERNS:
        def _sub(match: re.Match[str]) -> str:
            groups = match.groups()
            secret = groups[-1]
            if not secret or len(secret) < 6:
                return match.group(0)
            return match.group(0).replace(secret, "***已脱敏***")

        result = pattern.sub(_sub, result)
    return result


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """限制输出长度, 防止一次命令输出撑爆上下文。"""
    if len(text) <= limit:
        return text, False
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.2) :]
    return f"{head}\n\n...[已截断 {len(text) - len(head) - len(tail)} 字符]...\n\n{tail}", True


# --------------------------------------------------------------------------
# 路径沙箱
# --------------------------------------------------------------------------

# 这些文件即使在工作目录内也不允许读写, 因为通常含有凭据。
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "config.json",
    "credentials",
    "credentials.json",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_dsa",
    ".htpasswd",
    "secrets.yaml",
    "secrets.yml",
}

SENSITIVE_DIR_PARTS = {".ssh", ".aws", ".gnupg", ".kube", "keychains", ".docker"}

SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}


def is_sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    if name in SENSITIVE_NAMES:
        return True
    if path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    parts = {part.lower() for part in path.parts}
    return bool(parts & SENSITIVE_DIR_PARTS)


def resolve_in_workspace(workspace: Path, user_path: str, *, allow_sensitive: bool = False) -> Path:
    """把模型给出的路径解析为工作目录内的绝对路径。

    同时防御 `..` 穿越和符号链接逃逸: 先 resolve, 再校验归属关系。
    """
    workspace = workspace.expanduser().resolve()
    raw = (user_path or "").strip()
    if not raw:
        raise SandboxViolation("路径不能为空。")

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (workspace / candidate).resolve()

    if resolved != workspace and workspace not in resolved.parents:
        raise SandboxViolation(
            f"路径 {raw} 超出了工作目录 {workspace}, 已拒绝。"
            "如需操作其他目录, 请在配置里修改 workspace。"
        )

    if not allow_sensitive and is_sensitive_path(resolved):
        raise SandboxViolation(
            f"{resolved.name} 属于敏感文件(可能包含密钥或凭据), 已拒绝访问。"
        )
    return resolved


# --------------------------------------------------------------------------
# 命令策略
# --------------------------------------------------------------------------


@dataclass
class Decision:
    action: str  # allow | confirm | deny
    reason: str = ""
    rule: str = ""

    @property
    def denied(self) -> bool:
        return self.action == "deny"

    @property
    def needs_confirmation(self) -> bool:
        return self.action == "confirm"


# 永久拒绝: 不可逆、影响整机或把 shell 交给外部脚本。
DENY_RULES: tuple[tuple[str, str], ...] = (
    (r"rm\s+(-[a-zA-Z]*\s+)*(-{1,2}no-preserve-root|/\s*$|/\s|~\s*$|\*\s*$)", "递归删除根目录或家目录"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", "递归强制删除"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\b[^|]*\bof=/dev/", "直接写入块设备"),
    (r"\b(diskpart|format)\s+[a-zA-Z]:", "格式化磁盘"),
    (r">\s*/dev/(sd|nvme|disk)", "覆写磁盘设备"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba)?sh", "把网络内容直接管道给 shell"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机或重启"),
    (r"\bchmod\s+(-R\s+)?777\s+/\s*$", "对根目录放开全部权限"),
    (r"\bchown\s+-R\s+[^\s]+\s+/\s*$", "递归修改根目录归属"),
    (r"\bRemove-Item\b[^\n]*-Recurse[^\n]*-Force[^\n]*(C:\\|\\\\|\$env:USERPROFILE)", "递归强删系统目录"),
    (r"\breg\s+delete\b", "删除注册表项"),
    (r"\b(DROP\s+DATABASE|DROP\s+TABLE|TRUNCATE\s+TABLE)\b", "删除数据库对象"),
    (r"\bgit\s+push\b[^\n]*(--force|-f)\b", "强制推送会覆盖远端历史"),
    (r"\bhistory\s+-c\b", "清空 shell 历史"),
    (r"\b(cat|type|less|more|Get-Content)\b[^\n]*(\.ssh/|id_rsa|\.aws/credentials|\.env\b|config\.json)", "读取凭据文件"),
)

# 需要确认: 有副作用但属于正常开发操作。
CONFIRM_RULES: tuple[tuple[str, str], ...] = (
    (r"\b(rm|rmdir|del|erase|unlink)\b", "删除文件或目录"),
    (r"\bRemove-Item\b", "删除文件或目录"),
    (r"\b(mv|move|rename|ren)\b", "移动或重命名"),
    (r"\bsudo\b|\brunas\b", "提权执行"),
    (r"\b(pip|pip3|npm|pnpm|yarn|brew|apt|apt-get|yum|dnf|choco|winget|cargo|gem|go)\s+"
     r"(install|add|remove|uninstall|update|upgrade)\b", "安装或卸载依赖"),
    (r"\bgit\s+(push|reset|clean|checkout\s+--|restore|rebase|merge|revert|stash\s+drop|branch\s+-D)\b",
     "改写工作区或远端状态"),
    (r"\b(chmod|chown|chgrp|icacls|attrib)\b", "修改权限"),
    (r"\b(kill|killall|pkill|taskkill|Stop-Process)\b", "结束进程"),
    (r"\b(systemctl|service|launchctl|sc\s+(start|stop|config))\b", "操作系统服务"),
    (r"\b(crontab|schtasks|at)\b", "修改定时任务"),
    (r"\b(docker|kubectl|helm|terraform|aws|gcloud|az)\b", "操作容器或云资源"),
    (r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|nc|ncat|ssh|scp|rsync|ftp)\b", "发起网络访问或传输"),
    (r">>?\s*[^\s|]+", "重定向写入文件"),
    (r"\b(tee|truncate|shred)\b", "覆写文件内容"),
    (r"\b(python|python3|node|deno|bun|ruby|perl|php|Rscript)\b\s+-\w*c\b", "执行内联脚本"),
    (r"\bnpm\s+publish\b|\btwine\s+upload\b", "对外发布产物"),
    (r"\bgit\s+config\b", "修改 git 配置"),
)

# 明确的只读命令, 直接放行。
ALLOW_RULES: tuple[tuple[str, str], ...] = (
    (r"^\s*(ls|dir|pwd|cd|tree|Get-ChildItem|Get-Location)\b", "查看目录"),
    (r"^\s*(cat|type|head|tail|wc|nl|Get-Content)\b", "查看文件内容"),
    (r"^\s*(grep|rg|findstr|Select-String|find|where|which|whereis|Get-Command)\b", "搜索"),
    (r"^\s*git\s+(status|log|diff|show|branch|remote|describe|blame|shortlog|ls-files|rev-parse|tag)\b",
     "查看 git 信息"),
    (r"^\s*(echo|date|whoami|hostname|uname|uptime|df|du|free|env|printenv|systeminfo|ver)\b", "查看环境信息"),
    (r"^\s*\w[\w.\-/\\]*\s+(--version|-V|--help|-h|version)\s*$", "查看版本或帮助"),
    (r"^\s*(pip|pip3|npm|pnpm|yarn|brew|cargo|go)\s+(list|ls|show|info|outdated|why|--version)\b", "查看依赖信息"),
    (r"^\s*(pytest|python\s+-m\s+pytest|npm\s+test|npm\s+run\s+test|go\s+test|cargo\s+test)\b", "运行测试"),
    (r"^\s*(ruff|flake8|black\s+--check|mypy|eslint|tsc\s+--noEmit)\b", "静态检查"),
)

# 命令串联时逐段判断, 避免 "ls && rm -rf ." 被整体判成只读。
_SPLIT_PATTERN = re.compile(r"&&|\|\||;|\n|\|")


def _classify_segment(segment: str) -> Decision:
    text = segment.strip()
    if not text:
        return Decision("allow", "空命令", "empty")

    for pattern, reason in DENY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return Decision("deny", reason, pattern)

    for pattern, reason in CONFIRM_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return Decision("confirm", reason, pattern)

    for pattern, reason in ALLOW_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return Decision("allow", reason, pattern)

    # 默认保守: 不认识的命令一律先问用户。
    return Decision("confirm", "未在白名单中的命令", "default")


def classify_command(command: str) -> Decision:
    """对完整命令行做三态判定, 取所有分段中最严格的结果。"""
    if not command or not command.strip():
        return Decision("deny", "命令为空", "empty")

    if "\x00" in command:
        return Decision("deny", "命令包含空字节", "nul")

    # 先对整条命令行做 deny 判定: 有些危险模式跨越管道或分隔符
    # (例如 curl ... | sh), 拆分后单段就匹配不上了。
    for pattern, reason in DENY_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            return Decision("deny", reason, pattern)

    decisions = [_classify_segment(part) for part in _SPLIT_PATTERN.split(command)]
    for decision in decisions:
        if decision.denied:
            return decision
    for decision in decisions:
        if decision.needs_confirmation:
            return decision
    return decisions[0] if decisions else Decision("confirm", "无法解析", "default")


# --------------------------------------------------------------------------
# 网络访问校验(防 SSRF)
# --------------------------------------------------------------------------

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localdomain")


def validate_url(url: str) -> str:
    """只允许 http/https, 并阻断指向内网与云元数据地址的请求。"""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise SandboxViolation("只允许 http 或 https 链接。")
    host = parsed.hostname
    if not host:
        raise SandboxViolation("链接缺少主机名。")

    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(_BLOCKED_HOST_SUFFIXES):
        raise SandboxViolation(f"拒绝访问内网地址 {host}。")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SandboxViolation(f"无法解析域名 {host}: {exc}") from exc

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or str(ip) == "169.254.169.254"
        ):
            raise SandboxViolation(f"{host} 解析到内网地址 {ip}, 已拒绝(防止 SSRF)。")
    return url.strip()
