import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { getAdminMainScript } from "./main-script.js";

class Element {
  value = ""; textContent = ""; title = ""; label = ""; disabled = false;
  dataset: Record<string, string> = {}; style: Record<string, string> = {};
  children: Element[] = [];
  set innerHTML(_html: string) { this.children = []; }
  appendChild(child: Element) { this.children.push(child); return child; }
  addEventListener() {}
}

function chatUi(api: ReturnType<typeof vi.fn>) {
  const nodes = Object.fromEntries(["chat-skill", "chat-skill-description", "chat-status", "chat-input"].map((id) => [id, new Element()]));
  const label = new Element(); label.dataset.skillIds = '["company","holdings"]';
  const context: Record<string, any> = { api, document: {
    getElementById: (id: string) => nodes[id], createElement: () => new Element(), querySelectorAll: () => [label],
  } };
  const script = getAdminMainScript().split("/* ══════ UNIFIED AI CHAT ══════ */")[1]!.split("/* ── Auto-refresh")[0]!;
  runInNewContext(script, context);
  return { context, nodes, label };
}

describe("Skill selection in the emitted browser code", () => {
  it("groups Chinese names, shows descriptions, disables unavailable Skills and labels primary/support roles", async () => {
    const api = vi.fn().mockResolvedValue({ configured: true, skills: [
      { id: "company", name: "company", displayName: "个股研究", category: "股票研究", description: "研究公司", available: true },
      { id: "holdings", name: "holdings", displayName: "持仓分析", category: "组合管理", description: "分析持仓", available: true },
      { id: "missing", name: "missing", displayName: "未接入", category: "股票研究", description: "所需数据缺失", available: false },
    ] });
    const { context, nodes, label } = chatUi(api);
    await context.chatLoadSkills();
    expect(nodes["chat-skill"].children.map((el) => el.label)).toEqual(["", "股票研究", "组合管理"]);
    expect(nodes["chat-skill"].children[1].children[0].textContent).toBe("个股研究 · @company");
    expect(nodes["chat-skill"].children[1].children[1].disabled).toBe(true);
    expect(nodes["chat-skill-description"].textContent).toContain("已加载 3 个 Skill");
    expect(label.textContent).toBe("AI 助手 · 主 Skill：个股研究 · 辅助：持仓分析");
    nodes["chat-skill"].value = "company"; context.chatDescribeSkill();
    expect(nodes["chat-skill-description"].textContent).toBe("研究公司");
    await context.chatLoadSkills();
    expect(nodes["chat-skill"].value).toBe("company");
  });

  it("makes loading failures visible and renders general mode without a Skill", async () => {
    const { context, nodes, label } = chatUi(vi.fn().mockRejectedValue(new Error("unavailable")));
    await context.chatLoadSkills();
    expect(nodes["chat-skill-description"].textContent).toContain("加载失败");
    context.chatSetSkillLabel(label, []);
    expect(label.textContent).toBe("AI 助手 · 通用模式");
  });
});
