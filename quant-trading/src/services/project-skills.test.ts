import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadProjectSkills } from "./project-skills.js";
import { CHAT_TOOL_POLICIES, createChatTools } from "./chat-tool-policy.js";
import type { ChatTool } from "./ai-chat.js";

const dirs: string[] = [];
afterEach(async () => { for (const dir of dirs.splice(0)) await fs.rm(dir, { recursive: true, force: true }); });

// Instantiate real tool definitions, without executing their market/broker I/O.
export async function projectChatRegistry(): Promise<ChatTool[]> {
  const aliases = { correlation_analysis: "correlation" };
  return Promise.all(Object.keys(CHAT_TOOL_POLICIES).map(async (name) => {
    const file = aliases[name] ?? name.replaceAll("_", "-");
    const mod = await import(`../tools/${file}.ts`);
    const factory = Object.entries(mod).find(([key]) => /^create.*Tool$/.test(key))?.[1] as (...args: unknown[]) => ChatTool;
    if (!factory) throw new Error(`Missing factory: ${name}`);
    return factory({}, {}, {}, {}, {}, {});
  }));
}

describe("project Skill catalog", () => {
  it("parses folded descriptions without leaking block markers or nested plugin metadata", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "qt-skills-")); dirs.push(root);
    await fs.mkdir(path.join(root, "example"));
    await fs.writeFile(path.join(root, "example", "SKILL.md"), '\uFEFF---\nname: example\ndescription: >-\n  个股分析\n  和资金研究\nmetadata:\n  quant-trading:\n    always: true\n---\n\n调用真实数据。');
    const [skill] = await loadProjectSkills(root);
    expect(skill.description).toBe("个股分析 和资金研究");
    expect(skill.body).toBe("调用真实数据。");
  });

  it("loads every shipped Skill with usable tool contracts and real action examples", async () => {
    const registry = await projectChatRegistry();
    const tools = createChatTools(registry);
    expect(tools.size).toBe(Object.keys(CHAT_TOOL_POLICIES).length);
    for (const raw of registry) {
      const policy = CHAT_TOOL_POLICIES[raw.name]!;
      const action = (raw.parameters?.properties as Record<string, { enum?: string[] }> | undefined)?.action;
      if (action?.enum) expect(policy.actions?.every((value) => action.enum!.includes(value)), raw.name).toBe(true);
    }
    const skills = await loadProjectSkills(path.resolve(import.meta.dirname, "../../skills"));
    expect(skills).toHaveLength(16);
    for (const skill of skills) {
      expect(skill.displayName, skill.id).not.toBe(skill.id);
      expect(skill.description.length, skill.id).toBeLessThan(1024);
      expect(skill.tools.length, skill.id).toBeGreaterThan(0);
      for (const name of skill.tools) expect(tools.has(name), `${skill.id}: ${name}`).toBe(true);
      for (const match of skill.body.matchAll(/\b([a-z_]+)\(action=([a-z_]+)\)/g)) {
        const [, name, action] = match;
        expect(skill.tools, `${skill.id}: ${name}`).toContain(name);
        const schema = tools.get(name!)!.parameters!.properties as Record<string, { enum?: string[] }>;
        expect(schema.action.enum, `${skill.id}: ${name}(${action})`).toContain(action);
      }
    }
  });
});
