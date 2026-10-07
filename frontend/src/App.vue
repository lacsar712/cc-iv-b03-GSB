<template>
  <div>
    <header class="topbar">
      <span class="brand">光伏组串IV扫描台</span>
      <nav>
        <button :class="{ active: view === 'strips' }" @click="goView('strips')">逆变器条带</button>
        <button :class="{ active: view === 'scans' }" @click="goView('scans')">扫描记录</button>
      </nav>
    </header>

    <main>
      <div v-if="!session">
        <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。旁观账号 watcher / watch123456 只读。</p>
        <section>
          <label>用户名</label><input v-model="loginUser" autocomplete="off" />
          <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
          <button :disabled="loading" @click="login">登录</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
      </div>

      <!-- ============ 逆变器条带专页 ============ -->
      <div v-else-if="view === 'strips'">
        <section class="toolbar">
          <span class="sub-inline">已登录：{{ session.username }}（{{ isWriter ? "扫描员，可维护编号" : "旁观，只读" }}）</span>
          <span style="flex:1"></span>
          <button class="secondary" :disabled="loading" @click="refreshAll">刷新条带</button>
          <button class="secondary" @click="logout">退出</button>
        </section>

        <div class="columns">
          <!-- 左：维护逆变器编号 -->
          <section class="left">
            <h2>逆变器编号</h2>
            <ul class="invlist">
              <li v-for="inv in inverters" :key="inv.id"
                  :class="{ selected: inv.id === selectedId }"
                  @click="selectedId = inv.id">
                <template v-if="renamingId === inv.id">
                  <input v-model="renameCode" @keyup.enter="saveRename(inv)" @keyup.esc="renamingId = null" />
                  <button class="mini" @click.stop="saveRename(inv)">存</button>
                  <button class="mini secondary" @click.stop="renamingId = null">消</button>
                </template>
                <template v-else>
                  <span class="dot" :class="stripeClass(inv)"></span>
                  <span class="code">{{ inv.code }}</span>
                  <button v-if="isWriter" class="mini secondary" @click.stop="startRename(inv)">改名</button>
                </template>
              </li>
              <li v-if="!inverters.length" class="empty">暂无逆变器</li>
            </ul>
            <div v-if="isWriter" class="addrow">
              <input v-model="newCode" placeholder="新逆变器编号，如 阵列C-串05" @keyup.enter="addInverter" />
              <button :disabled="loading" @click="addInverter">新增编号</button>
            </div>
            <p v-if="invError" class="err">{{ invError }}</p>
          </section>

          <!-- 右：整条，颜色只认接口返回 -->
          <section class="right">
            <template v-if="selected">
              <h2>{{ selected.code }} · 最近一次扫描合格率</h2>
              <div class="stripe" :class="stripeClass(selected)">
                <template v-if="selected.latest_verdict">
                  <div class="stripe-word">{{ selected.latest_verdict }}</div>
                  <div class="stripe-meta">
                    填充因子 {{ selected.latest_fill_factor }}
                    ｜扫描单 #{{ selected.latest_scan_id }}
                    ｜{{ selected.latest_reason }}
                  </div>
                </template>
                <template v-else>
                  <div class="stripe-word">从未办结</div>
                  <div class="stripe-meta">暂无已办结扫描，条带保持灰色，不得当作合格</div>
                </template>
              </div>
            </template>
            <div v-else class="stripe emptyhint">← 请先在左侧选择一台逆变器</div>
          </section>
        </div>
      </div>

      <!-- ============ 扫描记录页（原功能） ============ -->
      <div v-else>
        <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读，不能入队" }}）</p>
        <section>
          <button class="secondary" @click="refreshLogs">刷新列表</button>
          <button class="secondary" @click="logout">退出</button>
        </section>
        <section v-if="isWriter">
          <label>组串编号</label>
          <input v-model="stringCode" placeholder="例如 阵列C-串05" list="known-codes" />
          <datalist id="known-codes">
            <option v-for="inv in inverters" :key="inv.id" :value="inv.code"></option>
          </datalist>
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>逆变器</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ invCode(row.inverter_id) }}</td>
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
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";

const session = ref(null);
const view = ref("strips");
const logs = ref([]);
const inverters = ref([]);
const selectedId = ref(null);
const newCode = ref("");
const renamingId = ref(null);
const renameCode = ref("");
const invError = ref("");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);
let timer;

const isWriter = computed(() => session.value?.role === "writer");
const selected = computed(
  () => inverters.value.find((i) => i.id === selectedId.value) || null
);

