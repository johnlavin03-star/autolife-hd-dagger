const state = {
  config: null,
  websocket: null,
  reconnectTimer: null,
  rtcPeer: null,
  realtimeChannel: null,
  realtimeReconnectTimer: null,
  realtimeSequence: 0,
  xrSession: null,
  brandHud: null,
  headText: null,
  vrNotice: null,
  vrNoticeTimer: null,
  teleop: {
    available: false,
    status: null,
    requestPending: false,
    holdTimer: null,
    holdTriggered: false,
    pollTimer: null,
    lastRequestMessage: '',
    enableAfterFreshVr: false,
    enableAfterFreshVrDeadline: 0,
    lastHgDaggerMode: null,
    lastHgDaggerHapticToken: null
  },
  image: {
    enabled: false,
    cameras: [],
    states: new Map(),
    panels: new Map(),
    opacity: 0.82,
    renderStarted: false
  }
};

function setStatus(message) {
  const statusText = document.getElementById('statusText');
  if (statusText) statusText.textContent = message;
}

function ensureHgDaggerVrNotice() {
  if (state.vrNotice) return state.vrNotice;
  const cameraEl = document.querySelector('a-scene')?.camera?.el;
  if (!cameraEl) return null;
  const notice = document.createElement('a-text');
  notice.setAttribute('id', 'hg-dagger-vr-notice');
  notice.setAttribute('align', 'center');
  notice.setAttribute('anchor', 'center');
  notice.setAttribute('baseline', 'center');
  notice.setAttribute('color', '#7DFFCF');
  notice.setAttribute('width', '1.35');
  notice.setAttribute('wrap-count', '28');
  notice.setAttribute('position', '0 -0.25 -0.8');
  notice.setAttribute('visible', false);
  cameraEl.appendChild(notice);
  state.vrNotice = notice;
  return notice;
}

function showHgDaggerVrNotice(message, durationMs = 3500, color = '#7DFFCF') {
  setStatus(message);
  const notice = ensureHgDaggerVrNotice();
  if (!notice) return;
  if (state.vrNoticeTimer) window.clearTimeout(state.vrNoticeTimer);
  notice.setAttribute('color', color);
  notice.setAttribute('value', String(message));
  notice.setAttribute('visible', true);
  state.vrNoticeTimer = window.setTimeout(() => {
    if (state.vrNotice === notice) notice.setAttribute('visible', false);
    state.vrNoticeTimer = null;
  }, Math.max(1000, Number(durationMs) || 3500));
}

function pulseHgDaggerControllers(intensity = 0.7, durationMs = 180) {
  for (const source of Array.from(state.xrSession?.inputSources || [])) {
    const actuator = source?.gamepad?.hapticActuators?.[0]
      || source?.gamepad?.vibrationActuator;
    try {
      if (typeof actuator?.pulse === 'function') {
        void actuator.pulse(intensity, durationMs);
      } else if (typeof actuator?.playEffect === 'function') {
        void actuator.playEffect('dual-rumble', {
          duration: durationMs,
          strongMagnitude: intensity,
          weakMagnitude: intensity * 0.6
        });
      }
    } catch (_) { /* Haptics are optional in WebXR runtimes. */ }
  }
}

function renderHgDaggerStatus() {
  const hgDagger = state.teleop.status?.hg_dagger;
  const mode = String(hgDagger?.mode || '');
  const hapticToken = hgDagger?.haptic_token;
  if (!mode) return;
  if (
    state.teleop.lastHgDaggerMode === mode
    && state.teleop.lastHgDaggerHapticToken === hapticToken
  ) return;
  const warningModes = new Set([
    'FAILURE_HOLD', 'EXPERT_RELEASE_REQUIRED', 'ESTOP'
  ]);
  showHgDaggerVrNotice(
    hgDagger.prompt || `HG-DAGGER: ${mode}`,
    mode === 'EXPERT_RELEASE_REQUIRED' ? 7000 : 4500,
    warningModes.has(mode) ? '#FFB86B' : '#7DFFCF'
  );
  if (
    state.xrSession
    && ['EXPERT_RELEASE_REQUIRED', 'EXPERT_READY'].includes(mode)
  ) {
    pulseHgDaggerControllers(
      mode === 'EXPERT_RELEASE_REQUIRED' ? 0.9 : 0.55,
      mode === 'EXPERT_RELEASE_REQUIRED' ? 260 : 140
    );
  }
  state.teleop.lastHgDaggerMode = mode;
  state.teleop.lastHgDaggerHapticToken = hapticToken ?? null;
}

function setServerUrl() {
  const serverUrl = document.getElementById('serverUrl');
  if (serverUrl) serverUrl.textContent = window.location.origin;
}

function hardwareControlIsOnOrBusy() {
  const teleop = state.teleop.status;
  const backendState = String(teleop?.state || '').toLowerCase();
  return Boolean(
    teleop?.hardware_enabled
    || teleop?.hardware_enable_pending
    || teleop?.quick_reset?.active
    || ['enabling', 'enabling_arms', 'resetting', 'resetting_arms', 'active', 'armed'].includes(backendState)
  );
}

function vrInputIsFresh(teleop = state.teleop.status) {
  const age = Number(teleop?.vr_age);
  const trackedHands = teleop?.tracked_hands;
  const configuredTimeout = Number(state.config?.vr?.input_fresh_timeout_seconds);
  const maximumAge = Number.isFinite(configuredTimeout) && configuredTimeout > 0
    ? configuredTimeout
    : 0.8;
  return Boolean(
    teleop?.vr_input_fresh === true
    && Array.isArray(trackedHands)
    && trackedHands.length > 0
    && Number.isFinite(age)
    && age >= 0
    && age <= maximumAge
  );
}

function cancelDeferredHardwareEnable(message = '') {
  state.teleop.enableAfterFreshVr = false;
  state.teleop.enableAfterFreshVrDeadline = 0;
  if (message) state.teleop.lastRequestMessage = message;
}

function setHardwareButtonText(button, text) {
  if (button) button.innerHTML = `<span>${text}</span>`;
}

