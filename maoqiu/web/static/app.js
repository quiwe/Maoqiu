/**
 * 毛球 Web 前端。无构建步骤的原生 JS。
 *
 * 安全注意: 所有模型输出与工具输出都通过 textContent 写入 DOM,
 * 只有代码块做结构化渲染, 不使用 innerHTML 拼接远端内容, 避免 XSS。
 */
'use strict';

const state = {
  token: new URLSearchParams(location.search).get('token') || sessionStorage.getItem('maoqiu_token') || '',
  sessionId: null,
  sessions: [],
  config: null,
  streaming: false,
  lastMessage: null,
};

if (state.token) {
  sessionStorage.setItem('maoqiu_token', state.token);
  if (location.search.includes('token=')) {
    history.replaceState({}, '', location.pathname);
  }
}

const el = (id) => document.getElementById(id);
const messagesEl = el('messages');

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${state.token}`,
      ...(options.headers || {}),
    },
  });
  if (response.status === 401) {
    setStatus(false, '令牌无效，请用启动时打印的地址重新打开');
    throw new Error('unauthorized');
  }
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) { /* 忽略解析失败 */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function setStatus(ok, text) {
  el('status-dot').classList.toggle('online', Boolean(ok));
  el('status-text').textContent = text;
}

/* ------------------------------------------------------------------ */
/* 渲染                                                                */
/* ------------------------------------------------------------------ */

function renderTextInto(container, text) {
  // 按 ``` 分段, 奇数段为代码块。全部使用 textContent 写入。
  const parts = String(text).split(/```/);
  parts.forEach((part, index) => {
    if (!part) return;
    if (index % 2 === 1) {
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      const newline = part.indexOf('\n');
      code.textContent = newline > -1 ? part.slice(newline + 1) : part;
      pre.appendChild(code);
      const copy = document.createElement('button');
      copy.className = 'copy-button';
      copy.type = 'button';
      copy.textContent = '复制';
      copy.addEventListener('click', () => {
        navigator.clipboard.writeText(code.textContent).then(() => {
          copy.textContent = '已复制';
          setTimeout(() => { copy.textContent = '复制'; }, 1500);
        });
      });
      pre.appendChild(copy);
      container.appendChild(pre);
    } else {
      const p = document.createElement('p');
      p.textContent = part.trim();
      if (p.textContent) container.appendChild(p);
    }
  });
}

function addMessage(role, text) {
  const wrapper = document.createElement('article');
  wrapper.className = `message ${role}`;
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? '我' : 'M';
  const body = document.createElement('div');
  body.className = 'bubble';
  if (text) renderTextInto(body, text);
  wrapper.append(avatar, body);
  messagesEl.appendChild(wrapper);
  scrollToBottom();
  return body;
}

function addToolCard(name, args) {
  const details = document.createElement('details');
  details.className = 'tool-card';
  const summary = document.createElement('summary');
  const badge = document.createElement('span');
  badge.className = 'tool-badge';
  badge.textContent = '工具';
  const label = document.createElement('span');
  label.textContent = name;
  const spinner = document.createElement('span');
  spinner.className = 'tool-state running';
  spinner.textContent = '执行中';
  summary.append(badge, label, spinner);
  details.appendChild(summary);

  const argsPre = document.createElement('pre');
  argsPre.className = 'tool-args';
  argsPre.textContent = JSON.stringify(args, null, 2);
  details.appendChild(argsPre);

  messagesEl.appendChild(details);
  scrollToBottom();
  return { details, spinner };
}

function finishToolCard(card, payload) {
  if (!card) return;
  card.spinner.classList.remove('running');
  card.spinner.classList.add(payload.ok ? 'ok' : 'failed');
  card.spinner.textContent = payload.ok ? '成功' : '失败';
  const output = document.createElement('pre');
  output.className = 'tool-output';
  output.textContent = payload.ok ? (payload.data || '(无输出)') : (payload.error || '未知错误');
  card.details.appendChild(output);
  if (!payload.ok) card.details.open = true;
  scrollToBottom();
}

function addNotice(text, kind = 'info') {
  const div = document.createElement('div');
  div.className = `notice ${kind}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  scrollToBottom();
  return div;
}

