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

// Use non-printable control chars as stash delimiters - safe from markdown processing
const STASH_PREFIX = '\x02';
const STASH_SUFFIX = '\x03';

interface StashState {
  map: Map<string, string>;
  counter: number;
}

function createStash(): StashState {
  return { map: new Map(), counter: 0 };
}

function stashPut(state: StashState, content: string): string {
  const key = `${STASH_PREFIX}${state.counter++}${STASH_SUFFIX}`;
  state.map.set(key, content);
  return key;
}

// Single pass restore - no loops, no recursion
function restore(state: StashState, html: string): string {
  if (state.map.size === 0) return html;
  let result = html;
  state.map.forEach((value, key) => {
    result = result.split(key).join(value);
  });
  return result;
}

function escapeHTML(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

// Maximum characters for syntax highlighting; longer code blocks render as plain text
const MAX_HIGHLIGHT_LENGTH = 3000;

function protectCodeBlocks(state: StashState, text: string, skipHighlight: boolean = false): string {
  // Guard: if an unclosed code fence exists on a large input, the regex will
  // backtrack catastrophically (ReDoS). Skip code-block processing in that case.
  const openFences = (text.match(/^(`{3,}|~{3,})\w*/gm) || []).length;
  const closeFences = (text.match(/^(`{3,}|~{3,})$/gm) || []).length;
  if (openFences > closeFences && text.length > 500) {
    return text;
  }

  return text.replace(/(`{3,}|~{3,})(\w*)\n([\s\S]*?)\1/g, (_, _ticks, lang, code) => {
    const langAttr = (lang || 'text').trim();
    // Remove trailing newline before closing fence
    const trimmedCode = code.endsWith('\n') ? code.slice(0, -1) : code;
    let highlighted: string;
    // Skip expensive syntax highlighting during streaming or for very long code blocks
    if (skipHighlight || trimmedCode.length > MAX_HIGHLIGHT_LENGTH) {
      highlighted = escapeHTML(trimmedCode);
    } else {
      try {
        const grammar = Prism.languages[langAttr];
        highlighted = grammar
          ? Prism.highlight(trimmedCode, grammar, langAttr)
          : escapeHTML(trimmedCode);
      } catch {
        highlighted = escapeHTML(trimmedCode);
      }
    }
    const html = `<pre class="language-${langAttr}"><code class="language-${langAttr}">${highlighted}</code></pre>`;
    return stashPut(state, html);
  });
}

function protectInlineCode(state: StashState, text: string): string {
  return text.replace(/`([^`\n]+)`/g, (_, code) => {
    return stashPut(state, `<code class="inline-code">${escapeHTML(code)}</code>`);
  });
}

function processMathBlocks(state: StashState, text: string): string {
  // Guard: only process $$ if they appear in balanced pairs (even count).
  // An odd number means an unclosed delimiter which causes catastrophic backtracking.
  const ddCount = (text.match(/\$\$/g) || []).length;
  let result = text;

  if (ddCount >= 2 && ddCount % 2 === 0) {
    result = result.replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => {
      try {
        return stashPut(state, `<div class="katex-block">${katex.renderToString(math, { displayMode: true })}</div>`);
      } catch {
        return stashPut(state, `<div class="katex-block katex-error">${escapeHTML(math)}</div>`);
      }
    });
  }

  // Inline math — only when at least two $ signs remain
  const dollarCount = (result.match(/\$/g) || []).length;
  if (dollarCount >= 2) {
    result = result.replace(/\$([^\s$][^$]*?[^\s$])\$/g, (_, math) => {
      try {
        return stashPut(state, `<span class="katex-inline">${katex.renderToString(math, { displayMode: false })}</span>`);
      } catch {
        return stashPut(state, `<span class="katex-inline katex-error">${escapeHTML(math)}</span>`);
      }
    });
  }

  return result;
}

// Inline formatting - applied to text segments only
function applyInlineFormatting(text: string): string {
  let r = text;
  // Images before links
  r = r.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" loading="lazy" />');
  // Links
  r = r.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // Bold+italic
  r = r.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  // Bold
  r = r.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  r = r.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italic
  r = r.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  r = r.replace(/_([^_\n]+)_/g, '<em>$1</em>');
  // Strikethrough
  r = r.replace(/~~(.+?)~~/g, '<del>$1</del>');
  return r;
}

const MAX_LINES = 2000;
const MAX_RECURSION_DEPTH = 5;

function processMarkdownLines(text: string, _depth: number = 0): string {
  if (_depth > MAX_RECURSION_DEPTH) {
    return escapeHTML(text);
  }

  const lines = text.split('\n');
  if (lines.length > MAX_LINES) {
    lines.length = MAX_LINES;
  }

  const out: string[] = [];
  let i = 0;
  let safetyCounter = 0;

  while (i < lines.length) {
    safetyCounter++;
    if (safetyCounter > MAX_LINES * 3) {
      break;
    }

    const prevI = i;
    const line = lines[i];

    // Stash token (code block placeholder) - output as-is
    if (line.startsWith(STASH_PREFIX)) {
      out.push(line);
      i++;
      continue;
    }

    // Headings
    const m = line.match(/^(#{1,6})\s+(.+)$/);
    if (m) {
      const level = m[1].length;
      out.push(`<h${level}>${applyInlineFormatting(m[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule
    if (/^(\*{3,}|-{3,}|_{3,})$/.test(line.trim())) {
      out.push('<hr />');
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith('> ')) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].startsWith('> ')) {
        quoteLines.push(lines[i].slice(2));
        i++;
      }
      out.push(`<blockquote>${processMarkdownLines(quoteLines.join('\n'), _depth + 1)}</blockquote>`);
      if (i === prevI) { i++; }
      continue;
    }

    // Unordered list
    if (/^[-*+] /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+] /.test(lines[i])) {
        items.push(`<li>${applyInlineFormatting(lines[i].replace(/^[-*+] /, ''))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      if (i === prevI) { i++; }
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${applyInlineFormatting(lines[i].replace(/^\d+\.\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      if (i === prevI) { i++; }
      continue;
    }

    // Empty line
    if (line.trim() === '') {
      out.push('');
      i++;
      continue;
    }

    // Paragraph: collect consecutive "plain" lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !lines[i].startsWith(STASH_PREFIX) &&
      !/^#{1,6}\s/.test(lines[i]) &&
      !/^[-*+] /.test(lines[i]) &&
      !/^\d+\.\s/.test(lines[i]) &&
      !lines[i].startsWith('> ') &&
      !/^(\*{3,}|-{3,}|_{3,})$/.test(lines[i].trim())
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      const inner = paraLines.map(l => applyInlineFormatting(l)).join('<br />');
      out.push(`<p>${inner}</p>`);
    } else {
      // Force advance to prevent infinite loop on unhandled lines
      out.push(escapeHTML(line));
      i++;
    }
  }

  return out.join('\n');
}

function sanitizeHTML(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+="[^"]*"/gi, '')
    .replace(/javascript:/gi, '');
}

// Escape HTML in text content (not inside code blocks which are already stashed)
function escapeHTMLInText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderMarkdown(raw: string, skipHighlight: boolean = false): string {
  if (!raw) return '';

  // Hard limit on input size to prevent regex from hanging on very large inputs
  const MAX_INPUT = 100_000;
  if (raw.length > MAX_INPUT) {
    raw = raw.slice(0, MAX_INPUT) + '\n\n*(content truncated for display)*';
  }

  const state = createStash();

  // 1. Protect fenced code blocks first (before any HTML escaping)
  let html = protectCodeBlocks(state, raw, skipHighlight);

  // 2. Protect inline code
  html = protectInlineCode(state, html);

  // 3. Protect math blocks
  html = processMathBlocks(state, html);

  // 4. Escape HTML in remaining text (not in stashed code blocks)
  html = escapeHTMLInText(html);

  // 5. Process markdown structure
  html = processMarkdownLines(html);

  // 6. Restore stashed content (code blocks, inline code, math)
  html = restore(state, html);

  // 7. Final sanitization (remove any script tags that might have slipped through)
  html = sanitizeHTML(html);

  return html;
}

export function highlightCode(code: string, lang: string): string {
  try {
    const grammar = Prism.languages[lang] || Prism.languages.text;
    return Prism.highlight(code, grammar, lang);
  } catch {
    return code;
  }
}

export { Prism };
