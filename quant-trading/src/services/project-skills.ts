import fs from "node:fs/promises";
import path from "node:path";

export type ProjectSkill = {
  id: string; name: string; displayName: string; description: string;
  category: string; tools: string[]; body: string;
};

function frontmatter(raw: string): { metadata: Record<string, string>; body: string } {
  const text = raw.replace(/^\uFEFF/, "");
  const match = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(text);
  if (!match) return { metadata: {}, body: text.trim() };
  const metadata: Record<string, string> = {};
  const lines = match[1]!.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const pair = /^([\w-]+):[ \t]*(.*)$/.exec(lines[i]!);
    if (!pair) continue;
    const key = pair[1]!, value = pair[2]!.trim();
    if (/^[>|][+-]?$/.test(value)) {
      const parts: string[] = [];
      while (i + 1 < lines.length && (/^\s/.test(lines[i + 1]!) || !lines[i + 1]!.trim())) parts.push(lines[++i]!.trim());
      metadata[key] = parts.join(value.startsWith(">") ? " " : "\n").trim();
    } else {
      metadata[key] = value.startsWith('"') ? JSON.parse(value) as string : value.replace(/^'|'$/g, "").replace(/''/g, "'");
    }
  }
  return { metadata, body: text.slice(match[0].length).trim() };
}

export async function loadProjectSkills(skillsDir: string): Promise<ProjectSkill[]> {
  let dirs: string[];
  try {
    dirs = (await fs.readdir(skillsDir, { withFileTypes: true })).filter((d) => d.isDirectory()).map((d) => d.name);
  } catch { return []; }
  const skills: ProjectSkill[] = [];
  for (const id of dirs.sort()) {
    try {
      const raw = await fs.readFile(path.join(skillsDir, id, "SKILL.md"), "utf8");
      const parsed = frontmatter(raw);
      // Project metadata and allowed-tools use JSON-compatible inline YAML.
      // Legacy nested plugin metadata is ignored rather than parsed as prose.
      const meta = parsed.metadata.metadata?.startsWith("{") ? JSON.parse(parsed.metadata.metadata) as Record<string, unknown> : {};
      const tools: unknown = parsed.metadata["allowed-tools"] ? JSON.parse(parsed.metadata["allowed-tools"]) : [];
      if (!Array.isArray(tools) || tools.some((t) => typeof t !== "string")) continue;
      skills.push({ id, name: parsed.metadata.name || id, displayName: String(meta.display_name || parsed.metadata.name || id),
        description: parsed.metadata.description || "", category: String(meta.category || "通用"), tools, body: parsed.body });
    } catch { /* A directory without SKILL.md is not a skill. */ }
  }
  return skills;
}
