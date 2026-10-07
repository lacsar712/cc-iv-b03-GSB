<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <div class="brand">已登录：{{ session.username }}（{{ isWriter ? "扫描员" : "旁观" }}）</div>
        <div class="tabs">
          <button :class="{ active: tab === 'logs' }" @click="switchTab('logs')">扫描记录</button>
          <button :class="{ active: tab === 'stripes' }" @click="switchTab('stripes')">逆变器条带</button>
        </div>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- ============ 扫描记录 ============ -->
      <div v-if="tab === 'logs'">
        <section>
          <button class="secondary" @click="refreshAll">刷新列表</button>
          <span v-if="!isWriter" class="hint">旁观身份只读：不能提交、不能入队。</span>
        </section>
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- ============ 逆变器条带 ============ -->
      <div v-else class="stripe-page">
        <section class="stripe-toolbar">
          <button class="secondary" :disabled="loading" @click="refreshStripes">刷新整条条带</button>
          <span class="hint">颜色只取每台逆变器最近一次办结（编号最大那笔）；从未办结保持灰色空白，不用条带假装合格。</span>
          <p v-if="stripeError" class="err">{{ stripeError }}</p>
        </section>

        <div class="stripe-grid">
          <!-- 左侧：维护逆变器编号 -->
          <section class="inverter-list">
            <h2>逆变器编号</h2>
            <p v-if="!isWriter" class="hint">旁观身份：编号只读，不能改名。</p>
            <p v-if="stripes.length === 0" class="hint">暂无逆变器。</p>
            <div v-for="s in stripes" :key="s.string_code" class="inverter-row">
              <template v-if="editingCode === s.string_code">
                <input v-model="editValue" class="rename-input" @keyup.enter="saveRename(s)" @keyup.esc="cancelRename" />
                <button class="mini" :disabled="loading" @click="saveRename(s)">保存</button>
                <button class="mini secondary" @click="cancelRename">取消</button>
              </template>
              <template v-else>
                <span class="code" :title="'最近办结 #' + (s.last_scan_id ?? '—')">{{ s.string_code }}</span>
                <button v-if="isWriter" class="mini secondary" @click="startRename(s)">改名</button>
              </template>
            </div>
          </section>

          <!-- 右侧：整条条带 -->
          <section class="stripe-board">
            <h2>合格率条带</h2>
            <p v-if="stripes.length === 0" class="hint">暂无数据。</p>
            <div
              v-for="s in stripes"
              :key="'stripe-' + s.string_code"
              class="stripe"
              :class="stripeClass(s)"
            >
              <span class="stripe-code">{{ s.string_code }}</span>
              <span class="stripe-state">{{ stripeText(s) }}</span>
              <span v-if="s.last_scan_id != null" class="stripe-meta">最近办结 #{{ s.last_scan_id }} · FF {{ s.fill_factor }}</span>
            </div>
          </section>
        </div>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const stripes = ref([]);
const tab = ref("logs");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const stripeError = ref("");
const loading = ref(false);
const editingCode = ref("");
const editValue = ref("");
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}

// 条带颜色严格由接口 verdict 决定：合格绿 / 衰减红 / 无办结灰空。
// pending 或没有 done 记录时 verdict 为 null，前端绝不乐观涂色。
function stripeClass(s) {
  if (s.verdict === "合格") return "stripe-ok";
  if (s.verdict === "衰减") return "stripe-bad";
  return "stripe-none";
}
function stripeText(s) {
  if (s.verdict === "合格") return "合格";
  if (s.verdict === "衰减") return "衰减";
  return "未办结";
}

async function fetchLogs() {
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function fetchStripes() {
  const res = await fetch("/api/stripes", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) {
    // 整包替换：颜色始终以接口最新返回为准，避免“画面绿了但接口仍旧”。
    stripes.value = await res.json();
    stripeError.value = "";
  } else {
    stripeError.value = "条带刷新失败";
  }
}
function refreshActive() {
  if (!session.value) return;
  if (tab.value === "stripes") return fetchStripes();
  return fetchLogs();
}
async function refreshAll() {
  if (!session.value) return;
  await Promise.all([fetchLogs(), fetchStripes()]);
}
async function refreshStripes() {
  loading.value = true;
  try { await fetchStripes(); }
  finally { loading.value = false; }
}
function switchTab(t) {
  tab.value = t;
  refreshActive();
}

async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshActive, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  stripes.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    // 不乐观涂色：等工人办结后由 /api/stripes 返回真实颜色。
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

function startRename(s) {
  editingCode.value = s.string_code;
  editValue.value = s.string_code;
  stripeError.value = "";
}
function cancelRename() {
  editingCode.value = "";
  editValue.value = "";
}
async function saveRename(s) {
  const newCode = editValue.value.trim();
  if (!newCode) { stripeError.value = "新编号不能为空"; return; }
  if (newCode === s.string_code) { cancelRename(); return; }
  loading.value = true;
  stripeError.value = "";
  try {
    const res = await fetch("/api/inverters/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ string_code: s.string_code, new_code: newCode }),
    });
    const data = await res.json();
    if (!res.ok) { stripeError.value = data.detail || "改名失败"; return; }
    cancelRename();
    await refreshAll();
  } catch { stripeError.value = "改名时网络异常"; }
  finally { loading.value = false; }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshActive, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1040px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
h2 { margin: 0 0 0.75rem; font-size: 1rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.active { background: #15803d; outline: 2px solid #86efac; }
.mini { padding: 0.25rem 0.6rem; font-size: 0.8rem; margin-right: 0.25rem; }
.err { color: #fecaca; }
.hint { color: #a7f3d0; font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }

.topbar { display: flex; align-items: center; gap: 1rem; background: #052e16; border: 1px solid #166534; border-radius: 8px; padding: 0.6rem 1rem; margin-bottom: 1rem; }
.topbar .brand { color: #a7f3d0; font-size: 0.9rem; }
.topbar .tabs { display: flex; gap: 0.4rem; flex: 1; }

.stripe-toolbar { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.stripe-grid { display: grid; grid-template-columns: 320px 1fr; gap: 1rem; align-items: start; }
.inverter-list .inverter-row { display: flex; align-items: center; gap: 0.4rem; padding: 0.45rem 0; border-bottom: 1px solid #166534; }
.inverter-list .code { flex: 1; word-break: break-all; }
.rename-input { width: auto; flex: 1; margin-bottom: 0; }

.stripe-board .stripe { display: flex; align-items: center; gap: 0.75rem; height: 46px; padding: 0 1rem; border-radius: 8px; margin-bottom: 0.6rem; font-weight: 600; color: #ecfdf5; }
.stripe-code { min-width: 150px; }
.stripe-state { padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.82rem; background: rgba(0,0,0,0.28); }
.stripe-meta { margin-left: auto; font-size: 0.8rem; font-weight: 400; opacity: 0.85; }
.stripe-ok { background: linear-gradient(90deg, #16a34a, #22c55e); }
.stripe-bad { background: linear-gradient(90deg, #b91c1c, #ef4444); }
.stripe-none { background: #334155; color: #cbd5e1; border: 1px dashed #64748b; }
</style>