function addWelcome() {
  const div = document.createElement('div');
  div.className = 'welcome';
  const h = document.createElement('h3');
  h.textContent = '开始和毛球对话';
  const p = document.createElement('p');
  p.textContent = '它可以读写工作目录内的文件、搜索代码、查看 Git 状态、执行命令和联网查资料。';
  const list = document.createElement('div');
  list.className = 'suggestions';
  ['这个项目的结构是什么样的？', '看看当前的 Git 改动', '找出所有 TODO 注释', '运行测试并解释失败原因'].forEach((text) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'suggestion';
    button.textContent = text;
    button.addEventListener('click', () => { el('prompt').value = text; el('prompt').focus(); });
    list.appendChild(button);
  });
  div.append(h, p, list);
  messagesEl.appendChild(div);
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

/* ------------------------------------------------------------------ */
/* 会话                                                                */
/* ------------------------------------------------------------------ */

function renderSessions() {
  const list = el('session-list');
  list.textContent = '';
  state.sessions.forEach((session) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `session-item${session.id === state.sessionId ? ' active' : ''}`;
    const title = document.createElement('span');
    title.className = 'session-title';
    title.textContent = session.title;
    const meta = document.createElement('span');
    meta.className = 'session-meta';
    meta.textContent = `${session.message_count} 条 · ${session.updated_at.replace('T', ' ').slice(5, 16)}`;
    item.append(title, meta);
    item.addEventListener('click', () => openSession(session.id));
    list.appendChild(item);
  });
}

async function loadSessions() {
  state.sessions = await api('/api/sessions');
  renderSessions();
}

async function openSession(sessionId) {
  const session = await api(`/api/sessions/${sessionId}`);
  state.sessionId = session.id;
  el('session-title').textContent = session.title;
  messagesEl.textContent = '';
  hideConfirmation();

  const visible = session.messages.filter((m) => m.role !== 'system');
  if (!visible.length) {
    addWelcome();
  } else {
    visible.forEach((message) => {
      if (message.role === 'user') {
        addMessage('user', message.content);
      } else if (message.role === 'assistant') {
        if (message.content) addMessage('assistant', message.content);
        (message.tool_calls || []).forEach((call) => {
          let args = {};
          try { args = JSON.parse(call.function.arguments || '{}'); } catch (_) { args = { raw: call.function.arguments }; }
          const card = addToolCard(call.function.name, args);
          card.spinner.classList.remove('running');
          card.spinner.textContent = '已完成';
        });
      } else if (message.role === 'tool') {
        const pre = document.createElement('pre');
        pre.className = 'tool-output standalone';
        pre.textContent = String(message.content || '').slice(0, 4000);
        messagesEl.appendChild(pre);
      }
    });
  }
  renderSessions();
  scrollToBottom();
}

async function newSession() {
  const session = await api('/api/sessions', { method: 'POST' });
  await loadSessions();
  await openSession(session.id);
  el('prompt').focus();
}

/* ------------------------------------------------------------------ */
/* 发送与流式接收                                                      */
/* ------------------------------------------------------------------ */

function showConfirmation(text) {
  el('confirmation-text').textContent = text;
  el('confirmation').hidden = false;
}

function hideConfirmation() {
  el('confirmation').hidden = true;
  el('confirmation-text').textContent = '';
}

function setStreaming(on) {
  state.streaming = on;
  el('send-button').disabled = on;
  el('prompt').disabled = on;
}