// 条带颜色的唯一依据：接口 /api/inverters 返回的最近一次办结结论。
// 前端绝不本地涂色——接口没回报新颜色前，画面停在旧颜色/灰色。
function stripeClass(inv) {
  if (inv && inv.latest_verdict === "合格") return "green";
  if (inv && inv.latest_verdict === "衰减") return "red";
  return "gray";
}
function invCode(id) {
  return inverters.value.find((i) => i.id === id)?.code || "—";
}

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}

async function refreshInverters() {
  if (!session.value) return;
  const res = await fetch("/api/inverters", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (!res.ok) return;
  const data = await res.json();
  inverters.value = data;
  if (selectedId.value == null && data.length) selectedId.value = data[0].id;
  if (selectedId.value != null && !data.some((i) => i.id === selectedId.value)) {
    selectedId.value = data.length ? data[0].id : null;
  }
}
async function refreshLogs() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshAll() {
  loading.value = true;
  try {
    // 先条带（权威颜色），再日志
    await refreshInverters();
    await refreshLogs();
  } finally {
    loading.value = false;
  }
}
function goView(v) {
  view.value = v;
  if (v === "strips") refreshInverters();
  else refreshLogs();
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
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  inverters.value = [];
  selectedId.value = null;
  localStorage.removeItem("pv_session");
}
async function tick() {
  // 自动刷新同样只复读接口数据，不做任何本地推断涂色
  await refreshInverters();
  await refreshLogs();
}

async function addInverter() {
  invError.value = "";
  const code = newCode.value.trim();
  if (!code) return;
  loading.value = true;
  try {
    const res = await fetch("/api/inverters", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    if (!res.ok) { invError.value = data.detail || "新增失败"; return; }
    newCode.value = "";
    await refreshInverters();
    selectedId.value = data.id;
  } catch { invError.value = "新增时网络异常"; }
  finally { loading.value = false; }
}
function startRename(inv) {
  renamingId.value = inv.id;
  renameCode.value = inv.code;
}
async function saveRename(inv) {
  invError.value = "";
  const code = renameCode.value.trim();
  if (!code) { invError.value = "编号不能为空"; return; }
  loading.value = true;
  try {
    const res = await fetch(`/api/inverters/${inv.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ code }),
    });
    const data = await res.json();
    if (!res.ok) { invError.value = data.detail || "改名失败"; return; }
    renamingId.value = null;
    await refreshAll();
  } catch { invError.value = "改名时网络异常"; }
  finally { loading.value = false; }
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
    // 只向接口要颜色，不本地先涂色
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
.topbar { display: flex; align-items: center; gap: 1rem; background: #022c22; border-bottom: 1px solid #166534; padding: 0.7rem 1.5rem; }
.brand { font-weight: 700; color: #86efac; }
.topbar nav button { background: transparent; color: #a7f3d0; border: 1px solid transparent; }
.topbar nav button.active { background: #14532d; border-color: #166534; color: #ecfdf5; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
.sub-inline { color: #a7f3d0; font-size: 0.9rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.toolbar { display: flex; align-items: center; gap: 0.5rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.mini { padding: 0.15rem 0.5rem; font-size: 0.78rem; margin: 0 0 0 0.3rem; }
.err { color: #fecaca; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }

.columns { display: grid; grid-template-columns: 320px 1fr; gap: 1rem; align-items: start; }
.left h2, .right h2 { margin: 0 0 0.75rem; font-size: 1rem; color: #86efac; }
.invlist { list-style: none; margin: 0 0 0.75rem; padding: 0; }
.invlist li { display: flex; align-items: center; padding: 0.5rem 0.6rem; border-radius: 6px; cursor: pointer; border: 1px solid transparent; }
.invlist li:hover { background: #0f4426; }
.invlist li.selected { background: #166534; border-color: #4ade80; }
.invlist li.empty { color: #a7f3d0; cursor: default; }
.invlist li input { margin: 0; width: 100%; }
.code { flex: 1; }
.dot { width: 0.7rem; height: 0.7rem; border-radius: 50%; margin-right: 0.55rem; flex: none; }
.dot.green, .stripe.green { background: #16a34a; }
.dot.red, .stripe.red { background: #b91c1c; }
.dot.gray, .stripe.gray { background: #4b5563; }
.addrow { display: flex; gap: 0.4rem; }
.addrow input { margin: 0; }
.addrow button { margin: 0; white-space: nowrap; }

.stripe { min-height: 200px; border-radius: 10px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.7rem; color: #fff; }
.stripe-word { font-size: 2.4rem; font-weight: 800; letter-spacing: 0.2em; }
.stripe-meta { font-size: 0.9rem; opacity: 0.9; }
.stripe.emptyhint { color: #d1d5db; font-size: 1rem; background: #1f2937; border: 1px dashed #4b5563; }
</style>