function renderHardwareControl() {
  const card = document.getElementById('hardwareControl');
  const label = document.getElementById('hardwareStatusLabel');
  const detail = document.getElementById('hardwareStatusDetail');
  const button = document.getElementById('hardwareToggleButton');
  if (!card || !label || !detail || !button) return;

  card.classList.remove('is-off', 'is-busy', 'is-on', 'is-error');
  button.classList.remove('is-disable-action');
  const teleop = state.teleop.status;
  const backendState = String(teleop?.state || '').toLowerCase();
  const websocketReady = state.websocket?.readyState === WebSocket.OPEN;
  const controlling = hardwareControlIsOnOrBusy();
  const backendReason = teleop?.backend?.reason || teleop?.backend?.detail || '';

  if (!teleop) {
    card.classList.add('is-error');
    label.textContent = '遥操节点状态未连接';
    detail.textContent = state.teleop.lastRequestMessage || '请确认遥操功能包已经启动。';
    setHardwareButtonText(button, '等待遥操节点');
  } else if (teleop.dry_run) {
    card.classList.add('is-off');
    label.textContent = '当前为模拟模式';
    detail.textContent = 'dry_run:=true，不会向真机发送指令。';
    setHardwareButtonText(button, '模拟模式不可使能');
  } else if (state.teleop.enableAfterFreshVr) {
    card.classList.add('is-busy');
    label.textContent = '等待 VR 手柄实时姿态';
    detail.textContent = '使能授权已确认。正在进入 VR；唤醒手柄后才会安全使能真机。';
    setHardwareButtonText(button, '取消等待使能');
  } else if (
    teleop.hardware_enable_pending
    || teleop.quick_reset?.active
    || ['enabling', 'enabling_arms', 'resetting', 'resetting_arms'].includes(backendState)
  ) {
    card.classList.add('is-busy');
    const enabling = teleop.hardware_enable_pending || backendState.startsWith('enabling');
    label.textContent = enabling ? '正在全身复位并安全使能' : '正在快速复位';
    detail.textContent = backendReason || teleop.detail || '正在处理，请保持机器人周围安全。';
    button.classList.add('is-disable-action');
    setHardwareButtonText(button, '立即关闭真机遥操');
  } else if (teleop.hardware_enabled) {
    card.classList.add('is-on');
    label.textContent = ['active', 'armed'].includes(backendState) ? '真机遥操中' : '真机遥操已使能';
    detail.textContent = teleop.detail || '先松开双手握把，再按住需要控制的一侧。';
    button.classList.add('is-disable-action');
    setHardwareButtonText(button, '关闭真机遥操');
  } else if (['fault', 'e_stop'].includes(backendState)) {
    card.classList.add('is-error');
    label.textContent = backendState === 'e_stop' ? '急停保护已锁定' : '真机使能失败';
    detail.textContent = state.teleop.lastRequestMessage || backendReason || teleop.detail;
    setHardwareButtonText(button, '排除故障后按住 1 秒重试');
  } else if (!vrInputIsFresh(teleop)) {
    card.classList.add('is-off');
    label.textContent = '等待 VR 手柄';
    detail.textContent = state.teleop.lastRequestMessage
      || '当前只有网页连接，没有实时手柄姿态。长按后会进入 VR，检测到手柄才使能。';
    setHardwareButtonText(button, '按住 1 秒，全身复位后使能');
  } else {
    card.classList.add('is-off');
    label.textContent = '真机遥操已关闭';
    detail.textContent = state.teleop.lastRequestMessage || backendReason || teleop.detail || '机械臂不会跟随 VR 手柄。';
    setHardwareButtonText(button, '按住 1 秒，全身复位后使能');
  }

  button.disabled = Boolean(
    state.teleop.requestPending
    || !state.teleop.available
    || !websocketReady
    || teleop?.dry_run
  );
  if (state.teleop.requestPending) {
    setHardwareButtonText(button, controlling ? '正在关闭……' : '正在请求使能……');
  } else if (!websocketReady && !controlling) {
    setHardwareButtonText(button, '等待 VR 数据连接');
  } else if (!state.teleop.available && !controlling) {
    setHardwareButtonText(button, '真机使能服务未连接');
  }
}

async function refreshHardwareStatus() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.teleop.available = Boolean(payload.hardwareControlAvailable);
    state.teleop.status = payload.teleop || null;
    renderHgDaggerStatus();
  } catch (error) {
    state.teleop.available = false;
    state.teleop.status = null;
    state.teleop.lastRequestMessage = `状态读取失败：${error.message}`;
  }
  renderHardwareControl();
  void maybeEnableAfterFreshVr();
}

async function maybeEnableAfterFreshVr() {
  if (
    !state.teleop.enableAfterFreshVr
    || state.teleop.requestPending
    || hardwareControlIsOnOrBusy()
  ) return;
  if (Date.now() > state.teleop.enableAfterFreshVrDeadline) {
    cancelDeferredHardwareEnable('等待 VR 手柄超时，真机未使能。请重新长按并进入 VR。');
    setStatus('未检测到实时 VR 手柄，真机保持关闭。');
    renderHardwareControl();
    return;
  }
  if (!vrInputIsFresh()) return;
  cancelDeferredHardwareEnable('已检测到实时 VR 手柄，正在执行安全使能。');
  await requestHardwareEnabled(true);
}

async function requestHardwareEnabled(enabled) {
  if (state.teleop.requestPending) return;
  if (!enabled) cancelDeferredHardwareEnable();
  state.teleop.requestPending = true;
  state.teleop.lastRequestMessage = '';
  renderHardwareControl();
  try {
    const response = await fetch('/api/hardware-control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = { message: `HTTP ${response.status}` };
    }
    state.teleop.lastRequestMessage = payload.message || (enabled ? '使能请求已发送' : '关闭请求已发送');
    if (!response.ok || !payload.success) {
      throw new Error(state.teleop.lastRequestMessage);
    }
  } catch (error) {
    state.teleop.lastRequestMessage = error.message;
    setStatus(`真机遥操切换失败：${error.message}`);
  } finally {
    state.teleop.requestPending = false;
    await refreshHardwareStatus();
  }
}

function cancelHardwareEnableHold() {
  if (state.teleop.holdTimer !== null) {
    window.clearTimeout(state.teleop.holdTimer);
    state.teleop.holdTimer = null;
  }
  const button = document.getElementById('hardwareToggleButton');
  if (button) button.classList.remove('is-holding');
  if (!state.teleop.requestPending) renderHardwareControl();
}

