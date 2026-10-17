import fs from "node:fs/promises";
import path from "node:path";

export type ProjectSkill = { id: string; name: string; description: string; body: string };

function frontmatter(raw: string): { metadata: Record<string, string>; body: string } {
  const match = /^---\s*\r?\n([\s\S]*?)\r?\n---\s*\r?\n?/.exec(raw);
  if (!match) return { metadata: {}, body: raw.trim() };
  const metadata: Record<string, string> = {};
  let lastKey = "";
  for (const line of match[1]!.split(/\r?\n/)) {
    const pair = /^([\w-]+):\s*(.*)$/.exec(line);
    if (pair) {
      lastKey = pair[1]!;
      metadata[lastKey] = pair[2]!.replace(/^['"]|['"]$/g, "");
    } else if (lastKey && /^\s+/.test(line)) {
      metadata[lastKey] += ` ${line.trim().replace(/^['"]|['"]$/g, "")}`;
    }
  }
  return { metadata, body: raw.slice(match[0].length).trim() };
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
      skills.push({ id, name: parsed.metadata.name || id, description: parsed.metadata.description || "", body: parsed.body });
    } catch { /* A directory without SKILL.md is not a skill. */ }
  }
  return skills;
}
