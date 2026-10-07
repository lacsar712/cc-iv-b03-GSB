import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import App from "./src/App.vue";

function makeState() {
  return {
    logs: [],
    stripes: [
      { string_code: "阵列A-串03", last_scan_id: 1, verdict: "合格", fill_factor: 0.78, processed_at: "t" },
      { string_code: "阵列B-串11", last_scan_id: 2, verdict: "衰减", fill_factor: 0.61, processed_at: "t" },
      { string_code: "阵列甲-逆变01", last_scan_id: null, verdict: null, fill_factor: null, processed_at: null },
    ],
    renameCalls: [],
    logCalls: [],
  };
}

function installFetch(state, role = "writer") {
  const fetchStub = vi.fn(async (url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    const body = opts.body ? JSON.parse(opts.body) : null;
    const json = (obj, status = 200) => ({
      status,
      ok: status >= 200 && status < 300,
      json: async () => obj,
    });
    if (url.endsWith("/api/auth/login")) {
      return json({ access_token: "tok", username: body.username, role });
    }
    if (url.endsWith("/api/stripes")) return json(state.stripes);
    if (url.endsWith("/api/logs") && method === "GET") return json(state.logs);
    if (url.endsWith("/api/logs") && method === "POST") {
      state.logCalls.push(body);
      const row = {
        id: 100,
        string_code: body.string_code,
        voc_v: body.voc_v,
        isc_a: body.isc_a,
        fill_factor: body.fill_factor,
        status: "pending",
        verdict: null,
        reason: null,
        created_by: "scanner",
        created_at: "t",
        processed_at: null,
      };
      state.logs = [row, ...state.logs];
      return json(row, 201);
    }
    if (url.endsWith("/api/inverters/rename") && method === "POST") {
      state.renameCalls.push(body);
      state.stripes.forEach((s) => {
        if (s.string_code === body.string_code) s.string_code = body.new_code;
      });
      return json({ ok: true, string_code: body.new_code }, 200);
    }
    return json({ detail: "unexpected " + method + " " + url }, 404);
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

function stripeFor(wrapper, code) {
  const wraps = wrapper.findAll(".stripe");
  return wraps.find((w) => w.find(".stripe-code").text() === code);
}

async function login(wrapper) {
  await wrapper.get("button").trigger("click"); // 登录 button (first)
  await flushPromises();
}

describe("逆变器条带专页", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("颜色只由接口 verdict 决定：合格绿/衰减红/未办结灰，且不乐观涂色", async () => {
    const state = makeState();
    installFetch(state);
    const wrapper = mount(App, { attachTo: document.body });
    await login(wrapper);

    // 进入条带页
    await wrapper.findAll(".topbar .tabs button")[1].trigger("click");
    await flushPromises();

    const green = stripeFor(wrapper, "阵列A-串03");
    const red = stripeFor(wrapper, "阵列B-串11");
    const gray = stripeFor(wrapper, "阵列甲-逆变01");
    expect(green.classes()).toContain("stripe-ok");
    expect(red.classes()).toContain("stripe-bad");
    expect(gray.classes()).toContain("stripe-none");
    expect(gray.find(".stripe-state").text()).toBe("未办结");

    // 提交甲 FF=0.80，但此刻 /api/stripes 仍回报 null -> 必须保持灰，不许本地先涂绿
    await wrapper.findAll(".topbar .tabs button")[0].trigger("click"); // 回到记录页
    await flushPromises();
    const codeInput = wrapper.get('input[placeholder="例如 阵列C-串05"]');
    const nums = wrapper.findAll('input[type="number"]');
    await codeInput.setValue("阵列甲-逆变01");
    await nums[2].setValue(0.8); // 填充因子
    await wrapper.get("section button:not(.secondary)").trigger("click"); // 提交扫描
    await flushPromises();
    expect(state.logCalls).toHaveLength(1);

    await wrapper.findAll(".topbar .tabs button")[1].trigger("click"); // 条带页
    await flushPromises();
    const stillGray = stripeFor(wrapper, "阵列甲-逆变01");
    expect(stillGray.classes()).toContain("stripe-none"); // 接口没说合格 -> 不绿

    // 工人办结后接口回报合格，点整条刷新 -> 才变绿
    state.stripes = state.stripes.map((s) =>
      s.string_code === "阵列甲-逆变01"
        ? { ...s, last_scan_id: 100, verdict: "合格", fill_factor: 0.8 }
        : s
    );
    await wrapper.get(".stripe-toolbar button").trigger("click"); // 刷新整条条带
    await flushPromises();
    const nowGreen = stripeFor(wrapper, "阵列甲-逆变01");
    expect(nowGreen.classes()).toContain("stripe-ok");
    expect(nowGreen.find(".stripe-state").text()).toBe("合格");
    wrapper.unmount();
  });

  it("同一台两笔都办结时跟随接口给出的（编号更大）最近一笔：由绿转红", async () => {
    const state = makeState();
    // 甲当前最近办结是更大 id 的衰减
    state.stripes[2] = {
      string_code: "阵列甲-逆变01", last_scan_id: 4, verdict: "衰减", fill_factor: 0.55, processed_at: "t",
    };
    installFetch(state);
    const wrapper = mount(App, { attachTo: document.body });
    await login(wrapper);
    await wrapper.findAll(".topbar .tabs button")[1].trigger("click");
    await flushPromises();
    const stripe = stripeFor(wrapper, "阵列甲-逆变01");
    expect(stripe.classes()).toContain("stripe-bad");
    expect(stripe.text()).toContain("#4");
    expect(stripe.text()).not.toContain("#3");
    wrapper.unmount();
  });

  it("扫描员可改编号；旁观者无改名按钮、不能提交入队", async () => {
    // writer
    const state = makeState();
    const fetchStub = installFetch(state, "writer");
    let wrapper = mount(App, { attachTo: document.body });
    await login(wrapper);
    await wrapper.findAll(".topbar .tabs button")[1].trigger("click");
    await flushPromises();

    const rows = wrapper.findAll(".inverter-row");
    const jiaRow = rows.find((r) => r.text().includes("阵列甲-逆变01"));
    expect(jiaRow.text()).toContain("改名");
    await jiaRow.get("button").trigger("click"); // 改名
    await jiaRow.get("input.rename-input").setValue("阵列甲-改名99");
    await jiaRow.findAll("button")[0].trigger("click"); // 保存
    await flushPromises();
    expect(state.renameCalls).toEqual([
      { string_code: "阵列甲-逆变01", new_code: "阵列甲-改名99" },
    ]);
    expect(fetchStub).toHaveBeenCalledWith(
      expect.stringContaining("/api/inverters/rename"),
      expect.objectContaining({ method: "POST" })
    );
    expect(stripeFor(wrapper, "阵列甲-改名99")).toBeTruthy();
    wrapper.unmount();

    // watcher
    vi.clearAllTimers();
    const state2 = makeState();
    installFetch(state2, "reader");
    wrapper = mount(App, { attachTo: document.body });
    await login(wrapper);
    // 记录页不应有提交表单
    expect(wrapper.text()).not.toContain("提交扫描");
    expect(wrapper.find('input[placeholder*="阵列C"]').exists()).toBe(false);
    await wrapper.findAll(".topbar .tabs button")[1].trigger("click");
    await flushPromises();
    const rows2 = wrapper.findAll(".inverter-row");
    expect(rows2.some((r) => r.text().includes("改名"))).toBe(false);
    expect(wrapper.find(".rename-input").exists()).toBe(false);
    wrapper.unmount();
  });
});