function abortHardwareEnableHold() {
  const confirmed = state.teleop.holdTriggered;
  state.teleop.holdTriggered = false;
  cancelHardwareEnableHold();
  if (confirmed) {
    cancelDeferredHardwareEnable('使能确认已取消，真机保持关闭。');
    renderHardwareControl();
  }
}

function finishHardwareEnableHold(event) {
  const confirmed = state.teleop.holdTriggered;
  cancelHardwareEnableHold();
  if (!confirmed) return;
  if (event) event.preventDefault();
  if (vrInputIsFresh()) {
    cancelDeferredHardwareEnable('已检测到实时 VR 手柄，正在执行安全使能。');
    void requestHardwareEnabled(true);
    return;
  }
  setStatus('授权已确认；正在进入 VR，检测到手柄实时姿态后自动使能。');
  void enterVr();
}

function beginHardwareEnableHold(event) {
  const button = document.getElementById('hardwareToggleButton');
  if (
    !button
    || button.disabled
    || hardwareControlIsOnOrBusy()
    || state.teleop.enableAfterFreshVr
  ) return;
  event.preventDefault();
  if (state.teleop.holdTimer !== null) return;
  state.teleop.holdTriggered = false;
  button.classList.remove('is-holding');
  void button.offsetWidth;
  button.classList.add('is-holding');
  setHardwareButtonText(button, '继续按住以确认使能');
  state.teleop.holdTimer = window.setTimeout(() => {
    state.teleop.holdTimer = null;
    state.teleop.holdTriggered = true;
    state.teleop.enableAfterFreshVr = true;
    state.teleop.enableAfterFreshVrDeadline = Date.now() + 15000;
    state.teleop.lastRequestMessage = '使能授权已确认，等待实时 VR 手柄姿态。';
    button.classList.remove('is-holding');
    renderHardwareControl();
  }, 1000);
}

function setupHardwareControl() {
  const button = document.getElementById('hardwareToggleButton');
  if (!button) return;
  button.addEventListener('pointerdown', beginHardwareEnableHold);
  button.addEventListener('pointerup', finishHardwareEnableHold);
  button.addEventListener('pointercancel', abortHardwareEnableHold);
  button.addEventListener('pointerleave', event => {
    if (event.buttons) abortHardwareEnableHold();
  });
  button.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') beginHardwareEnableHold(event);
  });
  button.addEventListener('keyup', event => {
    if (event.key === 'Enter' || event.key === ' ') finishHardwareEnableHold(event);
  });
  button.addEventListener('click', event => {
    if (state.teleop.holdTriggered) {
      state.teleop.holdTriggered = false;
      event.preventDefault();
      return;
    }
    if (state.teleop.enableAfterFreshVr) {
      cancelDeferredHardwareEnable('等待使能已取消，真机保持关闭。');
      renderHardwareControl();
      return;
    }
    if (hardwareControlIsOnOrBusy()) {
      requestHardwareEnabled(false);
      return;
    }
    event.preventDefault();
    state.teleop.lastRequestMessage = '需要持续按住按钮 1 秒才能使能。';
    renderHardwareControl();
  });
  refreshHardwareStatus();
  state.teleop.pollTimer = window.setInterval(refreshHardwareStatus, 400);
}

async function loadConfig() {
  const response = await fetch('/api/config');
  state.config = await response.json();
  const imageConfig = state.config?.vr_images || state.config?.vr_image || {};
  state.image.enabled = Boolean(imageConfig.enabled);
  state.image.opacity = Number.isFinite(Number(imageConfig.opacity)) ? Number(imageConfig.opacity) : 0.82;
  state.image.cameras = imageConfig.cameras || (imageConfig.image_key ? [{ id: 'front', ...imageConfig }] : []);
  return state.config;
}

function websocketUrl() {
  const path = state.config?.network?.websocket_path || '/ws';
  return `wss://${window.location.host}${path}`;
}

function connectWebSocket() {
  if (state.websocket && [WebSocket.CONNECTING, WebSocket.OPEN].includes(state.websocket.readyState)) return;
  const url = websocketUrl();
  state.websocket = new WebSocket(url);
  state.websocket.onopen = () => {
    if (state.reconnectTimer) window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
    setStatus(`VR data connected: ${url}`);
    refreshHardwareStatus();
    void connectRealtimeChannel();
  };
  state.websocket.onerror = () => setStatus('VR data connection error');
  state.websocket.onclose = () => {
    closeRealtimeChannel();
    setStatus('VR data disconnected; reconnecting...');
    state.teleop.lastRequestMessage = 'VR 数据连接已断开，后台正在自动关闭真机遥操。';
    renderHardwareControl();
    if (!state.reconnectTimer) {
      state.reconnectTimer = window.setTimeout(() => {
        state.reconnectTimer = null;
        connectWebSocket();
      }, 1000);
    }
  };
}

function scheduleRealtimeReconnect() {
  if (
    state.realtimeReconnectTimer
    || state.websocket?.readyState !== WebSocket.OPEN
  ) return;
  state.realtimeReconnectTimer = window.setTimeout(() => {
    state.realtimeReconnectTimer = null;
    void connectRealtimeChannel();
  }, 2000);
}

function closeRealtimeChannel() {
  if (state.realtimeReconnectTimer) {
    window.clearTimeout(state.realtimeReconnectTimer);
    state.realtimeReconnectTimer = null;
  }
  const channel = state.realtimeChannel;
  const peer = state.rtcPeer;
  state.realtimeChannel = null;
  state.rtcPeer = null;
  if (channel) {
    channel.onclose = null;
    try { channel.close(); } catch (_) { /* already closed */ }
  }
  if (peer) {
    try { peer.close(); } catch (_) { /* already closed */ }
  }
}

