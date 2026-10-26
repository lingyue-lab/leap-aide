import { marked, Marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';
import 'prismjs/themes/prism-tomorrow.css';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';

// ── URL auto-link stash (stash → let marked handle rest → restore) ──

const STASH_PREFIX = '\x02';
const STASH_SUFFIX = '\x03';

function createStash(): { map: Map<string, string>; counter: number } {
  return { map: new Map(), counter: 0 };
}

function stashPut(stash: ReturnType<typeof createStash>, content: string): string {
  const key = `${STASH_PREFIX}${stash.counter++}${STASH_SUFFIX}`;
  stash.map.set(key, content);
  return key;
}

function restore(stash: ReturnType<typeof createStash>, html: string): string {
  if (stash.map.size === 0) return html;
  let result = html;
  stash.map.forEach((value, key) => {
    result = result.split(key).join(value);
  });
  return result;
}

// ── Math extension for marked (KaTeX) ──
// Registered as a marked inline extension so that marked's tokenizer respects
// its own priority rules — codespan (backticks) is matched before inline math,
// preventing $ inside `code` from being misinterpreted as LaTeX.

const mathExtension = {
  name: 'math' as const,
  level: 'inline' as const,
  start(src: string) {
    return src.match(/\$/)?.index;
  },
  tokenizer(src: string) {
    // Block math: $$...$$
    const blockMatch = src.match(/^\$\$([\s\S]+?)\$\$/);
    if (blockMatch) {
      return { type: 'math' as const, raw: blockMatch[0], text: blockMatch[1], display: true };
    }
    // Inline math: $...$ (skip empty or double-dollar)
    const inlineMatch = src.match(/^\$([^\s$][^$]*?[^\s$])\$/);
    if (inlineMatch) {
      return { type: 'math' as const, raw: inlineMatch[0], text: inlineMatch[1], display: false };
    }
  },
  renderer(token: { text: string; display: boolean }) {
    try {
      const html = katex.renderToString(token.text, { displayMode: token.display, strict: false });
      return token.display
        ? `<div class="katex-block">${html}</div>`
        : `<span class="katex-inline">${html}</span>`;
    } catch {
      const escaped = escapeHTML(token.text);
      const cls = token.display ? 'katex-block katex-error' : 'katex-inline katex-error';
      return token.display
        ? `<div class="${cls}">${escaped}</div>`
        : `<span class="${cls}">${escaped}</span>`;
    }
  },
};

// ── Configure marked ──

const MAX_HIGHLIGHT_LENGTH = 3000;

// Custom code block renderer with Prism syntax highlighting
// Note: skipHighlight is handled at the renderMarkdown level by swapping renderers
const renderer = new marked.Renderer();

renderer.code = function ({ text, lang }: { text: string; lang?: string }): string {
  const langAttr = lang || 'text';
  const trimmed = text.endsWith('\n') ? text.slice(0, -1) : text;
  let highlighted: string;
  if (trimmed.length > MAX_HIGHLIGHT_LENGTH) {
    highlighted = escapeHTML(trimmed);
  } else {
    try {
      const grammar = Prism.languages[langAttr];
      highlighted = grammar
        ? Prism.highlight(trimmed, grammar, langAttr)
        : escapeHTML(trimmed);
    } catch {
      highlighted = escapeHTML(trimmed);
    }
  }
  return `<pre class="language-${langAttr}"><code class="language-${langAttr}">${highlighted}</code></pre>`;
};

// Fast renderer — skips syntax highlighting (used during streaming)
const fastRenderer = new marked.Renderer();
fastRenderer.code = function ({ text, lang }: { text: string; lang?: string }): string {
  const langAttr = lang || 'text';
  const trimmed = text.endsWith('\n') ? text.slice(0, -1) : text;
  return `<pre class="language-${langAttr}"><code class="language-${langAttr}">${escapeHTML(trimmed)}</code></pre>`;
};

// ── Helpers ──

function escapeHTML(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function sanitizeHTML(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/javascript:/gi, '');
}


// ── Nested-fence repair (issue #28) ───────────────────────────────────────────
//
// CommonMark has no nested fences: while a block is open, ANY line with the
// same fence char, at least the opener's length and no info string closes it.
// A ```markdown wrapper whose body contains a ```bash block is therefore closed
// by the inner block's bare ``` — the rest of the document leaks out as prose
// and the wrapper's real closing fence becomes a stray empty code block.
//
// Models produce exactly that shape when they quote a whole markdown document
// ("wrap the PR description in a ```markdown block"). repairNestedFenceDocument
// recognises the signature — the document ends inside an unterminated fence and
// the first block already contains a nested opener — and re-emits the wrapper
// with a fence longer than anything inside it, which renders as one block.

const FENCE_LINE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

interface FenceLine {
  char: '`' | '~';
  len: number;
  info: string;
}

/** Parse a line that is a fenced-code marker, following CommonMark's shape. */
function parseFenceLine(line: string): FenceLine | null {
  const m = FENCE_LINE_RE.exec(line);
  if (!m) return null;
  const marker = m[1];
  const info = m[2].trim();
  // Per CommonMark, a backtick fence's info string may not contain a backtick.
  if (marker[0] === '`' && info.includes('`')) return null;
  return { char: marker[0] as '`' | '~', len: marker.length, info };
}

/**
 * Rewrite a fenced markdown document that nests a fenced block inside a
 * shorter (or equal) wrapper fence, so the whole document stays in one code
 * block. Returns the input unchanged for every other shape.
 */
export function repairNestedFenceDocument(raw: string): string {
  const lines = raw.split('\n');

  let first = 0;
  while (first < lines.length && lines[first].trim() === '') first++;
  if (first >= lines.length) return raw;
  const outer = parseFenceLine(lines[first]);
  // Only a language-tagged wrapper is treated as "a document in a code block".
  if (!outer || outer.info === '') return raw;

  // Walk the document the way marked will, to learn where it ends up.
  let open: FenceLine | null = null;
  let firstBlockClosed = false;
  let firstBlockHasNestedOpener = false;
  let lastFence = -1;

  for (let i = first; i < lines.length; i++) {
    const fence = parseFenceLine(lines[i]);
    if (!fence) continue;
    lastFence = i;
    if (open === null) {
      open = fence;
      continue;
    }
    const closes = fence.char === open.char && fence.len >= open.len && fence.info === '';
    if (closes) {
      open = null;
      if (i !== first) firstBlockClosed = true;
    } else if (!firstBlockClosed && fence.info !== '') {
      firstBlockHasNestedOpener = true;
    }
  }

  // Signature of the bug: EOF inside a fence, after a first block that itself
  // contained a nested opener. Anything else is left untouched.
  if (open === null || !firstBlockClosed || !firstBlockHasNestedOpener) return raw;
  const tail = parseFenceLine(lines[lastFence]);
  if (!tail || tail.info !== '') return raw;

  const body = lines.slice(first + 1, lastFence);
  if (body.length === 0) return raw;
  // Keep the wrapper's indentation (a fence may sit inside a list item).
  const indent = /^[ \t]*/.exec(lines[first])![0];

  let longestInner = outer.len;
  for (const line of body) {
    const fence = parseFenceLine(line);
    if (fence) longestInner = Math.max(longestInner, fence.len);
  }
  const wrapper = outer.char.repeat(longestInner + 1);

  return [
    ...lines.slice(0, first),
    indent + wrapper + outer.info,
    ...body,
    indent + wrapper,
    ...lines.slice(lastFence + 1),
  ].join('\n');
}

// ── Main export ──

export function renderMarkdown(raw: string, skipHighlight: boolean = false): string {
  if (!raw) return '';

  const MAX_INPUT = 100_000;
  if (raw.length > MAX_INPUT) {
    raw = raw.slice(0, MAX_INPUT) + '\n\n*(content truncated for display)*';
  }

  // Strip YAML frontmatter
  const textToRender = raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');

  // Repair a nested-fence wrapper before parsing (issue #28).
  const repaired = repairNestedFenceDocument(textToRender);

  const stash = createStash();
  let html = repaired;

  // 1. Protect plain URLs before marked's GFM autolink swallows non-ASCII text.
  //    Stash them as inline code so marked leaves them untouched.
  html = html.replace(
    /(https?:\/\/[\w\-._~:/?#[\]@!$&'()*+,;=%]+)/g,
    (_, url) => stashPut(stash, `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`),
  );

  // 2. Parse markdown with marked (math extension handles $...$ / $$...$$)
  const mkd = new Marked();
  mkd.use({ extensions: [mathExtension] });
  html = mkd.parse(html, {
    renderer: skipHighlight ? fastRenderer : renderer,
    breaks: true,
    gfm: true,
    async: false,
  }) as string;

  // 3. Restore stashed content (auto-linked URLs)
  html = restore(stash, html);

  // 5. Sanitize
  html = sanitizeHTML(html);

  return html;
}

export function highlightCode(code: string, lang: string): string {
  try {
    const grammar = Prism.languages[lang] || Prism.languages.text;
    return Prism.highlight(code, grammar, lang);
  } catch {
    return escapeHTML(code);
  }
}

// ── Language resolution (file extension → Prism language) ──

const LANG_MAP: Record<string, string> = {
  md: 'markdown',
  markdown: 'markdown',
  txt: 'plain',
  text: 'plain',
  js: 'javascript',
  ts: 'typescript',
  jsx: 'javascript',
  tsx: 'typescript',
  py: 'python',
  json: 'json',
  yml: 'yaml',
  yaml: 'yaml',
  css: 'css',
  sh: 'bash',
  bash: 'bash',
};

export function resolveLanguage(fileName?: string, lang?: string): string {
  if (lang) return lang;
  if (!fileName) return 'markdown';
  const ext = fileName.split('.').pop()?.toLowerCase() || '';
  return LANG_MAP[ext] || 'plain';
}

export { Prism };
