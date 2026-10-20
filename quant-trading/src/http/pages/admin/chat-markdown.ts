import { readFileSync } from "node:fs";

// Vendored markdown-it 15.0.3 (MIT) keeps the admin page self-contained.
const markdownIt = readFileSync(new URL("./vendor/markdown-it.min.js", import.meta.url), "utf8");

export function getChatMarkdownScript(): string {
  return markdownIt + `
;window.qtChatMarkdown = window.markdownit({ html: false, breaks: true, linkify: false });
window.qtChatMarkdown.renderer.rules.table_open = function(){ return '<div class="chat-table-scroll"><table>'; };
window.qtChatMarkdown.renderer.rules.table_close = function(){ return '</table></div>'; };
window.qtChatMarkdown.renderer.rules.link_open = function(tokens, index, options, env, renderer){
  tokens[index].attrSet('target', '_blank');
  tokens[index].attrSet('rel', 'noopener noreferrer');
  return renderer.renderToken(tokens, index, options);
};
`;
}