async function connectRealtimeChannel() {
  if (
    state.config?.network?.webrtc_enabled !== true
    || state.websocket?.readyState !== WebSocket.OPEN
    || state.rtcPeer
  ) return;
  const peer = new RTCPeerConnection({ iceServers: [] });
  const channel = peer.createDataChannel('teleop', {
    ordered: false,
    maxRetransmits: 0
  });
  state.rtcPeer = peer;
  state.realtimeChannel = channel;
  channel.bufferedAmountLowThreshold = 0;
  channel.onopen = () => {
    setStatus('VR real-time UDP channel connected');
  };
  channel.onclose = () => {
    if (state.realtimeChannel === channel) state.realtimeChannel = null;
    if (state.rtcPeer === peer) state.rtcPeer = null;
    try { peer.close(); } catch (_) { /* already closed */ }
    scheduleRealtimeReconnect();
  };
  channel.onerror = () => {
    setStatus('UDP real-time channel unavailable; using WebSocket fallback');
  };
  peer.onconnectionstatechange = () => {
    if (['failed', 'closed'].includes(peer.connectionState)) {
      if (state.realtimeChannel === channel) state.realtimeChannel = null;
      if (state.rtcPeer === peer) state.rtcPeer = null;
      scheduleRealtimeReconnect();
    }
  };
  try {
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await waitForIceGatheringComplete(peer);
    const path = state.config?.network?.realtime_offer_path || '/api/realtime/offer';
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sdp: peer.localDescription.sdp,
        type: peer.localDescription.type
      })
    });
    if (!response.ok) throw new Error(await response.text());
    const answer = await response.json();
    await peer.setRemoteDescription(answer);
  } catch (error) {
    if (state.realtimeChannel === channel) state.realtimeChannel = null;
    if (state.rtcPeer === peer) state.rtcPeer = null;
    try { peer.close(); } catch (_) { /* already closed */ }
    console.warn('WebRTC setup failed; using WebSocket fallback:', error);
    scheduleRealtimeReconnect();
  }
}

function sendRealtimePacket(payload) {
  payload.sequence = ++state.realtimeSequence;
  if (!payload.timestamp) payload.timestamp = Date.now();
  const encoded = JSON.stringify(payload);
  const channel = state.realtimeChannel;
  if (channel?.readyState === 'open') {
    // Never build a local queue of stale motion.  A future frame is more
    // valuable than any pose that could not be sent immediately.
    if (channel.bufferedAmount <= 16384) channel.send(encoded);
    return;
  }
  if (state.websocket?.readyState === WebSocket.OPEN) {
    state.websocket.send(encoded);
  }
}

function sendReliablePacket(payload) {
  if (!payload.timestamp) payload.timestamp = Date.now();
  if (state.websocket?.readyState === WebSocket.OPEN) {
    state.websocket.send(JSON.stringify(payload));
  }
}

function attachRigToCamera() {
  const rig = document.getElementById('cameraRig');
  const cameraEl = document.querySelector('a-scene')?.camera?.el;
  if (!rig || !cameraEl) return;
  if (rig.parentNode !== cameraEl) cameraEl.appendChild(rig);
  rig.setAttribute('position', '0 -0.08 -1.85');
}

function createTextHud() {
  const cameraEl = document.querySelector('a-scene')?.camera?.el;
  if (!cameraEl || state.headText) return;

  const text = document.createElement('a-text');
  text.setAttribute('value', 'Head: waiting...');
  text.setAttribute('position', '0 -0.38 -0.75');
  text.setAttribute('align', 'center');
  text.setAttribute('color', '#EAF4FF');
  text.setAttribute('width', '0.9');
  text.setAttribute('baseline', 'center');
  text.setAttribute('anchor', 'center');
  cameraEl.appendChild(text);
  state.headText = text;
}

function createBrandHud() {
  const rig = document.getElementById('cameraRig');
  if (!rig || state.brandHud) return;

  const group = document.createElement('a-entity');
  group.setAttribute('id', 'hgm-brand-hud');
  group.setAttribute('position', '0.72 0.88 0');

  const mark = document.createElement('a-text');
  mark.setAttribute('value', 'AUTOLIFE 306 V4');
  mark.setAttribute('align', 'center');
  mark.setAttribute('anchor', 'center');
  mark.setAttribute('baseline', 'center');
  mark.setAttribute('color', '#05070A');
  mark.setAttribute('width', '1.45');
  mark.setAttribute('position', '0 0 0');
  group.appendChild(mark);

  const subtitle = document.createElement('a-text');
  subtitle.setAttribute('value', 'Dual Arm Teleoperation');
  subtitle.setAttribute('align', 'center');
  subtitle.setAttribute('anchor', 'center');
  subtitle.setAttribute('baseline', 'center');
  subtitle.setAttribute('color', '#05070A');
  subtitle.setAttribute('width', '0.86');
  subtitle.setAttribute('position', '0 -0.12 0');
  group.appendChild(subtitle);

  rig.appendChild(group);
  state.brandHud = group;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForIceGatheringComplete(pc) {
  if (pc.iceGatheringState === 'complete') return;

  await new Promise(resolve => {
    const checkState = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', checkState);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', checkState);
    window.setTimeout(() => {
      pc.removeEventListener('icegatheringstatechange', checkState);
      resolve();
    }, 2000);
  });
}

function cameraId(cameraConfig) {
  return cameraConfig.id || cameraConfig.image_key || 'front';
}

function panelLayout(cameraIdValue) {
  if (cameraIdValue === 'front') return { x: 0, y: 0.16, width: 1.5, height: 1.12 };
  if (cameraIdValue === 'left_wrist') return { x: -1.1, y: -0.08, width: 0.76, height: 0.57 };
  if (cameraIdValue === 'right_wrist') return { x: 1.1, y: -0.08, width: 0.76, height: 0.57 };
  return { x: 0, y: -0.08, width: 0.76, height: 0.57 };
}

function createHiddenImageState(cameraConfig) {
  const id = cameraId(cameraConfig);
  const img = document.createElement('img');
  img.id = `${id}-camera-stream`;
  img.alt = `${id} ZMQ camera stream`;
  img.crossOrigin = 'anonymous';
  img.decoding = 'async';
  img.style.cssText = 'position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:0.01;pointer-events:none;';

  const canvas = document.createElement('canvas');
  canvas.id = `${id}-camera-canvas`;
  canvas.width = cameraConfig.width || 640;
  canvas.height = cameraConfig.height || 480;
  canvas.style.cssText = img.style.cssText;

  const video = document.createElement('video');
  video.id = `${id}-camera-video`;
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  video.setAttribute('autoplay', '');
  video.setAttribute('muted', '');
  video.setAttribute('playsinline', '');
  video.setAttribute('webkit-playsinline', '');
  video.style.cssText = img.style.cssText;

  document.body.appendChild(img);
  document.body.appendChild(canvas);
  document.body.appendChild(video);

  state.image.states.set(id, {
    id,
    config: cameraConfig,
    img,
    video,
    canvas,
    ctx: canvas.getContext('2d'),
    texture: null,
    textureKind: null,
    material: null,
    transportMode: 'initializing',
    peerConnection: null,
    loading: false,
    lastFrameRequestMs: -Infinity,
    frameSeq: 0,
    drawnFrameSeq: 0,
    frameIntervalMs: 1000 / Math.max(1, Math.min(20, Number(cameraConfig.fps || 15)))
  });
}