async function sendMessage(text, confirm = false) {
  if (!state.sessionId || state.streaming) return;
  const welcome = messagesEl.querySelector('.welcome');
  if (welcome) welcome.remove();

  state.lastMessage = text;
  if (!confirm) addMessage('user', text);
  hideConfirmation();
  setStreaming(true);

  let assistantBubble = null;
  const toolCards = new Map();
  let sawPolicyBlock = false;

  try {
    const response = await fetch(`/api/sessions/${state.sessionId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${state.token}` },
      body: JSON.stringify({ content: text, confirm }),
    });
    if (!response.ok || !response.body) throw new Error(`发送失败 (${response.status})`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';

      for (const chunk of chunks) {
        const eventMatch = chunk.match(/^event: (.+)$/m);
        const dataMatch = chunk.match(/^data: (.+)$/m);
        if (!eventMatch || !dataMatch) continue;
        const type = eventMatch[1].trim();
        let payload = {};
        try { payload = JSON.parse(dataMatch[1]); } catch (_) { continue; }

        if (type === 'text') {
          assistantBubble = addMessage('assistant', payload.content);
        } else if (type === 'tool_call') {
          toolCards.set(payload.call_id, addToolCard(payload.name, payload.arguments));
        } else if (type === 'tool_result') {
          finishToolCard(toolCards.get(payload.call_id), payload);
          const errorText = payload.error || '';
          if (!payload.ok && /需要确认|拒绝了操作|没有可用的确认通道/.test(errorText)) {
            sawPolicyBlock = true;
            showConfirmation(errorText);
          }
        } else if (type === 'error') {
          addNotice(payload.message, 'error');
        }
      }
    }
    if (sawPolicyBlock) {
      addNotice('这次操作需要你确认。点击“确认后重试”会重新发送同一请求并允许执行。', 'warn');
    }
  } catch (error) {
    addNotice(error.message || '请求出错', 'error');
  } finally {
    setStreaming(false);
    await loadSessions();
    const current = state.sessions.find((s) => s.id === state.sessionId);
    if (current) el('session-title').textContent = current.title;
    el('prompt').focus();
  }
}

/* ------------------------------------------------------------------ */
/* 设置                                                                */
/* ------------------------------------------------------------------ */

function fillSettings(config) {
  const form = el('settings-form');
  form.base_url.value = config.base_url || '';
  form.model_name.value = config.model_name || '';
  form.workspace.value = config.workspace || '';
  form.confirm_mode.value = config.confirm_mode || 'confirm';
  form.command_timeout.value = config.command_timeout ?? 60;
  form.max_output_chars.value = config.max_output_chars ?? 8000;
  form.allow_network_tools.checked = Boolean(config.allow_network_tools);
  form.max_history_messages.value = config.max_history_messages ?? 40;
  form.save_history.checked = Boolean(config.save_history);
  form.system_prompt.value = config.system_prompt || '';
  el('key-status').textContent = config.api_key_set ? '（已设置）' : '（未设置）';
}

function settingsPayload() {
  const form = el('settings-form');
  const payload = {
    base_url: form.base_url.value.trim(),
    model_name: form.model_name.value.trim(),
    workspace: form.workspace.value.trim(),
    confirm_mode: form.confirm_mode.value,
    command_timeout: Number(form.command_timeout.value),
    max_output_chars: Number(form.max_output_chars.value),
    allow_network_tools: form.allow_network_tools.checked,
    max_history_messages: Number(form.max_history_messages.value),
    save_history: form.save_history.checked,
    system_prompt: form.system_prompt.value,
  };
  const key = form.api_key.value.trim();
  if (key) payload.api_key = key;
  return payload;
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */

async function bootstrap() {
  let health;
  try {
    health = await (await fetch('/api/health')).json();
  } catch (_) {
    setStatus(false, '无法连接本地服务');
    return;
  }

  // 令牌只能来自启动时打印的 URL 或本标签页的 sessionStorage。
  // 不从 /api/health 领取令牌: 那是匿名接口, 返回令牌等于把权限公开。
  if (!state.token) {
    setStatus(false, '缺少访问令牌，请使用启动时终端打印的带 token 的地址打开');
    el('setup-view').hidden = true;
    el('app').hidden = true;
    return;
  }

  if (!health.configured) {
    el('setup-view').hidden = false;
    el('app').hidden = true;
    return;
  }

  el('setup-view').hidden = true;
  el('app').hidden = false;
  setStatus(true, `${health.model} · ${health.os}`);
  el('workspace-label').textContent = health.workspace;

  try {
    state.config = await api('/api/config');
    fillSettings(state.config);
    await loadSessions();
    if (state.sessions.length) {
      await openSession(state.sessions[0].id);
    } else {
      await newSession();
    }
  } catch (error) {
    setStatus(false, error.message);
  }
}

/* 事件绑定 */
el('composer').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = el('prompt').value.trim();
  if (!value) return;
  el('prompt').value = '';
  el('prompt').style.height = 'auto';
  sendMessage(value, false);
});