function disposeCameraTexture(cameraState) {
  if (!cameraState?.texture) return;
  try {
    cameraState.texture.dispose();
  } catch (error) {
    console.warn('Failed to dispose camera texture:', error);
  }
  cameraState.texture = null;
  cameraState.textureKind = null;
  cameraState.material = null;
}

function setPanelTexture(id, texture, kind) {
  const cameraState = state.image.states.get(id);
  const panel = state.image.panels.get(id);
  if (!cameraState || !panel) return;

  const mesh = panel.screen.getObject3D('mesh');
  if (!mesh) return;

  if (cameraState.texture !== texture || cameraState.textureKind !== kind || !cameraState.material) {
    cameraState.material = new THREE.MeshBasicMaterial({
      map: texture,
      side: THREE.DoubleSide,
      toneMapped: false,
      transparent: state.image.opacity < 1,
      opacity: state.image.opacity,
      depthWrite: state.image.opacity >= 1
    });
    mesh.material = cameraState.material;
    mesh.material.needsUpdate = true;
    cameraState.texture = texture;
    cameraState.textureKind = kind;
  }
}

function applyCanvasTexture(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState) return;

  if (!cameraState.texture || cameraState.textureKind !== 'canvas') {
    disposeCameraTexture(cameraState);
    cameraState.texture = new THREE.CanvasTexture(cameraState.canvas);
    cameraState.texture.minFilter = THREE.LinearFilter;
    cameraState.texture.magFilter = THREE.LinearFilter;
    cameraState.texture.generateMipmaps = false;
    if ('colorSpace' in cameraState.texture && THREE.SRGBColorSpace) {
      cameraState.texture.colorSpace = THREE.SRGBColorSpace;
    }
    cameraState.textureKind = 'canvas';
    cameraState.material = null;
  }

  setPanelTexture(id, cameraState.texture, 'canvas');
  cameraState.texture.needsUpdate = true;
}

function applyVideoTexture(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState || !cameraState.video.srcObject) return;

  if (!cameraState.texture || cameraState.textureKind !== 'video') {
    disposeCameraTexture(cameraState);
    cameraState.texture = new THREE.VideoTexture(cameraState.video);
    cameraState.texture.minFilter = THREE.LinearFilter;
    cameraState.texture.magFilter = THREE.LinearFilter;
    cameraState.texture.generateMipmaps = false;
    if ('colorSpace' in cameraState.texture && THREE.SRGBColorSpace) {
      cameraState.texture.colorSpace = THREE.SRGBColorSpace;
    }
    cameraState.textureKind = 'video';
    cameraState.material = null;
  }

  setPanelTexture(id, cameraState.texture, 'video');
}

function drawCameraFrame(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState || !cameraState.ctx) return;

  if (cameraState.transportMode === 'webrtc') {
    applyVideoTexture(id);
    return;
  }

  if (!cameraState.img.complete || !cameraState.img.naturalWidth || !cameraState.img.naturalHeight) return;
  if (cameraState.canvas.width !== cameraState.img.naturalWidth || cameraState.canvas.height !== cameraState.img.naturalHeight) {
    cameraState.canvas.width = cameraState.img.naturalWidth;
    cameraState.canvas.height = cameraState.img.naturalHeight;
  }

  try {
    cameraState.ctx.drawImage(cameraState.img, 0, 0, cameraState.canvas.width, cameraState.canvas.height);
    applyCanvasTexture(id);
  } catch (error) {
    console.warn(`Could not draw MJPEG frame for ${id}:`, error);
  }
}

function createCameraPanel(cameraConfig) {
  const rig = document.getElementById('cameraRig');
  if (!rig) return;

  const id = cameraId(cameraConfig);
  const layout = panelLayout(id);
  const panel = document.createElement('a-entity');
  panel.setAttribute('id', `${id}-camera-panel`);
  panel.setAttribute('position', `${layout.x} ${layout.y} 0`);

  const border = document.createElement('a-plane');
  border.setAttribute('width', (layout.width + 0.04).toFixed(2));
  border.setAttribute('height', (layout.height + 0.04).toFixed(2));
  border.setAttribute('color', '#202836');
  border.setAttribute('position', '0 0 -0.01');
  border.setAttribute('material', 'shader: flat; side: double');
  panel.appendChild(border);

  const screen = document.createElement('a-plane');
  screen.setAttribute('width', layout.width);
  screen.setAttribute('height', layout.height);
  screen.setAttribute('color', '#111111');
  screen.setAttribute('material', 'shader: flat; side: double');
  panel.appendChild(screen);

  const label = document.createElement('a-text');
  label.setAttribute('value', cameraConfig.name || id);
  label.setAttribute('align', 'center');
  label.setAttribute('color', '#FFFFFF');
  label.setAttribute('width', '1.6');
  label.setAttribute('position', `0 ${(layout.height / 2 + 0.1).toFixed(2)} 0`);
  panel.appendChild(label);

  const meta = document.createElement('a-text');
  meta.setAttribute('value', 'waiting...');
  meta.setAttribute('align', 'center');
  meta.setAttribute('color', '#B8C7D9');
  meta.setAttribute('width', '1.2');
  meta.setAttribute('position', `0 ${(-layout.height / 2 - 0.1).toFixed(2)} 0`);
  panel.appendChild(meta);

  rig.appendChild(panel);
  state.image.panels.set(id, { panel, screen, label, meta });
}

function updateCameraPanel(id) {
  const cameraState = state.image.states.get(id);
  const panel = state.image.panels.get(id);
  if (!cameraState || !panel || !cameraState.status) return;

  panel.label.setAttribute('value', cameraState.status.name || id);
  const text = [
    `${cameraState.status.width || 0}x${cameraState.status.height || 0}`,
    `${cameraState.status.fps || 0} FPS`,
    `transport: ${cameraState.transportMode}`,
    `frame: ${cameraState.status.frame_version ?? 0}`,
    cameraState.status.image_key || id
  ];
  if (cameraState.status.last_error) text.push(`error: ${cameraState.status.last_error}`);
  panel.meta.setAttribute('value', text.join(' | '));
}

async function refreshCameraStatus(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState) return;
  try {
    cameraState.status = await fetch(`/api/camera/status?camera=${encodeURIComponent(id)}`).then(response => response.json());
    updateCameraPanel(id);
  } catch (error) {
    console.warn(`Could not load ZMQ camera status for ${id}:`, error);
  }
}

function startMjpegStream(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState) return;

  cameraState.transportMode = 'mjpeg-stream';
  if (cameraState.peerConnection) {
    try {
      cameraState.peerConnection.close();
    } catch (error) {
      console.warn(`Failed to close ${id} WebRTC peer:`, error);
    }
    cameraState.peerConnection = null;
  }
  cameraState.video.pause();
  cameraState.video.srcObject = null;

  const loadStream = () => {
    cameraState.img.src = `/api/camera/stream.mjpg?camera=${encodeURIComponent(id)}&ts=${Date.now()}`;
  };
  cameraState.img.onload = () => updateCameraPanel(id);
  cameraState.img.onerror = () => {
    cameraState.transportMode = 'mjpeg-retrying';
    updateCameraPanel(id);
    window.setTimeout(loadStream, 500);
  };
  loadStream();
  updateCameraPanel(id);
}

async function startCameraFeed(id) {
  const cameraState = state.image.states.get(id);
  if (!cameraState) return;
  cameraState.img.setAttribute('referrerpolicy', 'no-referrer');
  cameraState.img.decoding = 'async';

  try {
    const statusResponse = await fetch('/api/webrtc/status');
    const status = await statusResponse.json();
    if (!status.available) {
      startMjpegStream(id);
      return;
    }

    cameraState.transportMode = 'webrtc-connecting';
    updateCameraPanel(id);
    cameraState.peerConnection = new RTCPeerConnection({ iceServers: [] });
    cameraState.peerConnection.addTransceiver('video', { direction: 'recvonly' });
    cameraState.peerConnection.ontrack = async event => {
      cameraState.video.srcObject = event.streams[0];
      cameraState.video.onloadedmetadata = () => {
        applyVideoTexture(id);
        updateCameraPanel(id);
      };
      try {
        await cameraState.video.play();
      } catch (playError) {
        console.warn(`Autoplay retry required for ${id}:`, playError);
      }
      cameraState.transportMode = 'webrtc';
      applyVideoTexture(id);
      updateCameraPanel(id);
    };
    cameraState.peerConnection.onconnectionstatechange = () => {
      if (['failed', 'disconnected', 'closed'].includes(cameraState.peerConnection.connectionState)) {
        startMjpegStream(id);
      }
    };

    const offer = await cameraState.peerConnection.createOffer();
    await cameraState.peerConnection.setLocalDescription(offer);
    await waitForIceGatheringComplete(cameraState.peerConnection);

    const response = await fetch('/api/webrtc/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        camera_id: id,
        sdp: cameraState.peerConnection.localDescription.sdp,
        type: cameraState.peerConnection.localDescription.type
      })
    });
    const answer = await response.json();
    if (!response.ok || !answer.sdp) throw new Error(answer.error || `WebRTC offer failed for ${id}`);
    await cameraState.peerConnection.setRemoteDescription(answer);
  } catch (error) {
    console.warn(`WebRTC startup failed for ${id}, using MJPEG fallback:`, error);
    startMjpegStream(id);
  }
}

function createAxisPart(type, attributes) {
  const el = document.createElement(type);
  Object.entries(attributes).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
}

function addControllerAxes(handEl) {
  if (!handEl || handEl.querySelector('[data-telegrip-axis="true"]')) return;

  const axisLength = 0.11;
  const radius = 0.004;
  const tipHeight = 0.025;
  const tipRadius = 0.011;
  const axes = [
    {
      color: '#ff3b30',
      cylinder: { position: `${axisLength / 2} 0 0`, rotation: '0 0 90' },
      cone: { position: `${axisLength} 0 0`, rotation: '0 0 90' }
    },
    {
      color: '#34c759',
      cylinder: { position: `0 ${axisLength / 2} 0`, rotation: '0 0 0' },
      cone: { position: `0 ${axisLength} 0`, rotation: '0 0 0' }
    },
    {
      color: '#0a84ff',
      cylinder: { position: `0 0 ${axisLength / 2}`, rotation: '90 0 0' },
      cone: { position: `0 0 ${axisLength}`, rotation: '90 0 0' }
    }
  ];

  axes.forEach(axis => {
    const cylinder = createAxisPart('a-cylinder', {
      'data-telegrip-axis': 'true',
      height: axisLength,
      radius,
      color: axis.color,
      position: axis.cylinder.position,
      rotation: axis.cylinder.rotation
    });
    const cone = createAxisPart('a-cone', {
      'data-telegrip-axis': 'true',
      height: tipHeight,
      'radius-bottom': tipRadius,
      'radius-top': 0,
      color: axis.color,
      position: axis.cone.position,
      rotation: axis.cone.rotation
    });
    handEl.appendChild(cylinder);
    handEl.appendChild(cone);
  });
}

function renderCameraFrames() {
  if (!state.image.enabled) return;

  state.image.states.forEach((_, id) => drawCameraFrame(id));

  window.requestAnimationFrame(renderCameraFrames);
}

async function setupOptionalCameraPanels() {
  if (!state.image.enabled) {
    setStatus('ZMQ image display disabled; VR data ready');
    return;
  }

  const cameras = state.image.cameras.filter(camera => camera.enabled !== false);
  cameras.forEach(camera => {
    createHiddenImageState(camera);
    createCameraPanel(camera);
  });

  await Promise.all(cameras.map(async camera => {
    const id = cameraId(camera);
    await refreshCameraStatus(id);
    await startCameraFeed(id);
  }));

  if (!state.image.renderStarted) {
    state.image.renderStarted = true;
    window.requestAnimationFrame(renderCameraFrames);
  }

  window.setInterval(() => {
    cameras.forEach(camera => refreshCameraStatus(cameraId(camera)));
  }, 5000);
}