el('prompt').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    el('composer').requestSubmit();
  }
});

el('prompt').addEventListener('input', (event) => {
  event.target.style.height = 'auto';
  event.target.style.height = `${Math.min(event.target.scrollHeight, 200)}px`;
});

el('new-chat').addEventListener('click', () => newSession());

el('confirm-run').addEventListener('click', () => {
  if (state.lastMessage) sendMessage(state.lastMessage, true);
});
el('cancel-run').addEventListener('click', hideConfirmation);

el('clear-button').addEventListener('click', async () => {
  if (!state.sessionId || !window.confirm('清空当前会话的所有消息？')) return;
  await api(`/api/sessions/${state.sessionId}/clear`, { method: 'POST' });
  await loadSessions();
  await openSession(state.sessionId);
});

el('delete-button').addEventListener('click', async () => {
  if (!state.sessionId || !window.confirm('删除当前会话？此操作不可撤销。')) return;
  await api(`/api/sessions/${state.sessionId}`, { method: 'DELETE' });
  state.sessionId = null;
  await loadSessions();
  if (state.sessions.length) await openSession(state.sessions[0].id);
  else await newSession();
});

el('export-button').addEventListener('click', async () => {
  if (!state.sessionId) return;
  const response = await fetch(`/api/sessions/${state.sessionId}/export?format=markdown`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!response.ok) return;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${state.sessionId}.md`;
  link.click();
  URL.revokeObjectURL(url);
});

el('settings-button').addEventListener('click', () => {
  el('settings-status').textContent = '';
  el('settings-dialog').showModal();
});
el('close-settings').addEventListener('click', () => el('settings-dialog').close());
el('cancel-settings').addEventListener('click', () => el('settings-dialog').close());

el('settings-test').addEventListener('click', async () => {
  el('settings-status').textContent = '正在测试连接...';
  try {
    const result = await api('/api/config/test', { method: 'POST', body: JSON.stringify(settingsPayload()) });
    el('settings-status').textContent = result.message;
    el('settings-status').className = `form-status ${result.ok ? 'ok' : 'error'}`;
  } catch (error) {
    el('settings-status').textContent = error.message;
    el('settings-status').className = 'form-status error';
  }
});

el('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    state.config = await api('/api/config', { method: 'PUT', body: JSON.stringify(settingsPayload()) });
    fillSettings(state.config);
    el('settings-dialog').close();
    await bootstrap();
  } catch (error) {
    el('settings-status').textContent = error.message;
    el('settings-status').className = 'form-status error';
  }
});

/* 首次配置 */
async function submitSetup(testOnly) {
  const form = el('setup-form');
  const payload = {
    api_key: form.api_key.value.trim(),
    base_url: form.base_url.value.trim(),
    model_name: form.model_name.value.trim(),
  };
  const status = el('setup-status');
  if (!payload.api_key || !payload.base_url || !payload.model_name) {
    status.textContent = '三项都需要填写。';
    status.className = 'form-status error';
    return;
  }
  status.textContent = testOnly ? '正在测试连接...' : '正在保存...';
  status.className = 'form-status';
  try {
    const path = testOnly ? '/api/config/test' : '/api/config';
    const result = await api(path, { method: testOnly ? 'POST' : 'PUT', body: JSON.stringify(payload) });
    if (testOnly) {
      status.textContent = result.message;
      status.className = `form-status ${result.ok ? 'ok' : 'error'}`;
    } else {
      await bootstrap();
    }
  } catch (error) {
    status.textContent = error.message;
    status.className = 'form-status error';
  }
}

el('setup-test').addEventListener('click', () => submitSetup(true));
el('setup-form').addEventListener('submit', (event) => { event.preventDefault(); submitSetup(false); });

bootstrap();