function controllerData(handEl, hand, buttons, webxrPose = null) {
  const payload = {
    hand,
    position: null,
    rotation: null,
    quaternion: null,
    gripActive: buttons.grip,
    trigger: Math.min(1, Math.max(0, Number(buttons.trigger) || 0)),
    thumbstick: buttons.thumbstick || { x: 0, y: 0, pressed: 0 }
  };

  if (hand === 'left') {
    payload.xButton = buttons.x ? 1 : 0;
    payload.yButton = buttons.y ? 1 : 0;
  } else {
    payload.aButton = buttons.a ? 1 : 0;
    payload.bButton = buttons.b ? 1 : 0;
  }

  if (webxrPose) {
    payload.position = webxrPose.position;
    payload.rotation = webxrPose.rotation;
    payload.quaternion = webxrPose.quaternion;
    return payload;
  }

  if (!handEl?.object3D?.visible) return payload;

  const pos = handEl.object3D.position;
  const rot = handEl.object3D.rotation;
  const quat = handEl.object3D.quaternion;

  payload.position = { x: pos.x, y: pos.y, z: pos.z };
  payload.rotation = {
    x: THREE.MathUtils.radToDeg(rot.x),
    y: THREE.MathUtils.radToDeg(rot.y),
    z: THREE.MathUtils.radToDeg(rot.z)
  };
  payload.quaternion = { x: quat.x, y: quat.y, z: quat.z, w: quat.w };
  return payload;
}

AFRAME.registerComponent('telegrip-vr-bridge', {
  init: function () {
    this.leftHand = document.getElementById('leftHand');
    this.rightHand = document.getElementById('rightHand');
    this.leftButtons = { grip: false, trigger: 0, x: false, y: false, thumbstick: { x: 0, y: 0, pressed: 0 } };
    this.rightButtons = { grip: false, trigger: 0, a: false, b: false, thumbstick: { x: 0, y: 0, pressed: 0 } };

    this.el.renderer.xr.addEventListener('sessionstart', () => {
      state.xrSession = this.el.renderer.xr.getSession();
      setStatus('VR 已进入，正在检测手柄实时姿态。');
      attachRigToCamera();
      // Keep the operator's VR view clean by default.  The diagnostic HUD can
      // still be enabled explicitly in the fetched VR configuration.
      if (state.config?.vr?.show_hud === true) {
        createBrandHud();
        createTextHud();
      }
    });
    this.el.renderer.xr.addEventListener('sessionend', () => {
      state.xrSession = null;
      if (state.teleop.enableAfterFreshVr) {
        cancelDeferredHardwareEnable('VR 会话已退出，真机未使能。');
        renderHardwareControl();
      }
    });

    this.bindControllerEvents(this.leftHand, 'left', this.leftButtons);
    this.bindControllerEvents(this.rightHand, 'right', this.rightButtons);

    if (state.config?.vr?.controller_axes?.enabled !== false) {
      addControllerAxes(this.leftHand);
      addControllerAxes(this.rightHand);
    }
  },

  sendButtonEvent: function (hand, button, pressed) {
    sendReliablePacket({
      type: pressed ? 'button_press' : 'button_release',
      hand,
      button,
      pressed,
      timestamp: Date.now()
    });
  },

  sendReleaseEvent: function (hand, releaseKey) {
    sendReliablePacket({
      hand,
      [releaseKey]: true
    });
  },

  bindControllerEvents: function (handEl, hand, buttons) {
    if (!handEl) return;
    handEl.addEventListener('gripdown', () => { buttons.grip = true; });
    handEl.addEventListener('gripup', () => {
      buttons.grip = false;
      this.sendReleaseEvent(hand, 'gripReleased');
    });
    handEl.addEventListener('triggerdown', () => { buttons.trigger = 1; });
    handEl.addEventListener('triggerup', () => {
      buttons.trigger = 0;
      this.sendReleaseEvent(hand, 'triggerReleased');
    });

    if (hand === 'left') {
      handEl.addEventListener('xbuttondown', () => {
        buttons.x = true;
        this.sendButtonEvent('left', 'X', true);
      });
      handEl.addEventListener('xbuttonup', () => {
        buttons.x = false;
        this.sendButtonEvent('left', 'X', false);
      });
      handEl.addEventListener('ybuttondown', () => {
        buttons.y = true;
        this.sendButtonEvent('left', 'Y', true);
      });
      handEl.addEventListener('ybuttonup', () => {
        buttons.y = false;
        this.sendButtonEvent('left', 'Y', false);
      });
    } else {
      handEl.addEventListener('abuttondown', () => {
        buttons.a = true;
        this.sendButtonEvent('right', 'A', true);
      });
      handEl.addEventListener('abuttonup', () => {
        buttons.a = false;
        this.sendButtonEvent('right', 'A', false);
      });
      handEl.addEventListener('bbuttondown', () => {
        buttons.b = true;
        this.sendButtonEvent('right', 'B', true);
      });
      handEl.addEventListener('bbuttonup', () => {
        buttons.b = false;
        this.sendButtonEvent('right', 'B', false);
      });
    }
  },

  updateThumbsticks: function () {
    if (!state.xrSession) return;
    const deadzone = 0.05;

    for (const source of state.xrSession.inputSources) {
      if (!source.gamepad || !source.handedness) continue;
      const axes = source.gamepad.axes || [];
      const buttons = source.gamepad.buttons || [];
      const x = Math.abs(axes[2] || 0) < deadzone ? 0 : axes[2] || 0;
      const y = Math.abs(axes[3] || 0) < deadzone ? 0 : axes[3] || 0;
      if (source.handedness === 'left') {
        this.leftButtons.trigger = Number.isFinite(buttons[0]?.value)
          ? buttons[0].value
          : (buttons[0]?.pressed ? 1 : 0);
        this.leftButtons.grip = Boolean(buttons[1]?.pressed);
        this.leftButtons.x = Boolean(buttons[4]?.pressed);
        this.leftButtons.y = Boolean(buttons[5]?.pressed);
        this.leftButtons.thumbstick = { x, y, pressed: buttons[2]?.pressed ? 1 : 0 };
      }
      if (source.handedness === 'right') {
        this.rightButtons.trigger = Number.isFinite(buttons[0]?.value)
          ? buttons[0].value
          : (buttons[0]?.pressed ? 1 : 0);
        this.rightButtons.grip = Boolean(buttons[1]?.pressed);
        this.rightButtons.a = Boolean(buttons[4]?.pressed);
        this.rightButtons.b = Boolean(buttons[5]?.pressed);
        this.rightButtons.thumbstick = { x, y, pressed: buttons[3]?.pressed ? 1 : 0 };
      }
    }
  },

  inputSourceForHand: function (hand) {
    if (!state.xrSession) return null;
    return Array.from(state.xrSession.inputSources || []).find(source => source.handedness === hand) || null;
  },

  webxrPoseForHand: function (hand) {
    const source = this.inputSourceForHand(hand);
    const frame = this.el.renderer.xr.getFrame?.();
    const referenceSpace = this.el.renderer.xr.getReferenceSpace?.();
    if (!source || !frame || !referenceSpace) return null;
    // Some WebXR runtimes keep gripSpace registered while temporarily
    // returning no grip pose, even though targetRaySpace remains valid.
    // Try both spaces before declaring the controller untracked.
    const poseSpaces = [source.gripSpace, source.targetRaySpace].filter(Boolean);
    let pose = null;
    for (const poseSpace of poseSpaces) {
      pose = frame.getPose(poseSpace, referenceSpace);
      if (pose) break;
    }
    if (!pose) return null;
    const position = pose.transform.position;
    const quaternion = pose.transform.orientation;
    const euler = new THREE.Euler().setFromQuaternion(
      new THREE.Quaternion(quaternion.x, quaternion.y, quaternion.z, quaternion.w),
      'XYZ'
    );
    return {
      position: { x: position.x, y: position.y, z: position.z },
      rotation: {
        x: THREE.MathUtils.radToDeg(euler.x),
        y: THREE.MathUtils.radToDeg(euler.y),
        z: THREE.MathUtils.radToDeg(euler.z)
      },
      quaternion: { x: quaternion.x, y: quaternion.y, z: quaternion.z, w: quaternion.w }
    };
  },

  controllerForHand: function (hand, handEl, buttons) {
    return controllerData(handEl, hand, buttons, this.webxrPoseForHand(hand));
  },

  inputSourceSummary: function () {
    const sources = Array.from(state.xrSession?.inputSources || []);
    if (!sources.length) return 'none';
    return sources.map(source => {
      const profile = source.profiles?.[0] || (source.hand ? 'hand-tracking' : 'unknown');
      return `${source.handedness || 'unknown'}:${profile}`;
    }).join(', ');
  },

  headData: function (leftController, rightController) {
    const headObject = this.el.camera?.el?.object3D;
    if (!headObject) return { position: null, rotation: null, quaternion: null };

    const pos = headObject.position;
    const rot = headObject.rotation;
    const quat = headObject.quaternion;
    const head = {
      position: { x: pos.x, y: pos.y, z: pos.z },
      rotation: {
        x: THREE.MathUtils.radToDeg(rot.x),
        y: THREE.MathUtils.radToDeg(rot.y),
        z: THREE.MathUtils.radToDeg(rot.z)
      },
      quaternion: { x: quat.x, y: quat.y, z: quat.z, w: quat.w }
    };

    if (state.headText) {
      const serverReady = state.websocket?.readyState === WebSocket.OPEN;
      const leftReady = Boolean(leftController?.position);
      const rightReady = Boolean(rightController?.position);
      state.headText.setAttribute(
        'value',
        `AUTOLIFE 306 V4 / PAGE V3\n` +
        `Server: ${serverReady ? 'CONNECTED' : 'DISCONNECTED'} | ` +
        `Left: ${leftReady ? 'READY' : 'MISSING'} | Right: ${rightReady ? 'READY' : 'MISSING'}\n` +
        `Inputs: ${this.inputSourceSummary()}\n` +
        `Head Pos: ${head.position.x.toFixed(2)} ${head.position.y.toFixed(2)} ${head.position.z.toFixed(2)}\n` +
        `Grip L:${this.leftButtons.grip ? 'ON' : 'OFF'} R:${this.rightButtons.grip ? 'ON' : 'OFF'} ` +
        `Trigger L:${this.leftButtons.trigger.toFixed(2)} R:${this.rightButtons.trigger.toFixed(2)}\n` +
        `RESET ARMS: hold LEFT X + RIGHT A for 1.0s`
      );
    }
    return head;
  },

  tick: function () {
    if (!state.xrSession) return;

    this.updateThumbsticks();
    const leftController = this.controllerForHand('left', this.leftHand, this.leftButtons);
    const rightController = this.controllerForHand('right', this.rightHand, this.rightButtons);
    const packet = {
      packetType: 'pose',
      timestamp: Date.now(),
      head: this.headData(leftController, rightController),
      leftController,
      rightController
    };
    sendRealtimePacket(packet);
  }
});

async function enterVr() {
  const scene = document.querySelector('a-scene');
  if (!scene) return;

  const button = document.getElementById('startVrButton');
  if (button) {
    button.disabled = true;
    button.textContent = 'Starting...';
  }

  try {
    await scene.enterVR(true);
  } catch (error) {
    console.error('Failed to enter VR:', error);
    setStatus(`Failed to enter VR: ${error.message}`);
    if (state.teleop.enableAfterFreshVr) {
      cancelDeferredHardwareEnable(`进入 VR 失败：${error.message}`);
      renderHardwareControl();
    }
    if (button) {
      button.disabled = false;
      button.textContent = 'Start VR';
    }
  }
}

async function init() {
  setServerUrl();
  setStatus('Loading configuration...');
  await loadConfig();

  const scene = document.querySelector('a-scene');
  if (scene.hasLoaded) {
    scene.setAttribute('telegrip-vr-bridge', '');
  } else {
    scene.addEventListener('loaded', () => scene.setAttribute('telegrip-vr-bridge', ''), { once: true });
  }

  connectWebSocket();
  setupHardwareControl();
  attachRigToCamera();
  createBrandHud();
  await setupOptionalCameraPanels();

  const startButton = document.getElementById('startVrButton');
  if (startButton) startButton.addEventListener('click', enterVr);

  scene.addEventListener('enter-vr', () => {
    const launchPanel = document.getElementById('launchPanel');
    if (launchPanel) launchPanel.style.display = 'none';
  });
  scene.addEventListener('exit-vr', () => {
    const launchPanel = document.getElementById('launchPanel');
    const startButton = document.getElementById('startVrButton');
    if (launchPanel) launchPanel.style.display = 'flex';
    if (startButton) {
      startButton.disabled = false;
      startButton.textContent = 'Start VR';
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  init().catch(error => {
    console.error('Telegrip startup failed:', error);
    setStatus(`Startup failed: ${error.message}`);
  });
});
